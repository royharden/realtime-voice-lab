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

## Proposed next-package mock fault seam (not active in CORE)

After a separate FAULTS dispatch: `POST /sessions/{session_id}/faults` with exact
Host/Origin and X-Voicelab-Nonce. Require an operator-enabled mock-only Settings
fault policy containing a fixed seed and authorization reference; refuse live mode
and absent policy. Never accept arbitrary provider payloads, paths or executable text.

Proposed body: `{control_id,kind}`. `control_id` follows Identifier grammar. Closed
kinds: late_pcm, duplicate_pcm, reordered_pcm, lost_ack, drop_before_commit,
drop_after_commit, fresh_tool_duplicate, service_restart, provider_error,
missing_audio, missing_transcript, queue_overrun. Timing is fixed by kind: PCM faults
at next output, lost_ack at next rendered ACK, before/after commit at the authority
effect seam, fresh tool duplicate at the next task proposal, named restart/error now.
Reject invalid/finished targets; replaying an identical control_id returns its original
receipt, conflicting reuse refuses. Seed-derived parameters are recorded in the
receipt. Proposed response includes control_id, kind, seed, status
(armed/applied/refused), `simulation:true`, session/run/trial/epoch identity and
`effect` (the relay/provider-control boundary actually affected).

Simulated service_restart terminates only the target owned mock session with that
named outcome; it neither kills nor restarts a process and cannot stand in for an
actual restart observation. Fresh-tool duplication must traverse the real mock
proposal parser/authority seam if adapters are released to the producer; otherwise
report the narrower simulation boundary explicitly. No silent playback replay.
Post-End evidence remains available through the protected metadata GET, while new
controls after End refuse. Driver scheduling does not prove an applied fault; save
the receipt plus actual observer/wire/journal results and retain unknown/missing effects.
