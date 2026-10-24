"""Canonical bounded protocol v1. Browser milliseconds never become server nanoseconds."""
from __future__ import annotations

import base64
import binascii
from decimal import Decimal, localcontext
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

PROTOCOL_VERSION = 1
SAMPLE_RATE = 24_000
MAX_FRAME_SAMPLES = 4_800
MAX_MESSAGE_BYTES = 24_000
Identifier = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")]
Epoch = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
TerminalOutcome = Literal["success", "task_failure", "off_script", "permission_denied", "silence_timeout", "device_lost", "provider_error", "provider_timeout", "transport_loss", "service_restart", "abandoned", "queue_overrun", "calibration_invalid", "budget_halt", "expected_interruption", "recovery_unknown", "missing_terminal", "protocol_error", "unsupported_audio", "control_complete"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


DecimalText = Annotated[str, Field(pattern=r"^\d{1,18}(?:\.\d{1,18})?$", max_length=37)]
UsagePath = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){0,7}$", max_length=160)]
ProviderIdentityTag = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$", max_length=200)]


class ReservationTerms(Model):
    """Operator-supplied prices and bounds; this model supplies no tariff or entitlement."""
    schema_version: Literal[1] = 1
    mode: Literal["mock", "live", "local_control"]
    component: Literal["native", "stt", "tts", "canned"]
    model: ProviderIdentityTag
    currency: Literal["USD"]
    price_version: Annotated[str, Field(min_length=1, max_length=200)]
    pricing_reference: Annotated[str, Field(min_length=1, max_length=200)]
    capability_reference: Annotated[str, Field(min_length=1, max_length=200)]
    enforcement_reference: Annotated[str, Field(min_length=1, max_length=200)]
    bound_usd: DecimalText
    basis: Annotated[str, Field(min_length=1, max_length=500)]
    rate_unit: Literal["USD_per_unit"] = "USD_per_unit"
    rates: Annotated[dict[UsagePath, DecimalText], Field(min_length=1, max_length=32)]
    usage_bounds: Annotated[dict[UsagePath, DecimalText], Field(min_length=1, max_length=32)]
    usage_units: dict[UsagePath, Literal["tokens", "seconds", "requests", "samples"]]

    @model_validator(mode="after")
    def consistent_bound(self) -> ReservationTerms:
        if set(self.rates) != set(self.usage_bounds) or set(self.rates) != set(self.usage_units):
            raise ValueError("price_bound_dimensions_must_match")
        if any(other.startswith(path + ".") for path in self.rates for other in self.rates if other != path):
            raise ValueError("overlapping_usage_paths")
        with localcontext() as context:
            context.prec = 96
            maximum = Decimal(0)
            for path, rate in self.rates.items():
                units = Decimal(self.usage_bounds[path])
                if self.usage_units[path] != "seconds" and units != units.to_integral_value():
                    raise ValueError("discrete_usage_bound_required")
                if self.mode == "live" and units <= 0:
                    raise ValueError("positive_live_usage_bound_required")
                maximum += Decimal(rate) * units
            bound = Decimal(self.bound_usd)
            if maximum > bound or (self.mode == "live" and bound <= 0):
                raise ValueError("reservation_does_not_cover_declared_usage_bounds")
            if self.mode == "mock" and (bound != 0 or maximum != 0 or self.price_version != "mock-zero-v1"):
                raise ValueError("mock_requires_explicit_zero_provider_basis")
            if self.mode == "local_control" and (bound != 0 or maximum != 0 or self.component != "canned"
                    or self.price_version != "local-control-zero-v1" or set(self.rates) != {"provider_requests"}
                    or self.usage_bounds != {"provider_requests": "0"}):
                raise ValueError("local_control_requires_no_provider_component_only")
        return self


class ReturnedIdentity(Model):
    value: ProviderIdentityTag | None = None
    observation: Literal["observed", "unknown"] = "unknown"
    source: Literal["provider_session_updated", "unknown"] = "unknown"

    @model_validator(mode="after")
    def observed_value_required(self) -> ReturnedIdentity:
        if (self.observation == "observed") != (self.value is not None and self.source == "provider_session_updated"):
            raise ValueError("returned_identity_observation_mismatch")
        if self.observation == "unknown" and (self.value is not None or self.source != "unknown"):
            raise ValueError("unknown_returned_identity_has_no_value")
        return self


class BudgetOwnerEvidence(Model):
    scope: Literal["single_owner_run", "mock_local_run", "unknown"]
    owner_id: Identifier
    lease_reference: Annotated[str | None, Field(max_length=200)] = None
    lease_observation: Literal["operator_attestation", "unknown"]
    lease_verified: Literal["unknown"] = "unknown"


