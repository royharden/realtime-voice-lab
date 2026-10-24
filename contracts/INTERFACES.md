# Source interfaces

Runtime unverified. `src/voicelab/contracts.py` is the canonical typed schema;
`wire-v1.json` is its common-bound reference. Existing version 1 field names remain.

## Plan, actual identities and evidence

`evaluation/planning.py` owns ordered plan tasks and their deterministic generation:
task_id, trial_id, pair_id, arm, cohort, family, speaker, repeat, network_profile,
model_version, fixture_id, fault_schedule, logical_turn_ids, control. No second
task-plan model or sampling truth is introduced here. `evaluation/assembly.py`
owns `TurnObservation` and raw-driver-event conversion.

| Canonical model | Mapping and evidence rule |
| --- | --- |
| ScheduledTurn | One row per prespecified logical turn; trial/task, arm, cohort, network/model, family and per-turn pair membership come from the frozen plan. Planning starts with admission unknown. `admission_evidence_reference` names independently saved admission/start evidence; a plan row is not evidence of execution. |
| ScheduledTurn observed identity fields | Optional `observed_trial_id`, `observed_session_id`, `observed_boot_id`, `identity_evidence_reference` retain the actual ready/trace/journal identity mapping separately from the immutable planned trial. Missing mapping stays unknown; consumers must verify the referenced saved bytes before joining differing IDs. Never overwrite observed trace IDs with planned IDs. |
| RunManifest | Pins hashes, mode/cohort versions, fixture split/seal, calibration, pricing/authorization and frozen trial plan; `scheduled_turns`/`scheduled_tasks` retain the full denominator. `turn_assignments` carries canonical membership and later explicitly referenced admission/identity observations. Missing start/terminal evidence cannot remove a scheduled row. |
| Attempt | One reservation/settlement/unknown row per provider component attempt, with stable logical parent, exact decimal strings, basis, price version and usage source. Adapter ledger owns accounting. Reserving is not independent proof of dispatch or acceptance. |
| EvidenceRecord | Binds the result to hashed trace/audio/ledger/oracle artifacts and custody reference. Hashing a claim is not independent verification. Missing audio/oracle/terminal/calibration remains unknown. |

Relay ready adds run/trial/boot IDs and `/sessions/{session_id}/evidence`. Programmatic
operator `Settings.run_id` selects a mock run ID; default is `mock-local`. Live run ID
comes from its gated producer configuration. Optional operator `run_manifest_hash`
is syntax-validated and retained as an operator-supplied link, not independently
certified. Actual trial namespaces are server-assigned (or approved retention trial
assignments); a consumer retains the plan-to-observed mapping as saved evidence.

`VoiceEvent.turn_started` is observed relay input/commit admission, not prespecified
membership. `turn_ended.terminal_outcome` closes a transport attempt; recovery may
have multiple transport attempts with the same logical parent. Reducers must preserve
attempt order, not treat expected loss/interruption followed by recovery as duplicate
logical results or silently discard failed attempts. `boot_id` scopes server
monotonic timestamps; no subtraction across boots or browser/server domains.
Controller spans use explicit `span_start_ns`/`span_end_ns` and
`span_clock_domain=server_monotonic_ns`; receipt timestamps are not substituted for them.

Client status is exactly `{type:"client_status",outcome}` with the five closed outcomes
permission_denied, device_lost, silence_timeout, abandoned, unsupported_audio. There is
no reason/free-text field. Device mute stays a bounded local observation; preconnection
observations never become invented server receipts. Ended retains outcome/reason,
task, trace IDs/hash/clock, and the ordinary envelope connection/response epochs.
The protected `/sessions/{session_id}/trace` endpoint exports metadata-only JSONL
after terminal closure, with actual hash and explicit completeness headers; a crash
prefix stays incomplete. No raw-audio download or transcript artifact is introduced.

## Mock fault control source (runtime unverified)

`POST /sessions/{session_id}/faults` uses exact
Host/Origin and X-Voicelab-Nonce. Require an operator-enabled mock-only Settings
fault policy containing a fixed seed and authorization reference; refuse live mode
and absent policy. Never accept arbitrary provider payloads, paths or executable text.

