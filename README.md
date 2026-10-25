# Realtime Voice Lab

Compare the full path of a native voice arm (V), a deterministic STT → controller → TTS arm (C), and a canned control (K) on one fictional appointment task. The workbench preserves interruptions, task effects, uncertain timing, retries and all-attempt cost instead of showing only fast successful responses.

**Offline validation in progress.** The pinned Python environment installs reproducibly, all 51 baseline tests collect, and 48 non-browser tests pass (see [validation](docs/VALIDATION.md)). Browser journeys, Docker, microphone experiments, device calibration, soak, provider calls, live comparison and independent product acceptance are not established by those checks. There is no recorded demo or measured latency result here. Mock mode uses typed diagnostic text and a short synthetic tone; it cannot establish speech recognition, spoken correctness or physical acoustic latency.

## Implemented source

The source below was originally built under static checks. The executed Python baseline covers selected invariants; it does not validate every feature or replace the remaining integration and acceptance gates.

- **Arms:** keyless V/C/K mocks; guarded live V/C source; conditional licensed fixed-PCM K source (live mode, explicit config, external verifier). No licensed assets, rights, verifier or approval are supplied.
- **Protocol and gates:** bounded protocol v1 with a closed server payload registry (`contracts/server-payloads-v1.json`, producer-boundary validation); live/K configuration gates; protected HTTP calibration phases.
- **Task authority and lifecycle:** SQLite TaskAuthority with confirmation challenges; relay lifecycle, deadlines, cancellation, epochs, reconnect and recovery.
- **Audio:** browser capture/resampling/endpointing, fences, onset and render source.
- **Accounting:** reserve-before-dispatch all-attempt ledger with reservation-time attempt lineage, bound halts and exposure.
- **Reports:** deterministic denominator-preserving reports, including K/admission/lineage limitations (generator version 2).
- **Evaluation:** planning, gates, assembly, custody, adjudication, exposure, sealed reproduction and metadata-only retention planning.
- **Harness drivers:** browser journey, cohort/catalog/admission, calibration and soak orchestration (Playwright source).
- **Web:** generated bindings and a protected post-End session evidence inspector.
- **Tests:** `tests/` holds regression, wire, local-boundary and keyless-journey contracts. The 48-test Python baseline passes; the three actual UI tests require separate browser/build/port evidence.

Executed checks and reproduction steps are in [validation](docs/VALIDATION.md). Broader limitations and the live/rights/acceptance gates remain in [limitations](docs/LIMITATIONS.md).

## Keyless quickstart

From PowerShell 7, use the product worktree's absolute path. Install uv 0.11.19 separately, then run the local bootstrap (executed successfully in the environment candidate):

```powershell
$project = '<absolute-product-worktree>'
& "$project/scripts/bootstrap.ps1"
& "$project/.venv/Scripts/python.exe" -m voicelab.cli --help
& "$project/.venv/Scripts/python.exe" -m pytest "$project/tests" -c "$project/pyproject.toml" --ignore="$project/tests/browser/test_ui_journey.py" -q
```

Bootstrap installs CPython 3.12.13 locally and synchronizes `uv.lock` with test/validation extras. It starts no service and installs no browser binary. Keep the working directory at the outer shell. See [operations](docs/OPERATIONS.md) for full CLI flags and safe refusal checks.

The following listener/UI sequence remains an operator recipe pending joined browser evidence. Claim and immediately check separate loopback relay/frontend ports with agent-ports before starting listeners. For PB-M6-R03 use only M6-owned ports 22300–22399. Web dependency setup/build belongs to the web owner; use its reviewed pinned dependencies and build evidence. Do not assume the Python bootstrap installs Node packages or Chromium.

Set `VOICELAB_NONCE` to an operator-chosen per-launch 16–128 character URL-safe ASCII value and `VOICELAB_ALLOWED_ORIGIN` to the exact frontend origin, for example `http://127.0.0.1:<claimed-frontend-port>`. Supply an absolute owned runtime directory with `--artifact-root`. Do not put provider keys into the browser or repository.

