# Realtime Voice Lab

Compare the full path of a native voice arm (V), a deterministic STT → controller → TTS arm (C), and a canned control (K) on one fictional appointment task. The workbench preserves interruptions, task effects, uncertain timing, retries and all-attempt cost instead of showing only fast successful responses.

**Source construction — runtime unverified.** This repository was built with static checks only. No tests, browser journey, microphone experiment, calibration, soak, provider call, live comparison or independent product acceptance has run. There is no recorded demo or measured result yet. Mock mode uses typed diagnostic text and a short synthetic tone; it cannot establish speech recognition, spoken correctness or physical acoustic latency.

## Keyless quickstart — later activation

These commands describe the source interfaces; they were not executed during construction. After runtime activation is authorized, resolve/install the pinned Python and web dependencies and generate reviewed locks. Claim and check separate loopback relay/frontend ports with agent-ports before starting listeners. Use Python 3.12 and Node with the pinned Vite/TypeScript versions in `web/package.json`.

Set `VOICELAB_NONCE` to an operator-chosen per-launch 16–128 character value and `VOICELAB_ALLOWED_ORIGIN` to the exact frontend origin, for example `http://127.0.0.1:<claimed-frontend-port>`. Optional `VOICELAB_ARTIFACT_ROOT` controls the local owned runtime artifact directory. Do not put provider keys into the browser or repository.

```text
uv run voicelab serve --mode mock --port <claimed-relay-port> --port-claim <existing-claim-receipt> --run-id <frozen-run-id> --run-manifest-hash <manifest-file-sha256> --artifact-root <owned-runtime-root> --allowed-origin http://127.0.0.1:<claimed-frontend-port>
```

In `web/`, set `VOICELAB_WEB_PORT` to the checked frontend port and use `npm run dev` after authorized dependency setup. Enter `ws://127.0.0.1:<claimed-relay-port>/ws` and the launch nonce in the UI. Select an arm, Start, and use the labeled typed stimulus: `hours`, `list slots`, `select slot 1`, `confirm`, `status`. Unknown wording asks clarification. Interrupt, Simulate disconnect, explicit retry, End, task journal and trace inspection are implemented in source. Microphone capture does not transcribe audio in mock mode. See [browser notes](web/README.md).

The source serve flags now pass run identity/manifest hash, owned artifact root and exact origin into server settings; `VOICELAB_NONCE` remains the canonical per-launch nonce. Optional `--mock-fault-policy <operator-approved-policy.json>` loads a bounded mock-only JSON object into `Settings.mock_fault_policy`; absent policy stays disabled. These are source interfaces, not evidence that a listener, policy or experiment has run.

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

The report generator consumes canonical `RunManifest`/`TurnResult` artifacts and ledger summaries. Prespecified `turn_assignments` preserve each scheduled turn's trial/task, arm, network/model and clean/fault membership independently of terminal rows; referenced admission records and ledger attempts preserve known or potential starts. A missing known-started result remains a censored nonattainment inside its original cohort. Missing/partial assignments or unresolved start evidence withhold the all-started headline and mark its denominator incomplete, rather than publish a smaller finite percentile. Receipt and calibrated render estimates stay separate; acceptable-response quantiles censor beyond 10 seconds. Conditional latency includes its n and exclusions. It emits no unconditional winner. Model/network/mode/fault cohorts stay separate; family-clustered paired intervals are exploratory.

Raw receipt/render onset, calibrated render, correctness-qualified conditional latency and all-started clean receipt/render quantiles are separate source outputs, with n and exclusions. Paired C-minus-V differences and family-clustered intervals condition on both qualified responses being attained; missing pairs never become zero. Stage spans require valid same-domain, same-session/boot anchors. Unknown native internals and identity translation get no fabricated duration.

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

`eval.py` writes deterministic `results.json`, `results.jsonl`, `results.md` and `ledger-summary.md` from fixed manifest timestamps and hashed input bytes. It requires canonical schema v1, every scheduled logical turn in `scheduled_turns`, and one supplied row per observed logical turn. Complete all-started headlines additionally require matching `turn_assignments` for every scheduled ID and resolved admission evidence. Each assignment pins `logical_turn_id`, `trial_id`, `arm`, `cohort`, `network_profile`, `model_version`, optional task/family/pair, and an independently referenced admission status. `not_started`/`refused` need an admission evidence reference; scheduling alone proves neither. Ledger terminal/dispatched attempts count as start evidence; reservation-only ambiguity retains a potential start but withholds completeness. Missing rows are explicitly synthesized as missing-terminal diagnostics in assigned cohorts. Conflicting terminal/assignment metadata is rejected. `pair_id` identifies a prespecified paired turn position, not an entire multi-turn task. Cost denominators are optional `manifest.audio` fields `captured_input_seconds`, `rendered_output_seconds`, `elapsed_session_seconds`; absent values stay undefined. No report artifacts have been generated here.