The exact body is `{control_id,kind}` (at most 512 bytes). `control_id` follows Identifier grammar. Closed
kinds: late_pcm, duplicate_pcm, reordered_pcm, lost_ack, drop_before_commit,
drop_after_commit, fresh_tool_duplicate, service_restart, provider_error,
missing_audio, missing_transcript, queue_overrun. Timing is fixed by kind: PCM faults
at next output, lost_ack at next rendered ACK, before/after commit at the authority
effect seam, fresh tool duplicate at the next task proposal, named restart/error now.
Reject invalid/finished targets; replaying an identical control_id returns its original
receipt, conflicting reuse refuses. Seed-derived parameters are recorded in the
receipt. Canonical `MockFaultReceipt` includes control_id, kind, seed, status
(armed/applied/unresolved), `simulation:true`, session/run/trial/boot/epoch identity,
`affected_boundary`, `injected_count`, generated parameters, details and the later
observed terminal outcome. Invalid requests/reuse/closed targets return explicit
HTTP errors. Receipt `applied` means the named injection boundary ran; it does not
prove an expected browser outcome, queue failure, recovery or task correctness.
`fault-controls-v1.json` references the exact request and `fault-receipt-v1.json`
references the actual receipt; Python MockFaultControl/Policy/Receipt remain canonical.
Receipt connection/response epochs describe arming; applied_connection_epoch and
applied_response_epoch identify the later injection point. Late-frame details retain
the original frame epoch/sequence. Combined PCM controls share a transformed batch:
their injected_count values are explicitly not additive across those controls.

`Settings.mock_fault_policy` defaults absent. Enabling it requires canonical
`MockFaultPolicy`: literal enabled true, u32 seed, bounded operator authorization
reference and max_controls 1..32 (default 32). Live activation with this policy refuses.
The seed and stable control ID select late-frame delay 100..500 ms; the other kinds
select deterministic named boundaries rather than probabilistic unreported drops.
No policy/authorization is supplied by the product or its default CLI.

Simulated service_restart terminates only the target owned mock session with that
named outcome; it neither kills nor restarts a process and cannot stand in for an
actual restart observation (`actual_process_restart:false`, source
`mock_fault_simulation`). Fresh-tool duplication traverses the real mock `tool.call`
serializer/parser and, on V, native proposal parser, with distinct provider tool and
provider-transport IDs, one stable logical operation and the unchanged F1 guard.
Both authority attempts are journaled; only a newly committed effect is traced once.
Before-effect drop runs only after genuine confirmation eligibility in the mock
authority: its no-effect attempt is recorded without freezing an original operation
outcome, so a reconnect can perform it once. After-effect drop runs after SQLite commit.
Both close only the owned mock socket and keep the ordinary bounded recovery/no-replay path.

PCM is parsed in four 20 ms tone chunks. Duplicate/reordered injections preserve
source sequence identity, late delivery preserves original turn/epochs, missing
audio/transcript explicitly suppress parsed output, and queue-overrun injects a bounded
2.4-second diagnostic PCM burst. Queue outcomes are observed later, never inferred from
the injection request. Adapter event/PCM/discard/task counts are retained in the fault
snapshot. Unreached/canceled injection boundaries finish unresolved. No silent playback replay.
Post-End evidence remains available through the protected metadata GET, while new
controls after End refuse. Driver scheduling does not prove an applied fault; save
the receipt plus actual observer/wire/journal results and retain unknown/missing effects.

## Saved ledger and probe interfaces

The terminal evidence GET includes `ledger` (the existing raw attempts/events/totals
summary) and `ledger_artifact` (artifact_hash SHA256, artifact_reference, run
snapshot scope and capture clock/boot). The existing atomic ledger read is reused
unchanged. Sorted compact UTF-8 JSON with a final newline is saved once per ending
session; later GET verifies those saved bytes instead of recomputing newer totals.
The exact codec is json.dumps(sort_keys=True,separators=(comma,colon),
allow_nan=False,ensure_ascii=True), one final newline, encoded UTF-8. Metadata records
json_ensure_ascii=true. Hash the saved snapshot bytes, not the surrounding HTTP response.
The snapshot is run-wide, not an isolated per-session bill. `faults`/`faults_artifact`
likewise export the saved session control receipts and adapter counters. Active
`active_fault_controls` is explicitly a current diagnostic view. Missing/failed snapshots
are null plus unknown reasons. Exported snapshot size is bounded to 32 MB.

Client `clock_probe` is `{type:"clock_probe",probe_id,browser_sent_ms}`. Server
`clock_pong` is the normal envelope with echoed probe_id/browser_sent_ms,
server_received_ns/server_sent_ns, clock_domain server_monotonic_ns, explicit browser
clock domain, boot ID and receive/send boundary labels. At most 20 probes per session
including legacy pings; duplicate IDs/sequences or overflow refuse as protocol errors.
Server received means the validated probe handler; sent means dispatch inside the
send lock, not remote receipt. No one-way delay or mixed-clock subtraction is computed.

