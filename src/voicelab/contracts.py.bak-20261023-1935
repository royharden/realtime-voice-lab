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
TerminalOutcome = Literal["success", "task_failure", "off_script", "permission_denied", "silence_timeout", "device_lost", "provider_error", "provider_timeout", "transport_loss", "service_restart", "abandoned", "queue_overrun", "calibration_invalid", "budget_halt", "expected_interruption", "recovery_unknown", "missing_terminal", "protocol_error", "unsupported_audio"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


DecimalText = Annotated[str, Field(pattern=r"^\d{1,18}(?:\.\d{1,18})?$", max_length=37)]
UsagePath = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){0,7}$", max_length=160)]
ProviderIdentityTag = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$", max_length=200)]


class ReservationTerms(Model):
    """Operator-supplied prices and bounds; this model supplies no tariff or entitlement."""
    schema_version: Literal[1] = 1
    mode: Literal["mock", "live"]
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
    kind: Literal["session_started", "turn_started", "turn_ended", "fault_control", "clock_probe", "audio_received", "commit_received", "transcript", "controller_start", "controller_end", "audio_sent", "rendered_ack", "cancel_dispatch", "fence_applied", "stale_discarded", "reconnect", "repeat_requested", "effect_commit", "provider_event", "integrity_error", "session_ended", "error"]
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
    """Envelope; payload type is explicit and all emissions use this serializer."""
    type: Literal["ready", "state", "transcript", "audio", "event", "task", "error", "ended", "pong", "clock_pong"]
    protocol_version: Literal[1] = 1
    session_id: Identifier
    connection_epoch: Epoch
    response_epoch: Epoch
    sequence: Annotated[int, Field(strict=True, ge=0)]
    payload: dict[str, object]


def serialize_server(message: ServerMessage) -> str:
    return message.model_dump_json()


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