`calibrate.py` analyzes actual saved repeats (30 required) and reports internal error separately from unknown acoustic uncertainty. `soak.py` analyzes saved resource/coverage observations. `audit_split.py` checks each declared family, speaker and interruption dimension separately; it cannot certify independent custody.

`scripts/browser_harness.py calibrate|soak --manifest harness.json --activation-reference <later-approval> --out observations.json` is actual bounded Playwright orchestration source. It requires the optional validation dependencies/browser installation, approved synthetic WAV/hash/rights, two existing port claim receipts, immediately checked registered localhost URLs, and the operator nonce. It does not start relay/frontend services. Calibration drives render-graph repeats, actual application worklet anchors, a deliberately past scheduling control, injected offset rejection and cancel/epoch controls. Soak drives three contexts and a refused fourth attempt, up to 200 attempted turns/30 minutes, interruptions/reconnect attempts and two-second diagnostic sampling. It records actions separately from trace-audited outcomes; unavailable metrics stay unknown. Post-End browser handle and worklet queue proofs still require actual trace/instrumentation audit. See [limitations](docs/LIMITATIONS.md), [operations](docs/OPERATIONS.md), and the [planned demo](docs/DEMO.md).

## Evaluation handoff

Evaluation source prepares the seeded 40-clean/8-fault balanced AB/BA task plan plus separate K controls, validates fixture/license/freeze/custody bindings, and assembles saved trace/ledger/journal/adjudication evidence into canonical reporter inputs. Raw driver events retain observed server/session/transport IDs in a correlation sidecar; attempted actions and DOM completion do not prove server starts, browser latency or spoken correctness. Default CLI assembly supplies no independent oracle/release authority. Root `voicelab run` is a later-activation mock/dev driver composition requiring an exact frozen plan, gate bundle, activation reference, port checks and actual per-task fixture bindings. See [evaluation contracts and commands](docs/EVALUATION.md). No evaluation command has been run.

`eval.py` and root `voicelab eval` now delegate to the same evaluation `report` entrypoint and canonical reporter. Alongside the existing report files, it emits `failures.json`, `numeric-fragments.json`, `README-results.md` and `LIMITATIONS-results.md` from the same report object and exact input hashes. These separate fragments do not silently rewrite this README or product limitations. Optional EvidenceRecord/source-hash inputs bind original trace/audio/ledger/oracle provenance and canonical result hashes. No report or numeric fragment has been generated. Incomplete costs permit only clearly labeled known-part rates, with unknown liability alongside them; no complete cost/affordability winner follows.

## Guarded live source

The default server CLI remains mock-only. Programmatically supplied server settings may select the V/C live source only after `validate_live_config` admits explicit, unexpired authorization/budget, pinned capabilities/session-update hashes/models, prices/enforceable bounds, voice/output rights and independent evaluation references. No such manifests, provider entitlement or approval are supplied here. The live transport source uses allowlisted TLS WebSocket/HTTP endpoints, actual parsers/serializers and reserve-before-dispatch; absent usage retains liability. Native tool-followup generations reserve separately. K refuses live mode until licensed fixed speech is available.

An injected trusted synchronous confirmation verifier must evaluate the current captured input and bind namespace, logical/transport turn, epoch, slot/challenge/version and actual ephemeral PCM digests. Provider text/proposals cannot confirm a booking. No independent verifier is supplied; missing evidence refuses confirmation. Microphone PCM stays bounded in memory and is cleared after commit/cancel/close. Live reconnect terminates `recovery_unknown` when provider history consistency cannot be established. See [provider gate schema](docs/PROVIDERS.md).

## Limits and authorship

The source was authored by AI agents and inspected statically under Roy's explicitly enabled construction mode. Product/runtime readiness, independent fixture custody, licensed spoken assets, provider entitlement/rights/pricing, calibrated offline faults/soak and the authorized live paired comparison remain open. This is not completed M6 or independent product acceptance. No public deployment, remote publication or paid calls are implied.
