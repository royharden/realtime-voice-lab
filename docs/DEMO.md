# Development walkthrough and source driver

Source construction only. No run, rehearsal, video, duration, device calibration, benchmark or product acceptance is claimed. Keep MOCK / DEV / diagnostic-tone labels visible. Typed words bypass speech interpretation; tones cannot establish spoken-task correctness or physical acoustic latency.

`voicelab demo` prints the fixed walkthrough: show the fictional task; End then Start a fresh namespace; complete hours, slots, selection, explicit confirmation and status with V, C and K; interrupt before and after booking; explicitly reconnect; export traces and inspect unknowns/all attempts. Reset never deletes or rewrites the task journal. Do not dub tones with speech or insert fabricated values.

## Later operator activation

Dirty Mode forbids executing this driver. After separate authorization, resolve declared dependencies and Chromium, claim/check both ports with agent-ports and launch only the operator-owned frontend and mock relay. The driver starts its own browser only. It does not launch/kill relay or frontend processes, install dependencies, create infrastructure or access providers. Only mock mode and explicit 127.0.0.1 ports 20000â€“29999 are accepted. Nonce comes from VOICELAB_NONCE, never a manifest.

Generate/review the development signal manifest as documented in fixtures/dev/README.md. An activation JSON contains runtime_authorized:true, matching activation_reference, run_id, mode:"mock", split:"dev", frontend_url, relay_url, pinned code_hash and config_hash, fixture_root, relative fixture_manifest, explicit own relay_artifact_root, and two port_claims rows {port,receipt,operator_check_reference}. Claim files are operator checkpoints, not automated ownership proof. Use a new owned evidence directory per run.

Only in that authorized phase:

```
python -m voicelab.harness.cli journey --manifest <activation.json> --activation-reference <reference> --out <new-owned-output>
```

Optional --trial-plan <D5-plan.json> uses D5's canonical plan validator and ordered tasks. Each task has task_id, arm, cohort, logical_turn_ids and bounded fault_schedule actions {turn_position,kind}. Default is one fixed development task per V/C/K arm, not the 40+8 comparison. include_required_faults:true adds the named diagnostic fault schedule for each arm; it does not create a second paired evaluation plan. D5 owns balanced plans and metrics. Each planned task.fixture_id must map through task_fixtures to its own reviewed relative fixture manifest, whose fixture_id, wording_family, speaker, exact turns and hashed microphone WAV match the actual task. Unmapped/mismatched cells are not_started/incomplete; the default diagnostic silence cannot stand for sixteen speaker/family cells. A separate browser is launched per bound task so its actual microphone file matches that binding.

## Faults and evidence

Schedules name permission denial, device loss/mute, actual silence timeout, start/end acquisition race, pre/postcommit cancellation, fresh-tool duplicate, lost rendered ACK, queue overrun, malformed/duplicate protocol and restart. Browser-boundary injection exercises the real UI/worklet/parser and remains explicitly synthetic. Fresh-tool duplicate is armed before the selected proposal via POST /sessions/{ready.session_id}/faults; service_restart applies only to that owned mock session. Requests carry {control_id,kind}, exact Origin and nonce header. mock_fault_policy pins the same operator seed and authorization_reference as the relay. Save the returned armed/applied/refused receipt plus actual journal/trace observations. HTTP acceptance alone proves no effect; absent producer/control evidence remains unknown/incomplete. Simulated restart never kills/restarts a process or proves actual restart recovery. Actual restart/duplicate-effect invariants need journal/trace audit. W7/W8 gates stay closed.

driver.jsonl retains scheduled tasks/turns, actions, separate admission observations, allowlisted wire metadata (no hello nonce, auth headers, base64 PCM or raw transcripts) and missing terminals. No PCM or nonce is persisted. Browser exports disclose their bounded retained window. handoff.json binds artifacts by SHA256; D5 assembles canonical trace/ledger/audio-oracle evidence and metrics. Driver completion is not correctness or acceptance. Browser/server/render clock domains remain separate.

## Calibration and soak source

The following are future operator commands, forbidden during Dirty Mode:

```
voicelab calibrate --manifest <activation.json> --activation-reference <reference> --phase pre --out <new-pre-directory>
voicelab calibrate --manifest <activation.json> --activation-reference <reference> --phase post --out <new-post-directory>
voicelab soak --manifest <activation.json> --activation-reference <reference> --profile full --out <new-full-directory>
voicelab soak --manifest <activation.json> --activation-reference <reference> --profile short --out <new-short-directory>
```

Calibration consumes D6 `diagnosticProbe` on an explicitly admitted synthetic mock graph: thirty two-pulse known-gap repeats per phase, including past scheduling and offset controls, plus a worklet-scheduled cancel sample. It retains graph/probe/sample IDs, actual device/context rates, requested and observed frames, pulse length, cutoff target, raw output-timestamp anchors and uncertainty. Sample-to-performance mapping uses getOutputTimestamp without adding outputLatency twice. A deliberately wrong +100ms mapping must be rejected while the unmodified anchor matches. A new render fence and an actual rejected stale-epoch buffer are separate controls. Missing or mismatched anchors, controls, graph IDs or diagnostic windows keep the phase incomplete. Internal graph timing never establishes physical sound at a listener's ear or independent calibration acceptance.

Full soak owns three Chromium contexts, attempts a fourth while three sessions are admitted, and stops admitting work at 200 logical turns or the 30-minute cap. It schedules 40 targeted interruptions and 10 explicit reconnects; completed effects are counted from fresh browser fence/ACK observations plus the authoritative session trace, separately from attempted actions. Every scheduled session/turn stays visible when the time cap or failure prevents execution. Each lane uses the fixed five-turn development task; this load profile is not the paired V/C speech experiment. Pre/post calibration runs on each lane; session resets create new graph IDs, which remain explicit rather than being treated as the original graph.

The separate short profile uses two contexts, ten five-turn sessions and a two-minute cap. Its `soak-profile.json` artifact and label are distinct, and it can never satisfy the full soak gate. Profile constants have one source in harness/soak.py; future run manifests copy the selected values without claiming execution in advance.

Resource sampling targets two seconds, records actual collection times/gaps, own-relay RSS/files/sockets/tasks/event-loop lag, per-context browser heap, observed API ownership, and both estimated buffers and actual worklet queue anchors. Diagnostic events also retain queue maxima and failures between periodic samples. Missing heap/resource/cleanup evidence, lost diagnostic windows, unobserved effects, queue excursions or insufficient coverage cannot pass. Post-End media/worklet/context/socket/timer ownership must be observed released; OS/device cleanup remains outside this API-level claim. Source never opens or kills another project's process.

EvidenceSink applies a shared closed metadata projection before JSONL and JSON writes, including nested wire data. Dynamic count/heap/reference containers have restricted value shapes; resource samples are distinguished from forbidden PCM arrays. Authoritative trace/ledger bytes are copied only after hash checks and metadata-only validation. Saved run-level ledger snapshots are referenced by content hash and never summed as separate sessions or added to earlier cumulative snapshots. D5 owns canonical evidence conversion, statistical analysis and reporting. All behavior described here remains runtime unverified.
