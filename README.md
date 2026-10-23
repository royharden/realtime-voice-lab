# Realtime Voice Lab

Compare the full path of a native voice arm (V), a deterministic STT → controller → TTS arm (C), and a canned control (K) on one fictional appointment task. The workbench preserves interruptions, task effects, uncertain timing, retries and all-attempt cost instead of showing only fast successful responses.

**Source construction — runtime unverified.** This repository was built with static checks only. No tests, browser journey, microphone experiment, calibration, soak, provider call, live comparison or independent product acceptance has run. There is no recorded demo or measured result yet. Mock mode uses typed diagnostic text and a short synthetic tone; it cannot establish speech recognition, spoken correctness or physical acoustic latency.

## Keyless quickstart — later activation

These commands describe the source interfaces; they were not executed during construction. After runtime activation is authorized, resolve/install the pinned Python and web dependencies and generate reviewed locks. Claim and check separate loopback relay/frontend ports with agent-ports before starting listeners. Use Python 3.12 and Node with the pinned Vite/TypeScript versions in `web/package.json`.

Set `VOICELAB_NONCE` to an operator-chosen per-launch 16–128 character value and `VOICELAB_ALLOWED_ORIGIN` to the exact frontend origin, for example `http://127.0.0.1:<claimed-frontend-port>`. Optional `VOICELAB_ARTIFACT_ROOT` controls the local owned runtime artifact directory. Do not put provider keys into the browser or repository.

```text
uv run voicelab serve --mode mock --port <claimed-relay-port> --port-claim <existing-claim-receipt>
```

In `web/`, set `VOICELAB_WEB_PORT` to the checked frontend port and use `npm run dev` after authorized dependency setup. Enter `ws://127.0.0.1:<claimed-relay-port>/ws` and the launch nonce in the UI. Select an arm, Start, and use the labeled typed stimulus: `hours`, `list slots`, `select slot 1`, `confirm`, `status`. Unknown wording asks clarification. Interrupt, Simulate disconnect, explicit retry, End, task journal and trace inspection are implemented in source. Microphone capture does not transcribe audio in mock mode. See [browser notes](web/README.md).

Expected source behavior is a guarded task update and diagnostic tone for each admitted typed request, with no provider egress. This paragraph is an implementation description, not actual example execution output.

## Architecture

The browser captures actual-rate audio, resamples to mono PCM16/24 kHz, applies local endpointing and fences playback by connection/response epochs. A loopback FastAPI WebSocket relay validates bounded protocol v1 messages, drives an arm session, records trace events and delegates every task effect to SQLite TaskAuthority. TaskAuthority alone issues confirmation challenges and permits one fictional booking per trial.

V mock uses a documented native wire parser subset and synthetic tool proposals. C mock traverses a transcription-event parser, the conservative lexical controller and the PCM TTS decoder. K bypasses generation and returns the same diagnostic control tone. All share task, relay, capture and playback source. The native mock's lexical behavior is not evidence about native model reasoning. English identity translation is a no-op; it gets no invented duration. See [architecture](docs/ARCHITECTURE.md) and [provider boundaries](docs/PROVIDERS.md).

## Baseline and results

| Evidence | V | C | K |
|---|---|---|---|
| Keyless source path | Implemented; unexecuted | Implemented; unexecuted | Implemented; unexecuted |
| Receipt/render p50/p95 | Not yet measured | Not yet measured | Not yet measured |
| Spoken-task correctness | Unknown; tone is not speech | Unknown; tone is not speech | Unknown; tone is not speech |
| Cancellation/recovery/soak | Runtime unverified | Runtime unverified | Runtime unverified |
| Provider compatibility/live cost | Guarded source; unverified | Guarded source; unverified | No provider dispatch |

The report generator consumes canonical `RunManifest`/`TurnResult` artifacts and ledger summaries, retains the scheduled denominator and missing-terminal records, separates receipt from calibrated render estimates, and censors all-started acceptable-response quantiles beyond 10 seconds. Conditional latency includes its n and exclusions. It emits no unconditional winner. Model/network/mode/fault cohorts stay separate; family-clustered paired intervals are exploratory.

## All-attempt cost