```powershell
& "$project/.venv/Scripts/python.exe" -m voicelab.cli serve --mode mock --port <claimed-relay-port> --port-claim <existing-claim-receipt> --run-id <frozen-run-id> --run-manifest-hash <manifest-file-sha256> --artifact-root <absolute-owned-runtime-root> --allowed-origin http://127.0.0.1:<claimed-frontend-port>
```

Set `VOICELAB_WEB_PORT` to the checked frontend port and run `npm --prefix "$project/web" run dev` after authorized dependency setup. Enter `ws://127.0.0.1:<claimed-relay-port>/ws` and the launch nonce in the UI. Select an arm, Start, and use the labeled typed stimulus: `hours`, `list slots`, `select slot 1`, `confirm`, `status`. Unknown wording asks clarification. Interrupt, Simulate disconnect, explicit retry, End, task journal and trace inspection are implemented in source. Microphone capture does not transcribe audio in mock mode. The saved-evidence inspector requires same-origin serving; a separate Vite origin can display an incomplete-serving refusal. The browser tests use test-only same-origin composition; this recipe does not claim that production CLI composition exists or has passed. See [browser notes](web/README.md).

The serve flags pass run identity/manifest hash, owned artifact root and exact origin into server settings; `VOICELAB_NONCE` remains the canonical per-launch nonce. `--artifact-root` is the actual root CLI input; do not rely on `VOICELAB_ARTIFACT_ROOT` to override it. Optional `--mock-fault-policy <operator-approved-policy.json>` loads a bounded mock-only JSON object into `Settings.mock_fault_policy`; absent policy stays disabled. CLI help/refusal checks do not establish that a listener, policy or experiment has run.

Expected source behavior is a guarded task update and diagnostic tone for each admitted typed request, with no provider egress. This paragraph is an implementation description, not actual example execution output.

## Architecture

The browser captures actual-rate audio, resamples to mono PCM16/24 kHz, applies local endpointing and fences playback by connection/response epochs. A loopback FastAPI WebSocket relay validates bounded protocol v1 messages, drives an arm session, records trace events and delegates every task effect to SQLite TaskAuthority. TaskAuthority alone issues confirmation challenges and permits one fictional booking per trial.

V mock uses a documented native wire parser subset and synthetic tool proposals. C mock traverses a transcription-event parser, the conservative lexical controller and the PCM TTS decoder. K bypasses generation and returns the same diagnostic control tone. All share task, relay, capture and playback source. The native mock's lexical behavior is not evidence about native model reasoning. English identity translation is a no-op; it gets no invented duration. See [architecture](docs/ARCHITECTURE.md) and [provider boundaries](docs/PROVIDERS.md).

## Baseline and results

| Evidence | V | C | K |
|---|---|---|---|
| Keyless source path | Implemented; full UI journey pending | Implemented; full UI journey pending | Implemented; full UI journey pending |
| Receipt/render p50/p95 | Not yet measured | Not yet measured | Not yet measured |
| Spoken-task correctness | Unknown; tone is not speech | Unknown; tone is not speech | Not applicable (control); spoken correctness unknown |
| Cancellation/recovery/soak | Runtime unverified | Runtime unverified | Runtime unverified |
| Provider compatibility/live cost | Guarded source; unverified | Guarded source; unverified | No provider dispatch |

The report generator consumes canonical `RunManifest`/`TurnResult` artifacts and ledger summaries. Prespecified `turn_assignments` preserve each scheduled turn's trial/task, arm, network/model and clean/fault membership independently of terminal rows; referenced admission records and ledger attempts preserve known or potential starts. A missing known-started result remains a censored nonattainment inside its original cohort. Missing/partial assignments or unresolved start evidence withhold the all-started headline and mark its denominator incomplete, rather than publish a smaller finite percentile. Receipt and calibrated render estimates stay separate; acceptable-response quantiles censor beyond 10 seconds. Conditional latency includes its n and exclusions. It emits no unconditional winner. Model/network/mode/fault cohorts stay separate; family-clustered paired intervals are exploratory.