## Accounting and admission boundary source

`ReservationTerms` is the canonical known-price source contract: mode, component,
requested model, USD currency, price version, pricing/capability/enforcement references,
bound_usd, basis, rate_unit=USD_per_unit, rates/usage_bounds/usage_units with identical
nonoverlapping usage paths. Decimal strings are plain finite nonnegative numbers,
up to 18 integer and 18 fractional digits. Live bounds are positive and must cover
the exact sum of declared per-unit rates times maximum usage. No rates are supplied.

Ledger reserve keeps its named arguments and adds optional terms/trial_id. A live
reservation requires terms, trial and boot owner; the explicit mock-zero-v1/no-egress
case remains compatible. Three component attempts per trial/logical turn/arm are
counted durably, including canceled and unknown attempts. `authorize_dispatch` is
the atomic policy point before a transport call; `ensure_dispatch_allowed` rechecks
halt/owner/active reservation. Authorization is not dispatch/remote charge evidence.
`record_sent` records only a returned transport send. Raw attempts retain optional
trial_id/owner_id/reservation_terms/dispatch_authorized/dispatched/started metadata;
unset dispatch observations stay null. Errors before/after a possible charge retain
unknown exposure, not an inferred zero refund.
Dispatch observations retain the exact boundary (WebSocket send return or first TTS
PCM) and transport_source. Injected transports cannot assert a provider dispatch,
returned identity or authoritative usage. Live price-version/component term fingerprints
are immutable within the run; a changed tariff/model/bound requires a new run/cohort.

`settle_usage` computes from the immutable saved terms and retains only priced numeric
counters. Missing/invalid usage stays unknown. Settlement checks over-bound amounts
in reserved, settled AND unknown states under the same write transaction as the
halt/evidence. Late/duplicate usage never changes a terminal row or charges twice.
Late evidence greater than an unknown reservation invalidates its bound and halts
further reservation/dispatch; it does not reconcile that immutable unknown exposure.
The old total_exposure definition therefore stays known settlements plus held
reservations, not a complete bill after a bound violation. Added summary fields:
bound_integrity (invalid/not_disproved), bound_violation_count (observations),
bound_violation_attempt_count, dispatch_halt_active, dispatch_allowed (false/null),
owner_id and snapshot_owner_id. No-halt/null is not an affordability grant. Raw
attempts/events/totals still come from the same ledger read transaction and saved
snapshot codec; there is no parallel cost sum.
price_policy_fingerprints links the saved per-component terms without duplicating totals.

The sole artifact-root startup explicitly recovers a new boot owner. Prior reserved
attempts become immutable unknown/service_restart, retaining exposure. Per-session
constructors cannot reclaim a newer boot's owner; budget mismatch refuses instead
of rewriting history. A live single-owner lease reference remains an operator
attestation, not an independent process/lease proof.

Protected `GET /admission` returns `{schema_version:1,mode,arms:{arm:AdmissionEvidence},
observation_scope:"configuration_only_no_provider_probe",runtime_verified:false}`.
Ready.payload.admission and session evidence.admission use the same canonical
AdmissionEvidence (`admission-evidence-v1.json` is its source reference): mode/run ID,
run-manifest hash; capability/pricing/authorization/rights manifest hashes; requested
models/voice; expected returned models; actually_returned_models/voice observation
objects; budget_usd; budget_owner scope/boot owner/lease reference with observation;
independent_verification=unknown. GET only fingerprints configuration. Before open,
actual identities are null/unknown. Only a successful future default-provider-transport
session.updated supplies observed returned model data; injected transports and TTS/
voice identities not actually returned remain unknown. No keys, raw policies or
provider probe is used to fill metadata. Configuration fingerprints and references
are links/attestations, not independent acceptance.

Live producer source additionally requires tariff usage_bounds/usage_units/
enforcement_reference, capabilities.attempt_bounds_verified, authorization
budget_owner_scope=single_owner_run and owner_lease_reference, and an operator-injected
synchronous attempt_bound_verifier returning literal true on the typed terms/current
sample/outgoing-byte-count/epoch/owner context before each billable call. Byte counts
are not relabeled tokens or prices. This actual verifier and its
provider/session/history enforcement evidence are unbuilt activation prerequisites;
the product supplies no permissive verifier. Existing rights/custody/calibration,
supported TTS, credentials/transports and approved-price/budget prerequisites remain.