class LicensedControlEvidence(Model):
    kind: Literal['licensed_fixed_pcm']
    config_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    catalog_hash: Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
    asset_hashes: Annotated[dict[Identifier, Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]], Field(min_length=1, max_length=32)]
    authorization_reference: Identifier
    capability_reference: Identifier
    approval_observation: Literal['configuration_only_not_verified', 'external_verifier_accepted']
    asset_observation: Literal['local_wav_and_pcm_hashes_verified_at_open'] | None = None
    provider_generation_cost_usd: Literal['0']
    provider_cost_scope: Literal['no_provider_component_only']
    other_costs: Literal['unknown']
    task_applicability: Literal['not_applicable']
    spoken_correctness: Literal['unknown']
    model_stages: Literal['not_applicable']
    translation: Literal['identity_noop_no_duration']
    independent_verification: Literal['unknown'] = 'unknown'

    @model_validator(mode='after')
    def opened_observation(self) -> LicensedControlEvidence:
        if (self.approval_observation == 'external_verifier_accepted') != (self.asset_observation is not None):
            raise ValueError('licensed_control_open_observation_mismatch')
        return self


class AdmissionEvidence(Model):
    schema_version: Literal[1] = 1
    mode: Literal["mock", "live"]
    run_id: Identifier
    run_manifest_hash: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    manifest_hashes: dict[Literal["capabilities", "pricing", "authorization", "rights"], Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")]]
    fingerprint_observation: Literal["operator_configuration_fingerprint"] = "operator_configuration_fingerprint"
    requested_models: dict[Literal["native", "stt", "tts"], ProviderIdentityTag]
    expected_returned_models: dict[Literal["native", "stt", "tts"], ProviderIdentityTag]
    actually_returned_models: dict[Literal["native", "stt", "tts"], ReturnedIdentity]
    requested_voice: ProviderIdentityTag | None = None
    actually_returned_voice: ReturnedIdentity = Field(default_factory=ReturnedIdentity)
    budget_usd: DecimalText
    budget_owner: BudgetOwnerEvidence
    independent_verification: Literal["unknown"] = "unknown"
    licensed_control: LicensedControlEvidence | None = None


class CalibrationControl(Model):
    """Protected HTTP phase control; never a capture turn or deadline keepalive."""
    phase_id: Identifier
    action: Literal["begin", "end"]


class CalibrationReceipt(Model):
    phase_id: Identifier
    status: Literal['active', 'ended']
    started_ns: Annotated[int, Field(strict=True, ge=0)]
    deadline_ns: Annotated[int, Field(strict=True, ge=0)]
    ended_ns: Annotated[int | None, Field(strict=True, ge=0)]
    session_deadline_ns: Annotated[int, Field(strict=True, ge=0)]
    absolute_cap_ms: Literal[30000]
    phase_limit: Literal[2]
    clock_domain: Literal['server_monotonic_ns']
    duplicate: bool
    session_id: Identifier
    run_id: Identifier
    trial_id: Identifier
    boot_id: Identifier
    connection_epoch: Epoch
    response_epoch: Epoch
    task_deadlines_retained: Literal[True]
    captured_turn_created: Literal[False]
    probe_success: Literal['unknown']
    runtime_verified: Literal[False]


class LicensedControlAsset(Model):
    asset_id: Identifier
    path: Annotated[str, Field(min_length=1, max_length=500)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    pcm_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    format: Literal["PCM16LE"]
    channels: Literal[1]
    sample_rate: Literal[24000]
    frame_count: Annotated[int, Field(strict=True, ge=1, le=192000)]
    duration_seconds: DecimalText
    license_reference: Identifier
    output_rights_reference: Identifier
    approval_reference: Identifier

    @field_validator('channels', 'sample_rate', mode='before')
    @classmethod
    def integer_audio_format(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError('licensed_control_integer_audio_format_required')
        return value


class LicensedControlConfig(Model):
    schema_version: Literal[1] = 1
    run_id: Identifier
    budget_usd: DecimalText
    asset_root: Annotated[str, Field(min_length=1, max_length=1000)]
    catalog_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    assets: Annotated[list[LicensedControlAsset], Field(min_length=1, max_length=32)]
    state_assets: dict[Literal["information", "slots_offered", "awaiting_confirmation", "booked", "complete"], Identifier]
    authorization_reference: Identifier
    capability_reference: Identifier
    owner_lease_reference: Identifier
    expires_at_unix: Annotated[int, Field(strict=True, ge=1)]


class Hello(Model):
    type: Literal["hello"]
    protocol_version: Literal[1]
    mode: Literal["mock", "live"]
    arm: Literal["V", "C", "K"]
    nonce: Annotated[str, Field(min_length=16, max_length=128)]


class TurnFrame(Model):
    turn_id: Identifier
    connection_epoch: Epoch
    response_epoch: Epoch


class Audio(TurnFrame):
    type: Literal["audio"]
    sequence: Annotated[int, Field(strict=True, ge=0, le=100_000)]
    sample_rate: Literal[24000]
    pcm16: Annotated[str, Field(max_length=12_800)]

    @field_validator("pcm16")
    @classmethod
    def valid_pcm(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid PCM base64") from exc
        if not raw or len(raw) % 2 or len(raw) > MAX_FRAME_SAMPLES * 2:
            raise ValueError("PCM must contain 1..4800 signed mono PCM16 samples")
        return value


class Commit(TurnFrame):
    type: Literal["commit"]
    text: Annotated[str | None, Field(max_length=500)] = None


class Interrupt(Model):
    type: Literal["interrupt"]
    response_epoch: Epoch
    rendered_through_sample: Annotated[int, Field(strict=True, ge=0)]


class Rendered(Model):
    type: Literal["rendered"]
    response_id: Identifier
    rendered_through_sample: Annotated[int, Field(strict=True, ge=0)]


class Resume(Model):
    type: Literal["resume"]
    connection_epoch: Epoch
    rendered_ack: Rendered | None = None


class End(Model):
    type: Literal["end"]


class ClientStatus(Model):
    """A client observation, never authority to assert task/provider success."""
    type: Literal["client_status"]
    outcome: Literal["permission_denied", "device_lost", "silence_timeout", "abandoned", "unsupported_audio"]


class ClockProbe(Model):
    type: Literal["clock_probe"]
    probe_id: Identifier
    browser_sent_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]


FaultKind = Literal["late_pcm", "duplicate_pcm", "reordered_pcm", "lost_ack", "drop_before_commit", "drop_after_commit", "fresh_tool_duplicate", "service_restart", "provider_error", "missing_audio", "missing_transcript", "queue_overrun"]


class MockFaultControl(Model):
    control_id: Identifier
    kind: FaultKind


class MockFaultPolicy(Model):
    enabled: Literal[True]
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    authorization_reference: Annotated[str, Field(min_length=1, max_length=200)]
    max_controls: Annotated[int, Field(strict=True, ge=1, le=32)] = 32


class MockFaultReceipt(Model):
    schema_version: Literal[1] = 1
    session_id: Identifier
    run_id: Identifier
    trial_id: Identifier
    arm: Literal["V", "C", "K"]
    boot_id: Identifier
    control_id: Identifier
    kind: FaultKind
    seed: Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
    status: Literal["armed", "applied", "unresolved"]
    simulation: Literal[True]
    actual_process_restart: Literal[False]
    affected_boundary: Literal["mock_provider_pcm_queue", "mock_provider_pcm_stream", "mock_transcript_stream", "relay_render_ack", "mock_authority_effect", "mock_proposal_parser_authority", "owned_mock_session_lifecycle", "mock_provider_error_parser"]
    authorization_reference: Annotated[str, Field(min_length=1, max_length=200)]
    connection_epoch: Epoch
    response_epoch: Epoch
    server_timestamp_ns: Annotated[int, Field(strict=True, ge=0)]
    clock_domain: Literal["server_monotonic_ns"]
    request_count: Annotated[int, Field(strict=True, ge=1)]
    injected_count: Annotated[int, Field(strict=True, ge=0, le=128)]
    effect_observation: Literal["observed", "unknown"]
    observed_terminal_outcome: TerminalOutcome | None = None
    parameters: dict[str, int] = Field(default_factory=dict)
    details: dict[str, object] = Field(default_factory=dict)
    applied_server_ns: Annotated[int | None, Field(strict=True, ge=0)] = None
    applied_connection_epoch: Epoch | None = None
    applied_response_epoch: Epoch | None = None


class Ping(Model):
    type: Literal["ping"]
    sequence: Annotated[int, Field(strict=True, ge=0, le=19)]
    browser_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]


ClientMessage = Annotated[Union[Hello, Audio, Commit, Interrupt, Rendered, Resume, End, ClientStatus, ClockProbe, Ping], Field(discriminator="type")]
CLIENT_ADAPTER = TypeAdapter(ClientMessage)


def parse_client(payload: str) -> ClientMessage:
    if len(payload.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("message_too_large")
    return CLIENT_ADAPTER.validate_json(payload)


class VoiceEvent(Model):
    schema_version: Literal[1] = 1
    run_id: Identifier
    trial_id: Identifier
    arm: Literal["V", "C", "K"]
    session_id: Identifier
    logical_turn_id: Identifier | None = None
    transport_turn_id: Identifier | None = None
    response_id: Identifier | None = None
    operation_id: Identifier | None = None
    attempt_id: Identifier | None = None
    connection_epoch: Epoch
    response_epoch: Epoch
    sequence: Annotated[int, Field(strict=True, ge=0)]
    kind: Literal["session_started", "turn_started", "turn_ended", "calibration_phase", "fault_control", "clock_probe", "audio_received", "commit_received", "transcript", "controller_start", "controller_end", "audio_sent", "rendered_ack", "cancel_dispatch", "fence_applied", "stale_discarded", "reconnect", "repeat_requested", "effect_commit", "provider_event", "integrity_error", "session_ended", "error"]
    timestamp: Annotated[int | float, Field(ge=0, allow_inf_nan=False)]
    clock_domain: Literal["server_monotonic_ns", "browser_performance_ms", "audio_context_samples", "provider_unknown"]
    observation: Literal["observed", "estimated", "unknown"] = "observed"
    reason: Annotated[str | None, Field(max_length=500)] = None
    sample_offset: Annotated[int | None, Field(strict=True, ge=0)] = None
    sample_rate: Literal[24000] | None = None
    artifact_hash: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    content_hash: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    terminal_outcome: TerminalOutcome | None = None
    outcome_source: Literal["relay", "client_report", "restart_reconciliation", "mock_fault_simulation"] | None = None
    boot_id: Identifier | None = None
    span_start_ns: Annotated[int | None, Field(strict=True, ge=0)] = None
    span_end_ns: Annotated[int | None, Field(strict=True, ge=0)] = None
    span_clock_domain: Literal["server_monotonic_ns"] | None = None
    control_id: Identifier | None = None
    fault_kind: FaultKind | None = None
    fault_status: Literal["armed", "applied", "unresolved"] | None = None
    simulation: bool | None = None
    affected_boundary: Annotated[str | None, Field(max_length=96)] = None
    injected_count: Annotated[int | None, Field(strict=True, ge=0, le=128)] = None
    provider_tool_id: Identifier | None = None
    provider_transport_id: Identifier | None = None
    phase_id: Identifier | None = None
    phase_action: Literal["begin", "end"] | None = None
    phase_deadline_ns: Annotated[int | None, Field(strict=True, ge=0)] = None

    @model_validator(mode="after")
    def integrity(self) -> VoiceEvent:
        if self.clock_domain in {"server_monotonic_ns", "audio_context_samples"} and type(self.timestamp) is not int:
            raise ValueError("integer_clock_timestamp_required")
        if self.kind in {"turn_ended", "session_ended"} and self.terminal_outcome is None:
            raise ValueError("terminal_outcome_required")
        if self.span_start_ns is not None or self.span_end_ns is not None:
            if self.span_start_ns is None or self.span_end_ns is None or self.span_end_ns < self.span_start_ns or self.span_clock_domain != "server_monotonic_ns":
                raise ValueError("invalid_same_clock_span")
            if self.clock_domain == "server_monotonic_ns" and self.span_end_ns > self.timestamp:
                raise ValueError("span_ends_after_receipt")
        return self


class ServerMessage(Model):
    """Generic envelope kept for existing readers; SERVER_MESSAGE_ADAPTER closes each payload."""
    type: Literal["ready", "state", "transcript", "audio", "event", "task", "error", "ended", "pong", "clock_pong"]
    protocol_version: Literal[1] = 1
    session_id: Identifier
    connection_epoch: Epoch
    response_epoch: Epoch
    sequence: Annotated[int, Field(strict=True, ge=0)]
    payload: dict[str, object]


def serialize_server(message: ServerMessage) -> str:
    """Encode once, then validate those exact bytes against the closed variant registry.

    Emitted bytes stay the existing envelope encoding; registry defaults are never injected.
    Validation errors are not echoed: they could quote transcript text or PCM.
    """
    try:
        encoded = message.model_dump_json()
        SERVER_MESSAGE_ADAPTER.validate_json(encoded)
    except ValueError:
        raise ValueError("server_payload_contract_violation") from None
    return encoded


def parse_server(raw: str | bytes) -> ServerMessageVariant:
    """Closed consumer-side parse; unknown variants, fields and payload shapes refuse."""
    return SERVER_MESSAGE_ADAPTER.validate_json(raw)


Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Slot(Model):
    id: Literal["slot-a", "slot-b"]
    label: str


class TaskOutcome(Model):
    ok: bool
    logical_operation_id: Identifier
    duplicate: bool = False
    retryable_no_effect: bool = False
    code: str | None = None
    state: Literal["information", "slots_offered", "slot_selected", "awaiting_confirmation", "booked", "complete"] | None = None
    state_version: Annotated[int | None, Field(strict=True, ge=0)] = None
    slot_id: Literal["slot-a", "slot-b"] | None = None
    challenge: str | None = None
    response_epoch: Epoch | None = None
    booking: dict[str, object] | None = None
    effect_committed: bool = False
    hours: str | None = None
    slots: list[Slot] | None = None


class Attempt(Model):
    schema_version: Literal[1] = 1
    attempt_id: Identifier
    logical_turn_id: Identifier
    provider_request_id: str | None = None
    arm: Literal["V", "C", "K"]
    component: str
    reservation: Annotated[str, Field(pattern=r"^\d+(\.\d+)?$")]
    basis: str
    price_version: str
    currency: Literal["USD"]
    state: Literal["reserved", "settled", "unknown"]
    settled: Annotated[str | None, Field(pattern=r"^\d+(\.\d+)?$")] = None
    units: dict[str, str] = Field(default_factory=dict)
    usage_source: str | None = None
    terminal_reason: str | None = None
    trial_id: Identifier | None = None
    owner_id: Identifier | None = None
    session_id: Identifier | None = None
    transport_turn_id: Identifier | None = None
    connection_epoch: Epoch | None = None
    response_epoch: Epoch | None = None
    lineage_observation: Literal["recorded_at_reservation", "unknown"] = "unknown"
    dispatch_authorized: bool | None = None
    dispatched: bool | None = None
    started: bool | None = None
    reservation_terms: ReservationTerms | None = None
    transport_source: Literal["provider_network", "injected_unverified"] | None = None


class TurnResult(Model):
    schema_version: Literal[1] = 1
    run_id: Identifier
    trial_id: Identifier
    logical_turn_id: Identifier
    arm: Literal["V", "C", "K"]
    outcome: TerminalOutcome
    task_correct: bool | None
    spoken_correct: bool | None
    receipt_latency_ms: Annotated[float | None, Field(ge=0, allow_inf_nan=False)] = None
    render_latency_ms: Annotated[float | None, Field(ge=0, allow_inf_nan=False)] = None
    timing_boundary: Literal["receipt_only", "browser_output_timestamp_estimate", "unknown"]
    calibration_valid: bool | None
    unknown_reason: str | None = None
    retry_count: Annotated[int, Field(strict=True, ge=0)] = 0
    duplicate_count: Annotated[int, Field(strict=True, ge=0)] = 0
    cost_complete: bool = False
    stages: list[dict[str, object]] = Field(default_factory=list)
    admission: Literal["scheduled", "started", "refused", "missing_terminal", "unknown"] = "unknown"
    cohort: Literal["clean", "fault", "diagnostic"] = "diagnostic"
    task_id: Identifier | None = None
    task_terminal: TerminalOutcome | None = None
    network_profile: str | None = None
    model_version: str | None = None
    family: str | None = None
    pair_id: Identifier | None = None
    timing_domain: Literal["browser_performance_ms", "server_monotonic_ns", "unknown"] = "unknown"


class ScheduledTurn(Model):
    """Prespecified membership survives missing terminal rows; refs are separate evidence."""
    logical_turn_id: Identifier
    trial_id: Identifier
    arm: Literal["V", "C", "K"]
    cohort: Literal["clean", "fault", "diagnostic"]
    network_profile: Annotated[str, Field(min_length=1, max_length=500)]
    model_version: Annotated[str, Field(min_length=1, max_length=500)]
    task_id: Identifier | None = None
    family: str | None = None
    pair_id: Identifier | None = None
    admission: Literal["scheduled", "started", "refused", "not_started", "unknown"] = "unknown"
    admission_evidence_reference: Annotated[str | None, Field(min_length=1, max_length=500)] = None
    # Observed identities stay separate from immutable prespecified trial membership.
    observed_trial_id: Identifier | None = None
    observed_session_id: Identifier | None = None
    observed_boot_id: Identifier | None = None
    identity_evidence_reference: Annotated[str | None, Field(min_length=1, max_length=500)] = None


class RunManifest(Model):
    schema_version: Literal[1] = 1
    run_id: Identifier
    mode: Literal["mock", "replay", "live"]
    code_hash: Hash
    build_hash: Hash
    config_hash: Hash
    schema_hash: Hash
    fixture_manifest_hash: Hash
    split: Literal["dev", "heldout"]
    fixture_seal: str | None
    task_version: str
    prompt_version: str
    tool_version: str
    adapters: dict[str, str]
    models: dict[str, str]
    audio: dict[str, object]
    endpoint: dict[str, object]
    onset: dict[str, object]
    resampler: dict[str, object]
    browser: str
    operating_system: str
    device: str
    network: dict[str, object]
    fault_seed: int
    calibration_hash: Hash | None
    trial_plan_hash: Hash
    thresholds: dict[str, object]
    pricing_manifest_hash: Hash | None
    authorization_reference: str | None
    evidence_label: str
    created_at: str
    scheduled_turns: list[Identifier]
    # Legacy manifests parse, but incomplete assignment/start evidence cannot support
    # a complete all-started cohort headline. Reporting owns exact-set reconciliation.
    turn_assignments: list[ScheduledTurn] = Field(default_factory=list)
    scheduled_tasks: list[Identifier] = Field(default_factory=list)
    bootstrap_seed: int = 0


class EvidenceRecord(Model):
    schema_version: Literal[1] = 1
    run_id: Identifier
    trial_id: Identifier
    trace_hash: Hash
    audio_hash: Hash | None
    ledger_hash: Hash
    oracle_hash: Hash | None
    result_hash: Hash
    custody_reference: str | None
    observation: Literal["observed", "estimated", "unknown"]
    unknown_reason: str | None


# Server payload registry v1. One closed payload model per ServerMessage variant the
# relay actually emits (server/app.py Relay.send call sites). Adapter-originated
# payloads keep their adapter envelope fields (type/sequence/epochs/turn_id). Adapter
# events consumed by the relay (mock_transport_drop, mock_provider_error) are never sent.
# The registry persists nothing; existing PCM/task-journal persistence is unchanged and
# trace/snapshot persistence keeps its own closed metadata.

ServerCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
ServerNs = Annotated[int, Field(strict=True, ge=0)]
BrowserMs = Annotated[int | float, Field(ge=0, allow_inf_nan=False)]
EvidenceLabel = Annotated[str, Field(min_length=1, max_length=500)]
EvidenceReference = Annotated[str, Field(pattern=r"^/sessions/[A-Za-z0-9_-]{1,96}/evidence$", max_length=120)]
BoundedReference = Annotated[str, Field(min_length=1, max_length=500)]
CostText = Annotated[str, Field(pattern=r"^\d+(?:\.\d+)?$", max_length=200)]
RelayState = Literal["connecting", "listening", "capturing", "awaiting_response", "playing", "calibrating", "disconnected", "reconnecting", "closed"]
TaskState = Literal["information", "slots_offered", "slot_selected", "awaiting_confirmation", "booked", "complete"]
TaskCode = Literal["logical_operation_conflict", "stale_epoch", "already_booked", "select_offered_slot", "explicit_current_confirmation_required", "mock_drop_before_effect", "clarification", "explicit_confirmation_required", "stale_confirmation_proposal", "licensed_fixed_control_no_intent_inference"]


class TaskBooking(Model):
    """TaskAuthority task_bookings row; the only durable booking effect."""
    namespace: Annotated[str, Field(min_length=1, max_length=128)]
    operation_id: Identifier
    slot_id: Literal["slot-a", "slot-b"]
    confirmation_turn: Identifier
    epoch: Epoch
    state_version: Annotated[int, Field(strict=True, ge=0)]


class TaskPayload(Model):
    """`task` payload and nested task views: authority outcome or snapshot, clarification, or K control view."""
    ok: bool | None = None
    logical_operation_id: Identifier | None = None
    duplicate: bool | None = None
    retryable_no_effect: bool | None = None
    effect_committed: bool | None = None
    code: TaskCode | None = None
    status: Literal["clarification"] | None = None
    message: Annotated[str | None, Field(max_length=500)] = None
    lexical_miss: Literal[True] | None = None
    hours: Annotated[str | None, Field(max_length=200)] = None
    slots: list[Slot] | None = None
    state: TaskState | None = None
    state_version: Annotated[int | None, Field(strict=True, ge=0)] = None
    slot_id: Literal["slot-a", "slot-b"] | None = None
    challenge: Annotated[str | None, Field(max_length=128)] = None
    response_epoch: Epoch | None = None
    booking: TaskBooking | None = None
    control_only: Literal[True] | None = None
    task_applicability: Literal["not_applicable"] | None = None
    spoken_correctness: Literal["unknown"] | None = None
    intent_observation: Literal["unknown"] | None = None
    asset_id: Identifier | None = None
    catalog_hash: Hash | None = None

    @model_validator(mode="after")
    def control_view_is_descriptive(self) -> TaskPayload:
        if self.control_only is True and (self.task_applicability != "not_applicable" or self.spoken_correctness != "unknown" or self.effect_committed is True or self.ok is True):
            raise ValueError("control_task_view_cannot_claim_task_result")
        return self


class TaskUnknown(Model):
    """Ended task view when the authority snapshot itself was unavailable."""
    state: Literal["unknown"]
    control_only: Literal[True] | None = None
    task_applicability: Literal["not_applicable"] | None = None
    spoken_correctness: Literal["unknown"] | None = None
    intent_observation: Literal["unknown"] | None = None


class ReadyPayload(Model):
    mode: Literal["mock", "live"]
    arm: Literal["V", "C", "K"]
    evidence_label: EvidenceLabel
    trial_namespace: Identifier
    run_id: Identifier
    trial_id: Identifier
    boot_id: Identifier
    admission: AdmissionEvidence
    evidence_reference: EvidenceReference
    resume_required: Literal[True] | None = None

    @model_validator(mode="after")
    def one_identity(self) -> ReadyPayload:
        if self.trial_id != self.trial_namespace or self.admission.run_id != self.run_id or self.admission.mode != self.mode:
            raise ValueError("ready_identity_mismatch")
        return self


class StatePayload(Model):
    state: RelayState
    fence_epoch: Epoch | None = None
    task: TaskPayload | None = None


class AdapterEventFields(Model):
    """Adapter envelope fields the relay forwards unchanged inside adapter-originated payloads."""
    sequence: Annotated[int, Field(strict=True, ge=0)]
    connection_epoch: Epoch
    response_epoch: Epoch
    turn_id: Identifier | None = None


class TranscriptPayload(AdapterEventFields):
    type: Literal["transcript"]
    turn_id: Identifier
    text: Annotated[str, Field(max_length=8000)]
    source: Literal["mock_debug_text", "provider_transcript_unverified"]
    evidence_label: EvidenceLabel | None = None


class AudioPayload(AdapterEventFields):
    type: Literal["audio"]
    turn_id: Identifier
    response_id: Identifier
    sample_rate: Literal[24000]
    pcm16: Annotated[str, Field(min_length=4, max_length=12_800)]
    evidence_label: EvidenceLabel | None = None
    asset_id: Identifier | None = None
    synthetic: bool
    artifact_hash: Hash | None
    content_hash: Hash
    artifact_persisted: bool

    @model_validator(mode="after")
    def persisted_reference(self) -> AudioPayload:
        if self.artifact_persisted != (self.artifact_hash is not None):
            raise ValueError("audio_artifact_persistence_mismatch")
        return self


class ControllerSpanEvent(AdapterEventFields):
    type: Literal["event"]
    kind: Literal["controller_span"]
    turn_id: Identifier
    start_ns: ServerNs
    end_ns: ServerNs
    clock_domain: Literal["server_monotonic_ns"]
    translation: Literal["identity_noop"]
    mock_native_reasoning: bool | None = None

    @model_validator(mode="after")
    def ordered_span(self) -> ControllerSpanEvent:
        if self.end_ns < self.start_ns:
            raise ValueError("invalid_controller_span")
        return self


class MockToolAttemptEvent(AdapterEventFields):
    type: Literal["event"]
    kind: Literal["mock_tool_attempt"]
    turn_id: Identifier
    provider_tool_id: Identifier
    provider_transport_id: Identifier
    logical_operation_id: Identifier
    duplicate: bool
    effect_committed: bool


class ResponseDoneEvent(AdapterEventFields):
    type: Literal["event"]
    kind: Literal["response_done"]
    turn_id: Identifier
    response_id: Identifier
    spoken_correctness: Literal["unknown"] | None = None
    control_only: Literal[True] | None = None
    model_stages: Literal["not_applicable"] | None = None
    translation: Literal["identity_noop_no_duration"] | None = None


class CancelAckEvent(AdapterEventFields):
    type: Literal["event"]
    kind: Literal["cancel.ack"]
    response_id: Identifier | None
    rendered_cutoff: Annotated[int | None, Field(strict=True, ge=0)]
    history_method: Literal["mock_no_provider_history"]


class ResumeAckEvent(AdapterEventFields):
    type: Literal["event"]
    kind: Literal["resume.ack"]
    rendered_ack: Rendered | None
    audio_replayed: Literal[False]
    reason: Annotated[str, Field(max_length=500)]


class TtsTimingEvent(AdapterEventFields):
    type: Literal["event"]
    kind: Literal["tts_request", "tts_first_pcm"]
    turn_id: Identifier
    timestamp_ns: ServerNs
    clock_domain: Literal["server_monotonic_ns"]


class RepeatRequestedEvent(Model):
    """Relay-originated; it carries no adapter envelope fields."""
    kind: Literal["repeat_requested"]
    message: Literal["Please repeat your last request; any booking remains recorded."]
    audio_replayed: Literal[False]


EventPayload = Annotated[Union[ControllerSpanEvent, MockToolAttemptEvent, ResponseDoneEvent, CancelAckEvent, ResumeAckEvent, TtsTimingEvent, RepeatRequestedEvent], Field(discriminator="kind")]


class ErrorPayload(Model):
    code: ServerCode
    outcome: TerminalOutcome
    message: Literal["Session ended; inspect the labeled trace and task journal."]
    terminal: Literal[True]


class PongPayload(Model):
    """Legacy ping echo; no one-way delay or mixed-clock subtraction."""
    sequence: Annotated[int, Field(strict=True, ge=0, le=19)]
    browser_ms: BrowserMs
    server_receive_ns: ServerNs
    server_send_ns: ServerNs
    clock_domain: Literal["server_monotonic_ns"]
    one_way_delay_known: Literal[False]


class ClockPongPayload(Model):
    probe_id: Identifier
    browser_sent_ms: BrowserMs
    server_received_ns: ServerNs
    server_sent_ns: ServerNs
    clock_domain: Literal["server_monotonic_ns"]
    browser_clock_domain: Literal["browser_performance_ms"]
    boot_id: Identifier
    probe_count: Annotated[int, Field(strict=True, ge=1, le=20)]
    probe_limit: Literal[20]
    server_receive_boundary: Literal["validated_clock_probe_handler"]
    server_send_boundary: Literal["relay_send_dispatch"]
    one_way_delay_known: Literal[False]


class TraceSummary(Model):
    """TraceWriter.metadata() plus relay integrity flag; metadata only."""
    trace_hash: Hash | None
    event_count: Annotated[int, Field(strict=True, ge=0)]
    terminal_present: bool
    clock_domain: Literal["server_monotonic_ns"]
    terminal_fsynced: bool
    boot_id: Identifier
    run_id: Identifier
    trial_id: Identifier
    session_id: Identifier
    arm: Literal["V", "C", "K"]
    trace_reference: Annotated[str, Field(pattern=r"^traces/[A-Za-z0-9_-]{1,96}\.jsonl$")]
    integrity_error: bool


class SnapshotArtifact(Model):
    """save_snapshot() metadata; ledger snapshots add run scope and capture clock."""
    kind: Literal["ledger_snapshot", "fault_controls", "calibration_phases"]
    artifact_hash: Hash
    artifact_reference: BoundedReference
    bytes: Annotated[int, Field(strict=True, ge=1, le=32_000_000)]
    encoding: Literal["canonical_json_utf8_sorted_compact_newline"]
    json_ensure_ascii: Literal[True]
    observation: Literal["observed"]
    scope: Literal["run_ledger_snapshot"] | None = None
    run_id: Identifier | None = None
    captured_for_session_id: Identifier | None = None
    boot_id: Identifier | None = None
    server_timestamp_ns: ServerNs | None = None
    clock_domain: Literal["server_monotonic_ns"] | None = None


class UnknownArtifact(Model):
    observation: Literal["unknown"]
    reason: Literal["ledger_snapshot_unavailable", "ledger_snapshot_write_or_read_failure", "fault_receipts_snapshot_unavailable", "calibration_snapshot_unavailable"]


class LedgerSummary(Model):
    """AttemptLedger.summary() top level. Rows stay the canonical raw ledger rows, typed by accounting."""
    budget: CostText
    known_cost: CostText
    unknown_count: Annotated[int, Field(strict=True, ge=0)]
    unknown_exposure: CostText
    complete: bool
    total_exposure: CostText
    attempts: list[dict[str, object]]
    events: list[dict[str, object]]
    admission_halted: Annotated[str | None, Field(max_length=200)]
    currency: Literal["USD"]
    bound_violation_count: Annotated[int, Field(strict=True, ge=0)]
    bound_integrity: Literal["invalid", "not_disproved"]
    bound_violation_attempt_count: Annotated[int, Field(strict=True, ge=0)]
    dispatch_halt_active: bool
    dispatch_allowed: Literal[False] | None
    owner_id: Annotated[str | None, Field(max_length=200)]
    snapshot_owner_id: Annotated[str | None, Field(max_length=200)]
    price_policy_fingerprints: dict[str, str]
    total_exposure_definition: Literal["immutable known settlements plus reserved/unknown exposure; late usage evidence is not a reconciliation"]


class RetentionSummary(Model):
    """TrialRetention.finalize() result or its write-failure stand-in; hashes/references only, never PCM."""
    input_persisted: bool | Literal["unknown"]
    output_persisted: bool | Literal["unknown"]
    reason: Literal["no_synthetic_retention_manifest", "synthetic_input_mismatch_or_approval_expired", "exact_approved_synthetic_trial_input", "retention_write_failure; inspect approved artifact directory"]
    schema_version: Literal[1] | None = None
    run_id: Identifier | None = None
    trial_id: Identifier | None = None
    fixture_id: Identifier | None = None
    session_id: Identifier | None = None
    input_content_hash: Hash | None = None
    input_artifact_hash: Hash | None = None
    output_artifact_hash: Hash | None = None
    output_boundary: Literal["client_rendered_ack_pcm_prefixes"] | None = None
    sample_rate: Literal[24000] | None = None
    channels: Literal[1] | None = None
    encoding: Literal["signed_little_endian_pcm16"] | None = None
    approval_reference: BoundedReference | None = None
    input_rights_reference: BoundedReference | None = None
    # Manifest parse accepts "" here when output is not retained, and bounds no expiry length.
    output_rights_reference: Annotated[str | None, Field(max_length=500)] = None
    manifest_reference: BoundedReference | None = None
    retention_until_utc: str | None = None
    custody_reference: BoundedReference | None = None
    policy: Literal["operator_custody_expiry"] | None = None


class EndedPayload(Model):
    reason: ServerCode
    outcome: TerminalOutcome
    outcome_source: Literal["relay", "client_report", "mock_fault_simulation"]
    task: TaskPayload | TaskUnknown
    trace_hash: Hash | None
    trace: TraceSummary
    lifecycle_persisted: bool
    effect_fence: Literal["applied", "unknown"]
    ledger: LedgerSummary | None
    ledger_artifact: SnapshotArtifact | UnknownArtifact
    faults_artifact: SnapshotArtifact | UnknownArtifact | None
    calibration_artifact: SnapshotArtifact | UnknownArtifact
    admission: AdmissionEvidence
    retention: RetentionSummary
    evidence_label: EvidenceLabel
    evidence_reference: EvidenceReference
    spoken_correctness: Literal["unknown"]

    @model_validator(mode="after")
    def snapshot_reference(self) -> EndedPayload:
        if (self.ledger is None) != (self.ledger_artifact.observation == "unknown"):
            raise ValueError("ended_ledger_snapshot_reference_mismatch")
        return self


class ServerEnvelope(Model):
    protocol_version: Literal[1] = 1
    session_id: Identifier
    connection_epoch: Epoch
    response_epoch: Epoch
    sequence: Annotated[int, Field(strict=True, ge=0)]


class ReadyMessage(ServerEnvelope):
    type: Literal["ready"]
    payload: ReadyPayload


class StateMessage(ServerEnvelope):
    type: Literal["state"]
    payload: StatePayload


class TranscriptMessage(ServerEnvelope):
    type: Literal["transcript"]
    payload: TranscriptPayload


class AudioMessage(ServerEnvelope):
    type: Literal["audio"]
    payload: AudioPayload


class EventMessage(ServerEnvelope):
    type: Literal["event"]
    payload: EventPayload


class TaskMessage(ServerEnvelope):
    type: Literal["task"]
    payload: TaskPayload


class ErrorMessage(ServerEnvelope):
    type: Literal["error"]
    payload: ErrorPayload


class EndedMessage(ServerEnvelope):
    type: Literal["ended"]
    payload: EndedPayload


class PongMessage(ServerEnvelope):
    type: Literal["pong"]
    payload: PongPayload


class ClockPongMessage(ServerEnvelope):
    type: Literal["clock_pong"]
    payload: ClockPongPayload


# Closed registry: the envelope type is the discriminator; an unknown type refuses.
ServerMessageVariant = Annotated[Union[ReadyMessage, StateMessage, TranscriptMessage, AudioMessage, EventMessage, TaskMessage, ErrorMessage, EndedMessage, PongMessage, ClockPongMessage], Field(discriminator="type")]
SERVER_MESSAGE_ADAPTER = TypeAdapter(ServerMessageVariant)
SERVER_PAYLOAD_REGISTRY_VERSION = 1
