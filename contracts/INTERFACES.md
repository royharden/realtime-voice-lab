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