Raw receipt/render onset, calibrated render, correctness-qualified conditional latency and all-started clean receipt/render quantiles are separate source outputs, with n and exclusions. Paired C-minus-V differences and family-clustered intervals condition on both qualified responses being attained; missing pairs never become zero. Stage spans require valid same-domain, same-session/boot anchors. Unknown native internals and identity translation get no fabricated duration.

## All-attempt cost

SQLite reservations occur atomically before each possible attempt, including separate C STT/TTS component attempts. Mock requests have an explicit zero-provider basis. Unknown and interrupted reservations remain exposure; duplicate/late usage becomes append-only evidence and cannot refund an immutable unknown. A bound violation halts subsequent admission. Known cost, unknown exposure and estimated components stay separate; rates use captured input, rendered output and elapsed session denominators, with zero denominators undefined. No live budget is approved and no current provider tariff is certified.

## Artifact analysis and access gates

Inspect the actual installed interfaces with these non-listening help commands:

```powershell
& "$project/.venv/Scripts/python.exe" -m voicelab.cli eval --help
& "$project/.venv/Scripts/python.exe" -m voicelab.cli run --help
& "$project/.venv/Scripts/python.exe" -m voicelab.cli calibrate --help
& "$project/.venv/Scripts/python.exe" -m voicelab.cli soak --help
& "$project/.venv/Scripts/python.exe" -m voicelab.cli audit-split --help
& "$project/.venv/Scripts/python.exe" -m voicelab.cli demo --help
```

The report generator can write deterministic `results.json`, `results.jsonl`, `results.md` and `ledger-summary.md` from authorized inputs. Direct `eval --manifest ... --rows ... --ledger ... --out ...` is deliberately refused because the CLI has no actual read verifier. `--bundle` is also subject to PublicReadAccess and external rights/custody authority; supplying a path or hash cannot grant access. A saved-artifact CLI report remains blocked until genuine authority exists. Inline team-authored public-development fixtures can exercise the pure report APIs through supported dependency injection; they are not a bypass or live/heldout evidence.

Canonical reports require schema v1, every scheduled logical turn in `scheduled_turns`, and one supplied row per observed logical turn. Complete all-started headlines additionally require matching `turn_assignments` for every scheduled ID and resolved admission evidence. Each assignment pins `logical_turn_id`, `trial_id`, `arm`, `cohort`, `network_profile`, `model_version`, optional task/family/pair, and an independently referenced admission status. `not_started`/`refused` need an admission evidence reference; scheduling alone proves neither. Ledger terminal/dispatched attempts count as start evidence; reservation-only ambiguity retains a potential start but withholds completeness. Missing rows are explicitly synthesized as missing-terminal diagnostics in assigned cohorts. Conflicting terminal/assignment metadata is rejected. `pair_id` identifies a prespecified paired turn position, not an entire multi-turn task. Cost denominators are optional `manifest.audio` fields `captured_input_seconds`, `rendered_output_seconds`, `elapsed_session_seconds`; absent values stay undefined. No saved-artifact report is claimed by the environment baseline.

`calibrate.py` and `soak.py` delegate to guarded browser harness operations. Calibration requires `--manifest`, `--activation-reference`, `--out` and `--phase pre|post`; soak uses the first three plus optional `--profile full|short`. They do not accept `--observations`. A manifest or activation string alone does not supply missing rights/read authority, fixture approval or claimed listeners. `audit_split.py` checks each declared family, speaker and interruption dimension separately only through the authorized read context; it cannot certify independent custody.

