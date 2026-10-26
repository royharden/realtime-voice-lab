# Mock-only demonstration

The target is a two-minute demonstration of local instrumentation using typed prompts and a device-free real worklet graph with muted diagnostic PCM. This document and `scripts/demo.py` are a storyboard, not a recording or a measured rehearsal. They do not establish real speech, acoustic latency, a live comparison, heldout evaluation or independent product acceptance. No recorded demo is supplied by this change.

## Reproduce the storyboard

From PowerShell, keep the product checkout explicit:

```powershell
$project = '<absolute-product-checkout>'
& "$project/.venv/Scripts/python.exe" "$project/scripts/demo.py" --help
& "$project/.venv/Scripts/python.exe" "$project/scripts/demo.py"
& "$project/.venv/Scripts/python.exe" "$project/scripts/demo.py" --json
& "$project/.venv/Scripts/python.exe" "$project/scripts/demo.py" --json --out '<new-storyboard.json>'
```

Bootstrap the pinned Python environment first if needed, as described in [operations](OPERATIONS.md). The script uses the standard library, starts no listener/browser/provider, reads no fixtures or credential files, and never captures a device. `--out` creates only a new file in an existing parent directory; an existing file is refused. JSON keeps actual duration and recording unset. `scripts/demo.py` is the timed storyboard entrypoint; the installed `voicelab demo` command still prints the older untimed walkthrough and does not record anything.

## Target sequence

Scene boundaries are prospective pacing targets, not measured latencies. If actual interactions overrun, report the achieved duration rather than speed up, splice out failures or claim a two-minute result.

| Target seconds | Visible action | Evidence boundary |
|---|---|---|
| 0–15 | Show MOCK / TYPED / MUTED labels, fictional task and device-free scope. | No microphone/media permission or real device; output is muted. Acoustic latency and speech correctness remain unmeasured. |
| 15–40 | Select V, Start, submit `hours`, `list slots`, `select slot 1`, `yes confirm`, `status`; inspect booking, End and cleanup. | Typed mock journey; displayed task state is not an independent speech oracle. |
| 40–65 | After closed/cleanup, select C and Start a fresh namespace; repeat the same five prompts. | Same task/inventory, different pinned mock pipeline; no latency winner inferred. |
| 65–85 | Use the verified interruption point, show booking preserved after commit, Disconnect, Explicit retry, End and observed cleanup. | Button attempts are not applied-fence/recovery proof. Show unknown/refused recovery truthfully. |
| 85–110 | Show lifecycle trace and protected same-origin post-End metadata, including failures, unknowns and retained-window limit. | Receipt, endpoint, fence and rendered ACK are distinct; no cross-clock subtraction or fabricated percentiles. |
| 110–120 | Show all-attempt mock cost completeness and describe K as a separate no-generation control; close with open gates. | Known zero provider generation is not a live tariff, speech result or affordability claim. |

Use Explicit retry between drained responses only when the UI requires it. Do not Start another arm until the prior session is closed and owned handles are released. End/Start creates a fresh fictional namespace; it never deletes or resets the durable journal. Interruption does not roll back an already committed booking. The 120-second storyboard is not a full soak or the separately planned live paired comparison.

## Launch and capture gate

The exact serving build, source identity and four asset hashes must be frozen before a recording. Build success alone does not approve the scenario. Capture remains pending until independently reviewed V/C interrupt/recovery/End/cleanup evidence covers the new backend, the actual supported synthetic local launch route is tested, and an explicit scoped capture grant is issued. Earlier browser evidence stays bound to its original backend/build; it cannot be relabeled as a new-backend run. Use that approved route verbatim; do not create a permissive PublicReadAccess object, rights certificate or alternative gate bypass to run a fixture driver.

Relay-only remains the default serving mode. The pinned no-probe source candidate implements paired `--web-dist` and `--web-manifest` flags to mount an exact bounded public build after relay routes. The following is the corrected prospective command, not a tested launch approval. Do not execute it until the reviewed source, suitable tests, actual new-backend launch/browser evidence and explicit scoped runtime grant are supplied:

