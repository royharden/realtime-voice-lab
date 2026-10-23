"""Canonical bounded protocol v1. Browser milliseconds never become server nanoseconds."""
from __future__ import annotations

import base64
import binascii
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

PROTOCOL_VERSION = 1
SAMPLE_RATE = 24_000
MAX_FRAME_SAMPLES = 4_800
MAX_MESSAGE_BYTES = 24_000
Identifier = Annotated[str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")]
Epoch = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


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


class Ping(Model):
    type: Literal["ping"]
    sequence: Annotated[int, Field(strict=True, ge=0, le=19)]
    browser_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]


ClientMessage = Annotated[Union[Hello, Audio, Commit, Interrupt, Rendered, Resume, End, Ping], Field(discriminator="type")]
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
    kind: Literal["session_started", "audio_received", "commit_received", "transcript", "controller_start", "controller_end", "audio_sent", "rendered_ack", "cancel_dispatch", "fence_applied", "stale_discarded", "reconnect", "repeat_requested", "effect_commit", "provider_event", "integrity_error", "session_ended", "error"]
    timestamp: Annotated[int | float, Field(ge=0, allow_inf_nan=False)]
    clock_domain: Literal["server_monotonic_ns", "browser_performance_ms", "audio_context_samples", "provider_unknown"]
    observation: Literal["observed", "estimated", "unknown"] = "observed"
    reason: Annotated[str | None, Field(max_length=500)] = None
    sample_offset: Annotated[int | None, Field(strict=True, ge=0)] = None
    sample_rate: Literal[24000] | None = None
    artifact_hash: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None
    content_hash: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None


class ServerMessage(Model):
    """Envelope; payload type is explicit and all emissions use this serializer."""
    type: Literal["ready", "state", "transcript", "audio", "event", "task", "error", "ended", "pong"]
    protocol_version: Literal[1] = 1
    session_id: Identifier
    connection_epoch: Epoch
    response_epoch: Epoch
    sequence: Annotated[int, Field(strict=True, ge=0)]
    payload: dict[str, object]


def serialize_server(message: ServerMessage) -> str:
    return message.model_dump_json()


TerminalOutcome = Literal["success", "task_failure", "off_script", "permission_denied", "silence_timeout", "device_lost", "provider_error", "provider_timeout", "transport_loss", "service_restart", "abandoned", "queue_overrun", "calibration_invalid", "budget_halt", "expected_interruption", "recovery_unknown", "missing_terminal"]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Slot(Model):
    id: Literal["slot-a", "slot-b"]
    label: str


class TaskOutcome(Model):
    ok: bool
    logical_operation_id: Identifier
    duplicate: bool = False
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