Boundary failures return fixed structured HTTP `{detail:{code},error:{code}}`;
WebSocket failures retain their fixed code/outcome envelope. Host syntax is ASCII
loopback only, exact configured frontend Origin requires loopback http plus port,
and nonce is 16..128 URL-safe ASCII characters. Duplicate boundary headers/query
nonces refuse. Artifact paths normalize separators, reject absolute/rooted/drive/
traversal/ADS/reserved names, current root/child symlink/junction/hardlink escapes
and output outside the configured root. Use an operator-owned root with no competing
filesystem writer; atomic protection against concurrent link replacement is not
claimed. Provider errors/usage telemetry omit raw credentials/audio/transcript text.

## Conditional licensed fixed PCM K

`Settings.k_control_config` defaults to None. Enabling K requires explicit live mode,
enabled_arms including K, a pinned run_manifest_hash, and canonical
`LicensedControlConfig` plus a synchronous operator-injected `approval_verifier`.
The serializable spec is referenced by `licensed-control-v1.json`; code never loads
arbitrary callbacks/code/URLs from JSON. No assets, authentic verifier, approval,
rights, prices, provider keys or activation are supplied by this construction.
V/C keep their existing gates; mixed arms require one run ID and equal run budget.
Supplying a licensed control config in mock mode refuses rather than using a tone.

Finite 1..32 strict WAV assets are PCM16LE mono24k, at most192000 frames/8s each,
with exact WAV/PCM hashes and duration. Catalog hash pins `{assets,state_assets}`
using existing sorted compact ensure_ascii JSON plus LF; config_hash uses the same
codec over full model JSON including schema_version, excluding the callable.
Mapping covers exactly actual authority states information, slots_offered,
awaiting_confirmation, booked, complete. Paths are local relative asset names
under an explicit absolute local root; UNC/remote/link/traversal/hardlink/oversize
and incompatible WAV input refuse. Existing catalog read/WAV and security path
helpers are reused. Approval callback receives hash/run-manifest/lease/owner/
expiry/asset-rights context and must return literal true before any asset playback.
Opaque external refs and accepted callbacks are not independent verification.

K reads actual authority state only. It performs no recognition, guessed command,
slot selection or confirmation; F1 remains unchanged. Task payload adds
control_only=true, task_applicability=not_applicable, spoken_correctness=unknown,
code=licensed_fixed_control_no_intent_inference, asset_id,catalog_hash and the actual
authority snapshot. PCM uses common capture/commit/playback/epoch/cancel/reconnect
bounds and validated render ACK backpressure; unknown heard audio is never replayed.
`control_complete` is an additive terminal outcome for completed descriptive render
control, never task/spoken correctness. It requires actual complete render ACK.
Missing intent/proof is not supplied by the clip. No model/STT/controller duration
is invented; model_stages=not_applicable, translation=identity_noop_no_duration.

AdmissionEvidence adds nullable `LicensedControlEvidence`: pinned config/catalog/
asset hashes, capability/auth refs, explicit approval/asset observations, model/task
applicability and cost scope. GET configuration has no asset read/approval/provider
probe. Only actual future open records external_verifier_accepted and verified local
WAV/PCM hashes. Provider requested/returned model maps are empty for this component.
Ledger mode local_control/canned/local-control-zero-v1 declares only no-provider
generation known zero, under shared run owner/halt checks and immutable terms.
License, study and local compute costs remain unknown; this is not a zero total bill.

## Bounded diagnostics phase and exact attempt lineage

Protected `POST /sessions/{id}/calibration` body ONLY `{phase_id,action:begin|end}`,
max512 bytes, has canonical `CalibrationControl`/`CalibrationReceipt` and
`calibration-v1.json`. Begin requires relay listening, no capture and a fully drained
response. Consumer separately proves paused/drained browser state. At most two
distinct phases per session, each capped30s and clipped to original120s session
deadline. No keepalive/capture turn, epoch change, response deadline renewal or
probe success. Capture/commit/interrupt while active refuse. Disconnect/expiry
terminates calibration_invalid. Same-ID retries retain original boundaries and
phase-start epochs; begin after end returns the ended receipt, never renews it.
The HTTP receipt does not change browser state. End explicitly after diagnostics.
VoiceEvent adds calibration_phase with phase_id,phase_action,phase_deadline_ns.
Post-End evidence retains canonical hash-referenced calibration snapshot separately
from raw trace; status active at terminal means incomplete, not successful probes.