```powershell
$project = '<approved-frozen-server-checkout>'
$env:PYTHONPATH = "$project/src"
$port = <own-claimed-port>
& "$project/.venv/Scripts/python.exe" -m voicelab.cli serve --mode mock --host 127.0.0.1 --port $port --port-claim <real-receipt> --allowed-origin "http://127.0.0.1:$port" --artifact-root <fresh-owned-runtime> --web-dist <exact-device-free-dist> --web-manifest <matching-build-record.json>
```

The same actual port serves UI and relay; do not reuse another owner's claim or manufacture a second receipt. Both web flags are required together and the closed build inventory must match every served asset. Use the existing ephemeral per-launch nonce mechanism without printing or persisting it. `python -m voicelab.cli` is the supported module entrypoint. A separate Vite page is not same-origin evidence hosting. The older default relay-only command and test-only static fixture do not prove this successor launch recipe works. Storyboard generation remains usable without any launch.

Before any authorized listener, claim and immediately check its project-owned loopback port. Use only the assigned local reservation; a receipt file or an environment flag alone is not ownership proof. Do not share another test owner's mutable server, port, journal or browser context. Keep the nonce out of URLs shown in recordings, DOM exports, logs, manifests and command arguments; enter the per-launch nonce through the existing local control. Do not read or supply provider keys.

The device-free successor explicitly labels Mock as TYPED / MUTED. Its intended Start path does not call getUserMedia, enumerate devices or request microphone permission; the real AudioWorklet graph processes internal diagnostic PCM through muted output. Capture must use that exact reviewed build and keep those media APIs forbidden. The older build requested media during Start and is not a substitute, even with fake-device flags. Never select Live or activate the retained real-media path. Muted PCM has no speaker identity and cannot stand in for licensed speech fixtures or heldout cells. A screen recording must disclose its silent output; do not dub speech or imply audible tone playback.

If capture is supported after the gate passes, retain the original local recording and a separate receipt identifying exact source/build, recording mechanism, explicit device-free typed/muted configuration with getUserMedia/device enumeration forbidden and no microphone permission or audible output, observed start/end/duration, interruptions/recovery outcomes, post-End API-handle observations and any incomplete step. Label it MOCK / TYPED / MUTED / DEVICE-FREE throughout. A failed or partial capture is an incomplete artifact, not a successful two-minute demo. Keep outputs under an explicitly owned local artifact directory; publication remains separate.

## Process metrics are intentionally unknown

The no-probe default does not collect OS process metrics or launch a probe/helper.
`rss_bytes`, `open_sockets` and `open_files` remain `value: null`,
`observation: unknown`, with fixed reason `own_process_metrics_disabled` and their
original bytes/count units. This is a contract shape, not an observed measurement.
Show these unknowns honestly if a later authorized demo opens diagnostics; never
turn them into zero, a green resource indicator or an invented growth plot.

Required resource and soak reliability coverage therefore remains incomplete.
Observed event-loop values or released browser API handles do not substitute for
missing OS metrics. The helper feature is deferred, and Linux measurement is queued
only under a future separate review/design/activation. No listener, browser,
short/full soak, container or recording is authorized by this reconciliation.
The storyboard still targets 120 seconds; actual duration and recording remain unset.
See [no-probe diagnostics](OPERATIONS.md#no-probe-diagnostics-and-incomplete-resource-coverage).

## Operational evidence and open work

The accepted offline suite, build, audit and isolated evaluator evidence are documented in [validation](VALIDATION.md); this demo preparation adds no new validation total. The complete scenario matrix, any new short/full soak execution and its coverage must be reported from their own exact candidates and observations. Do not replace the accepted numbers with projected results.

The guarded journey/calibration/soak/cohort tools in [evaluation](EVALUATION.md) still require their genuine fixture/read/rights and activation inputs. Their default refusal is not a reason to fabricate authority. A typed UI demonstration does not redeem a private release, provide an independent oracle, certify a licensed K clip or supply live pricing. The full offline/live soak, hardware calibration, real speech/acoustic evaluation, live/provider budget and compatibility, independent custody and final acceptance remain separate open gates. See [limitations](LIMITATIONS.md).