SQLite reservations occur atomically before each possible attempt, including separate C STT/TTS component attempts. Mock requests have an explicit zero-provider basis. Unknown and interrupted reservations remain exposure; duplicate/late usage becomes append-only evidence and cannot refund an immutable unknown. A bound violation halts subsequent admission. Known cost, unknown exposure and estimated components stay separate; rates use captured input, rendered output and elapsed session denominators, with zero denominators undefined. No live budget is approved and no current provider tariff is certified.

## Artifact analysis commands — later use

These analysis scripts consume saved approved synthetic observations. The local package must first be installed in an authorized environment. A separate explicitly activated browser driver is described below; nothing here has been executed.

```text
uv run python scripts/eval.py --manifest run.json --rows turns.jsonl --ledger ledger.json --out results
uv run python scripts/calibrate.py --observations calibration-repeats.json --out calibration-analysis.json
uv run python scripts/soak.py --observations soak-observations.json --out soak-analysis.json
uv run python scripts/audit_split.py --manifest split-manifest.json
uv run python scripts/demo.py
```

`eval.py` writes deterministic `results.json`, `results.jsonl`, `results.md` and `ledger-summary.md` from fixed manifest timestamps and hashed input bytes. It requires canonical schema v1, every scheduled logical turn in `scheduled_turns`, and one supplied row per observed logical turn. Missing scheduled rows are explicitly synthesized as missing-source diagnostics, never successful observations. `pair_id` identifies a prespecified paired turn position, not an entire multi-turn task. Cost denominators are optional `manifest.audio` fields `captured_input_seconds`, `rendered_output_seconds`, `elapsed_session_seconds`; absent values stay undefined. No report artifacts have been generated here.

`calibrate.py` analyzes actual saved repeats (30 required) and reports internal error separately from unknown acoustic uncertainty. `soak.py` analyzes saved resource/coverage observations. `audit_split.py` checks each declared family, speaker and interruption dimension separately; it cannot certify independent custody.

`scripts/browser_harness.py calibrate|soak --manifest harness.json --activation-reference <later-approval> --out observations.json` is actual bounded Playwright orchestration source. It requires the optional validation dependencies/browser installation, approved synthetic WAV/hash/rights, two existing port claim receipts, immediately checked registered localhost URLs, and the operator nonce. It does not start relay/frontend services. Calibration drives render-graph repeats, actual application worklet anchors, a deliberately past scheduling control, injected offset rejection and cancel/epoch controls. Soak drives three contexts and a refused fourth attempt, up to 200 attempted turns/30 minutes, interruptions/reconnect attempts and two-second diagnostic sampling. It records actions separately from trace-audited outcomes; unavailable metrics stay unknown. Post-End browser handle and worklet queue proofs still require actual trace/instrumentation audit. See [limitations](docs/LIMITATIONS.md), [operations](docs/OPERATIONS.md), and the [planned demo](docs/DEMO.md).

## Guarded live source

The default CLI remains mock-only. Programmatically supplied server settings may select the V/C live source only after `validate_live_config` admits explicit, unexpired authorization/budget, pinned capabilities/session-update hashes/models, prices/enforceable bounds, voice/output rights and independent evaluation references. No such manifests, provider entitlement or approval are supplied here. The live transport source uses allowlisted TLS WebSocket/HTTP endpoints, actual parsers/serializers and reserve-before-dispatch; absent usage retains liability. Native tool-followup generations reserve separately. K refuses live mode until licensed fixed speech is available.

An injected trusted synchronous confirmation verifier must evaluate the current captured input and bind namespace, logical/transport turn, epoch, slot/challenge/version and actual ephemeral PCM digests. Provider text/proposals cannot confirm a booking. No independent verifier is supplied; missing evidence refuses confirmation. Microphone PCM stays bounded in memory and is cleared after commit/cancel/close. Live reconnect terminates `recovery_unknown` when provider history consistency cannot be established. See [provider gate schema](docs/PROVIDERS.md).

## Limits and authorship

The source was authored by AI agents and inspected statically under Roy's explicitly enabled construction mode. Product/runtime readiness, independent fixture custody, licensed spoken assets, provider entitlement/rights/pricing, calibrated offline faults/soak and the authorized live paired comparison remain open. This is not completed M6 or independent product acceptance. No public deployment, remote publication or paid calls are implied.