`scripts/browser_harness.py` exposes those same guarded calibration/soak interfaces (including required calibration `--phase`). It requires the optional validation dependencies/browser installation, approved synthetic WAV/hash/rights, two existing port claim receipts, immediately checked registered localhost URLs, and the operator nonce. It does not start relay/frontend services. Calibration source drives render-graph repeats, actual application worklet anchors, a deliberately past scheduling control, injected offset rejection and cancel/epoch controls. Soak source drives three contexts and a refused fourth attempt, up to 200 attempted turns/30 minutes, interruptions/reconnect attempts and two-second diagnostic sampling. It records actions separately from trace-audited outcomes; unavailable metrics stay unknown. Post-End browser handle and worklet queue proofs still require actual trace/instrumentation audit. See [limitations](docs/LIMITATIONS.md), [operations](docs/OPERATIONS.md), and the [planned demo](docs/DEMO.md).

## Evaluation handoff

Evaluation source prepares the seeded 40-clean/8-fault balanced AB/BA task plan plus separate K controls, validates fixture/license/freeze/custody bindings, and assembles saved trace/ledger/journal/adjudication evidence into canonical reporter inputs. Raw driver events retain observed server/session/transport IDs in a correlation sidecar; attempted actions and DOM completion do not prove server starts, browser latency or spoken correctness. Default CLI assembly supplies no independent oracle/release authority. Root `voicelab run` is a later-activation mock/dev driver composition requiring an exact frozen plan, gate bundle, activation reference, port checks and actual per-task fixture bindings. See [evaluation contracts and commands](docs/EVALUATION.md). CLI help and direct-report refusal were checked; no evaluation driver or saved-artifact report was run in this slice.

`eval.py` and root `voicelab eval` now delegate to the same evaluation `report` entrypoint and canonical reporter. Alongside the existing report files, it emits `failures.json`, `numeric-fragments.json`, `README-results.md` and `LIMITATIONS-results.md` from the same report object and exact input hashes. These separate fragments do not silently rewrite this README or product limitations. Optional EvidenceRecord/source-hash inputs bind original trace/audio/ledger/oracle provenance and canonical result hashes. No report or numeric fragment has been generated. Incomplete costs permit only clearly labeled known-part rates, with unknown liability alongside them; no complete cost/affordability winner follows.

## Guarded live source

The default server CLI remains mock-only. Programmatically supplied server settings may select the V/C live source only after `validate_live_config` admits explicit, unexpired authorization/budget, pinned capabilities/session-update hashes/models, prices/enforceable bounds, voice/output rights and independent evaluation references. No such manifests, provider entitlement or approval are supplied here. The live transport source uses allowlisted TLS WebSocket/HTTP endpoints, actual parsers/serializers and reserve-before-dispatch; absent usage retains liability. Native tool-followup generations reserve separately. K in live mode requires an explicit licensed fixed-PCM control configuration, local approved WAV assets and an operator-injected external approval verifier; none is supplied, so K live stays closed. K output is a descriptive receipt/render control (`control_complete`), never task, spoken or booking success, and its known zero covers only the no-provider-generation component.

An injected trusted synchronous confirmation verifier must evaluate the current captured input and bind namespace, logical/transport turn, epoch, slot/challenge/version and actual ephemeral PCM digests. Provider text/proposals cannot confirm a booking. No independent verifier is supplied; missing evidence refuses confirmation. Microphone PCM stays bounded in memory and is cleared after commit/cancel/close. Live reconnect terminates `recovery_unknown` when provider history consistency cannot be established. See [provider gate schema](docs/PROVIDERS.md).

## Limits and authorship

The source was authored by AI agents, initially inspected under static-only construction, then exercised by the bounded Python baseline documented above. Independent review and combined-tree verification remain separate. Product/runtime readiness, independent fixture custody, licensed spoken assets, provider entitlement/rights/pricing, calibrated offline faults/soak and the authorized live paired comparison remain open. This is not completed M6 or independent product acceptance. No public deployment, remote publication or paid calls are implied.