Ledger attempts add session_id, transport_turn_id, connection_epoch,response_epoch
alongside trial_id/logical_turn_id/owner_id and lineage_observation:
recorded_at_reservation or unknown. All new adapter reservations bind actual relay
session/current transport and epochs; historical absent fields remain null/unknown.
Non-mock reservations require full lineage. No transport/provider ID is relabeled
as a logical turn. GET evidence.session_attempts projects ONLY recorded exact session
matches from the saved canonical run ledger snapshot; attempt_lineage reports
unresolved_attempt_ids and missing snapshot unknown. Totals/hash/ref remain the
original single atomic ledger snapshot; active observations invent no terminal data.

## Server payload registry v1 (runtime unverified)

`contracts.py` holds one closed payload model per ServerMessage variant the relay
actually emits, enumerated from every `Relay.send` call site and the adapter events
it forwards. `ServerMessageVariant` is the envelope union discriminated on `type`;
`SERVER_MESSAGE_ADAPTER` validates it; `SERVER_PAYLOAD_REGISTRY_VERSION = 1`.
`contracts/server-payloads-v1.json` is the variant-to-model source reference.

| type | payload model | emitted by |
| --- | --- | --- |
| ready | ReadyPayload (admission: AdmissionEvidence; resume adds resume_required:true) | Relay.open, resume |
| state | StatePayload (RelayState; interrupt adds fence_epoch and task) | relay state changes |
| transcript | TranscriptPayload (adapter fields + source) | mock debug text, live provider transcript |
| audio | AudioPayload (adapter fields + synthetic/artifact_hash/content_hash/artifact_persisted) | mock, live V/C, licensed K |
| event | EventPayload, discriminated on `kind`: controller_span, mock_tool_attempt, response_done, cancel.ack, resume.ack, tts_request/tts_first_pcm, repeat_requested | adapters; repeat_requested from relay |
| task | TaskPayload (authority outcome/snapshot, clarification, K control view) | adapter task events, resume snapshot |
| error | ErrorPayload (code, TerminalOutcome, fixed message, terminal:true) | Relay.shutdown on failure |
| ended | EndedPayload (task: TaskPayload or TaskUnknown; TraceSummary; LedgerSummary or null; Snapshot/UnknownArtifact; RetentionSummary; AdmissionEvidence) | Relay.shutdown |
| pong | PongPayload | legacy ping |
| clock_pong | ClockPongPayload | clock_probe |

Adapter-originated payloads (transcript, audio, adapter events) keep the adapter
envelope fields the relay already forwarded: `type`, `sequence` (adapter sequence,
not the wire sequence), `connection_epoch`, `response_epoch` and, where present, `turn_id`
(`AdapterEventFields`). `task` carries only the outcome object, not the adapter event.
`mock_transport_drop` and `mock_provider_error` are consumed by the relay and never sent.

`serialize_server` encodes the generic `ServerMessage` exactly as before, then
validates those bytes against the registry; it never injects defaults, so emitted
bytes are unchanged. Unknown types/fields/values refuse with the fixed code
`server_payload_contract_violation`. Pydantic error text is not forwarded, because it
could quote transcript or PCM. The relay maps that code to `protocol_error`, and a
refused payload uses no wire sequence number. `parse_server` is the closed consumer
parse. Existing readers that call `ServerMessage.model_validate` keep the generic
envelope; nothing about them changes.

Field names and existing values are unchanged. Closed value sets: TaskCode (every
authority/live/K task code), RelayState, error/ended codes as `ServerCode` snake case,
UnknownArtifact and RetentionSummary reasons. F1 (separate selection/confirmation
turns), epoch fences, clock domains (server_monotonic_ns vs browser_performance_ms)
and the K descriptive-control rule (`control_only` cannot claim ok/effect) are kept.
The ready identity must agree with admission run/mode. Ended `ledger` is null exactly
when its artifact is unknown. Ledger attempt/event rows remain raw canonical
accounting rows (`dict`). The registry itself persists nothing. Existing relay
persistence is unchanged: transcript text is wire-only; the relay stores PCM only as
mock diagnostic tone or approved retention artifacts; the task `challenge` stays in
the task journal and its evidence export. (Operator harness asset import is separate.)
Trace/snapshot metadata stays closed (no nonce/auth/headers/text/transcripts/PCM).
When a payload is refused while the trace is open, the relay first records an
`integrity_error` trace row (observation unknown) and marks the trace incomplete,
because a trace row written just before that send may not have been delivered.
Exception: a refused final `ended` send happens after the trace and journal are closed.
That refusal is not durably recorded; the client sees a normal close with no `ended`
frame. Dump failures map to the same fixed refusal code.

