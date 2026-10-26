# Mock-only demonstration

The target is a two-minute demonstration of local instrumentation using typed prompts, software fake media and synthetic diagnostic tones. This document and `scripts/demo.py` are a storyboard, not a recording or a measured rehearsal. They do not establish real speech, acoustic latency, a live comparison, heldout evaluation or independent product acceptance. No recorded demo is supplied by this change.

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
| 0–15 | Show MOCK, typed-input and diagnostic-tone labels, fictional task and fake-media scope. | No real microphone or device. Acoustic latency and speech correctness remain unmeasured. |
| 15–40 | Select V, Start, submit `hours`, `list slots`, `select slot 1`, `yes confirm`, `status`; inspect booking, End and cleanup. | Typed mock journey; displayed task state is not an independent speech oracle. |
| 40–65 | After closed/cleanup, select C and Start a fresh namespace; repeat the same five prompts. | Same task/inventory, different pinned mock pipeline; no latency winner inferred. |
| 65–85 | Use the verified interruption point, show booking preserved after commit, Disconnect, Explicit retry, End and observed cleanup. | Button attempts are not applied-fence/recovery proof. Show unknown/refused recovery truthfully. |
| 85–110 | Show lifecycle trace and protected same-origin post-End metadata, including failures, unknowns and retained-window limit. | Receipt, endpoint, fence and rendered ACK are distinct; no cross-clock subtraction or fabricated percentiles. |
| 110–120 | Show all-attempt mock cost completeness and describe K as a separate no-generation control; close with open gates. | Known zero provider generation is not a live tariff, speech result or affordability claim. |

Use Explicit retry between drained responses only when the UI requires it. Do not Start another arm until the prior session is closed and owned handles are released. End/Start creates a fresh fictional namespace; it never deletes or resets the durable journal. Interruption does not roll back an already committed booking. The 120-second storyboard is not a full soak or the separately planned live paired comparison.

## Launch and capture gate

The exact serving build, source identity and four asset hashes must be frozen before a recording. Build success alone does not approve the scenario. Capture remains pending until both the independently verified V/C interrupt/recovery/End/cleanup scenarios and the actual tested synthetic local launch route are available. Use that approved route verbatim; do not create a permissive PublicReadAccess object, rights certificate or alternative gate bypass to run a fixture driver.

The ordinary `voicelab serve --mode mock` command currently starts the relay only. The five accepted browser cases used a test-only static mount on the same relay origin. A separate Vite page is not verified same-origin evidence hosting. Until a tested launch route is supplied, this document does not offer a working capture-server command. The metadata-only storyboard commands above remain usable without that route.

Before any authorized listener, claim and immediately check its project-owned loopback port. Use only the assigned local reservation; a receipt file or an environment flag alone is not ownership proof. Do not share another test owner's mutable server, port, journal or browser context. Keep the nonce out of URLs shown in recordings, DOM exports, logs, manifests and command arguments; enter the per-launch nonce through the existing local control. Do not read or supply provider keys.

A capture browser must be explicitly configured for software fake media before Start. Do not press Start in an ordinary browser under a no-device grant: the UI normally requests microphone permission. The existing test recipe uses Chromium fake-device/fake-UI flags and a team-generated silent WAV; it does not activate a physical microphone. Silence and diagnostic tones have no speaker identity and cannot stand in for licensed speech fixtures or heldout cells. An ordinary screen recording may contain no audio; disclose that rather than dubbing synthetic tones with speech.

If capture is supported after the gate passes, retain the original local recording and a separate receipt identifying exact source/build, recording mechanism, fake-media configuration, observed start/end/duration, interruptions/recovery outcomes, post-End API-handle observations and any incomplete step. Label it MOCK / TYPED INPUT / SYNTHETIC TONE throughout. A failed or partial capture is an incomplete artifact, not a successful two-minute demo. Keep outputs under an explicitly owned local artifact directory; publication remains separate.

## Operational evidence and open work

The accepted offline suite, build, audit and isolated evaluator evidence are documented in [validation](VALIDATION.md); this demo preparation adds no new validation total. The complete scenario matrix, any new short/full soak execution and its coverage must be reported from their own exact candidates and observations. Do not replace the accepted numbers with projected results.

The guarded journey/calibration/soak/cohort tools in [evaluation](EVALUATION.md) still require their genuine fixture/read/rights and activation inputs. Their default refusal is not a reason to fabricate authority. A typed UI demonstration does not redeem a private release, provide an independent oracle, certify a licensed K clip or supply live pricing. The full offline/live soak, hardware calibration, real speech/acoustic evaluation, live/provider budget and compatibility, independent custody and final acceptance remain separate open gates. See [limitations](LIMITATIONS.md).