AST-generated browser bindings consume these classes as ordinary source models.
Generated bindings and JSON references neither run custom validators nor prove
runtime parity. Dirty Mode leaves every emission unexercised against the registry.

Protected read-only `POST /sessions/{id}/evidence` accepts exactly the two UTF-8
bytes `{}` (no fields, whitespace or alternate JSON values), with a32-byte transport
body bound and2s read cap. It calls the same canonical GET reader, keeping all
session/path/hash/snapshot/error guards and the exact Host/Origin/X-Voicelab-Nonce
boundary. GET is unchanged for harness readers. POST adds no mutation, total,
provider call or origin/CORS relaxation. Browser consumers use same-origin POST and
browser-generated Origin, never attempt to forge the forbidden Origin header.
Invalid body/bound/timeout yields fixed400/413/408 codes, respectively
evidence_read_empty_object_required/evidence_read_body_bound/evidence_read_body_timeout.

## HTTP response registry v1 (runtime unverified)

`contracts.py` holds one closed body model per route outcome.
`HTTP_RESPONSE_MODELS` maps a route key to its model, `HTTP_RESPONSE_REGISTRY_VERSION = 1`,
and `contracts/http-responses-v1.json` is the route-to-model source reference.

| route | success body | notes |
| --- | --- | --- |
| GET /health | HealthResponse | unauthenticated; mode/protocol/live_admission/evidence_label |
| GET /admission | AdmissionResponse (`arms`: configured arm subset to AdmissionEvidence; all three in mock) | protected |
| GET /diagnostics | DiagnosticsResponse (five DiagnosticMetric entries; observed ⇔ value present) | protected |
| GET and POST /sessions/{id}/evidence | SessionEvidenceResponse | protected; one canonical reader; POST body exactly `{}` |
| POST /sessions/{id}/calibration | CalibrationReceipt | already FastAPI response_model |
| POST /sessions/{id}/faults | MockFaultReceipt | already FastAPI response_model |
| GET /sessions/{id}/trace | not JSON: NDJSON bytes plus X-Voicelab-Trace-SHA256 / X-Voicelab-Trace-Complete | unchanged |
| every refusal (403/404/405/408/409/413/400/422/500/503) | HttpErrorBody `{detail:{code},error:{code}}` | identical code, pattern `^[A-Za-z0-9_]{1,96}$` |

Routes return their existing dicts unchanged. `checked(route, body)` validates a JSON
encoding of that same value (`validate_http_response`), so emitted bytes do not change.
A mismatch becomes a fixed 500 `http_response_contract_violation`, with no validation
text. Refusal bodies are built by `http_error_body`: a code outside the pattern
degrades to `internal_error`. The existing handler already turns non-code details
(such as `unknown session` and other phrases with spaces, and Starlette's Not Found)
into `request_refused`, and that is unchanged. Host/Origin/nonce, the exact `{}`
evidence body and the 32/512-byte and 2s bounds are untouched.

SessionEvidenceResponse closes the export: session row (`trace_complete` remains the
stored SQLite 0/1 integer), turns, artifacts (discriminated on `kind`: diagnostic tone,
saved snapshot, retention reference; each with content_hash), admission or null, task
journal (TaskHistory or `{observation:unknown,reason:task_evidence_unavailable}`),
LedgerSummary/FaultSnapshot/CalibrationSnapshot each null exactly when its artifact
reference is unknown (EvidenceMissingArtifact `terminal_snapshot_unavailable`, or
EvidenceUnreadableArtifact `saved_snapshot_missing_or_hash_invalid`, or
`saved_snapshot_schema_unrecognized` when hash-valid bytes predate the current model, as
older ledger snapshots do), optional
active_fault_controls/calibration_phases, raw session_attempts ledger rows and
AttemptLineage. Session identity and trace_reference must agree. Task operation
arguments keep closed keys (`slot_id/challenge/state_version`); their values stay
unknown provider proposals. The evidence export already includes the task
`challenge`, task arguments and approved retention references. It never includes
nonce/auth/headers/transcripts/PCM, and this registry adds no field.
