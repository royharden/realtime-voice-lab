# Validation

The initial source was authored under static-only construction. Normal-mode validation began in PB-M6-R03 on 2026-10-25. Environment validation uses uv 0.11.19 and project-local CPython 3.12.13 with the exact, hash-bearing `uv.lock`. Collection found 51 tests. Runtime results below apply only to the stated baseline, not live providers, heldout evaluation or product acceptance.

### Executed baseline (PB-M6-R03, D8 environment candidate)

On Windows, PowerShell 7, source base `07a471f27db7ab86dc9f72143d9050751a2b919c`:

| Check | Actual result |
|---|---|
| Bootstrap with test + validation extras | 26 packages installed; exit 0 |
| Second, initially absent clean environment, same lock | 26 packages installed; exit 0; `uv pip check`: all compatible |
| Full pytest collection | 51 collected; exit 0 |
| Baseline excluding only `tests/browser/test_ui_journey.py` | JUnit: 48 tests, 0 failures, 0 errors, 0 skipped; exit 0 |
| Bootstrap repeat | Existing environment checked successfully; exit 0 |
| Missing-lock negative control in isolated scratch directory | Refused before Python installation, as intended |
| `uv lock --check` | 26 packages resolved against unchanged lock; exit 0 |

The baseline has one Starlette deprecation warning for `anyio.abc.BlockingPortal`. This is not a failure or a reason to alter the inherited test suite. Local JUnit and command output are retained in `.agent-work/environment-r1/`. No browser binaries, listeners, containers or paid/provider calls were started for these checks. These results establish only the inherited baseline with this environment; new reviewer tests and the integrated candidate require separate execution.

## Regression contracts (`tests/`)

A small set of pytest regression tests guards the plan's critical invariants. They call the actual public APIs with synthetic development-only data, use temporary SQLite files, and make no network calls. Each test's docstring opens with `what_bug_this_catches` and names the source rule it guards.

| File | Invariant | Source rule |
|---|---|---|
| `tests/core/test_task_confirmation.py` | Select and confirm in the same logical turn are refused and create no booking. A distinct explicit confirmation books exactly once, and replaying it returns a duplicate. Confirmation without an explicit affirmative is refused. | `core/task.py` `TaskAuthority.execute`; plan §2; F1 |
| `tests/reporting/test_report_denominators.py` | 18 clean successes plus 1 missing-but-known-started turn give n=19 and a p95 marked not attained. If a turn's cohort assignment or start evidence is unknown, p50/p95 are withheld and the cohort is marked incomplete. | `reporting/reports.py` `build_report`, `quantile`; plan M6-04; F2 |
| `tests/accounting/test_ledger_exposure.py` | Late over-bound usage after an attempt is already unknown halts admission. It never releases the reserved exposure and never settles twice. Five concurrent 0.40 reservations against a 1.00 budget admit exactly two. | `accounting/ledger.py` `AttemptLedger`; plan §7 |
| `tests/harness/test_privacy_projection.py` | Nested or JSON-string payloads, headers, nonce, auth, transcript, text, PCM and unknown vendor fields are dropped before anything is persisted. | `harness/privacy.py` `observed_metadata` |
| `tests/evaluation/test_identity_refusals.py` | These mismatches are refused, never relabeled: trial or arm conflicts with the planned assignment, rows from another run, a licensed-K catalog hash mismatch, a returned model identity claimed without an observation. A matching K declaration stays `configuration_only_not_verified`. | `reporting/reports.py`, `adapters/licensed.py` `validate_k_config`, `contracts.py` `ReturnedIdentity` |
| `tests/adapters/test_wire_parsing.py` | The synthetic `fixtures/wire/` success, fault and transcription lines go through the real V/C parsers. Provider error text is sanitized and the rate-limit code is kept. Cancel/truncate serialize the rendered cutoff (960 samples = 40 ms). A cancelled or empty response with missing usage becomes unknown liability at the full reservation, never zero. Observed zero usage settles at zero. Malformed frames raise fixed protocol errors. | `adapters/providers.py`, `accounting/ledger.py` `settle_usage`; plan W2 |
| `tests/security/test_local_boundary.py` | The protected evidence POST refuses a non-loopback Host (middleware), a missing or wrong Origin, a missing, wrong or malformed nonce, bodies over 32 bytes (413) and any body other than exactly `{}` (400). A valid request reaches the canonical reader and gets a fixed 404 for an unknown session. GET has the same boundary. | `server/app.py` `authorize`, `browser_session_evidence`; `security/__init__.py`; plan W4 |
| `tests/integration/test_mock_journey.py` | One keyless V-mock five-turn journey through the real websocket relay: hours, slots, select, distinct confirmation, status. It books once, ends `success`, and the saved terminal, turns, task journal, zero-cost all-attempt ledger and session attempts are read back through the evidence POST. Provider, transport and outbound-TCP entry points stay unused, and the credential sentinel and nonce never appear in output. Fault row: a commit with a stale epoch is refused as a terminal `protocol_error` with no turn or operation, and an old-epoch tool callback is refused after the authority's fence advances. | `server/app.py` relay, `adapters/sessions.py` `MockArmSession`, `core/session.py`, `core/task.py`; plan W5/M6-05 |
| `tests/browser/test_calibration_report.py` | Synthetic worklet anchors go through `harness/calibration.gap_row` (monotonic mapping, +100 ms injected offset rejected) and `reporting/diagnostics.calibration_report`. All 30 raw per-repeat errors are emitted. One 50 ms repeat fails the device by name even though the sample SD stays under 15 ms (rejected, not averaged in). Unobserved repeats and short runs are incomplete, never passed. No browser needed. | `harness/calibration.py`, `reporting/diagnostics.py`; plan W3 |
| `tests/browser/test_ui_journey.py` | The real built `web/` UI in Playwright Chromium (fake media devices) against the mock relay on a claimed port. A typed mock turn completes, End releases to `closed`, and the post-End inspector reads saved metadata through the same-origin POST with unknowns preserved. The nonce never appears in the page URL, DOM, console or trace export, and no off-origin request is made. Barge-in on the booking-commit task advances the worklet fence while the booking is kept, and a stale-epoch frame is discarded without moving the fence. A UI served from a different origin (localhost vs relay 127.0.0.1) shows the visible incomplete-serving refusal, never POSTs evidence, and gets no CORS read. | `web/src/main.ts`, `web/src/session-evidence.ts`, `web/public/audio-worklet.js`; harness `probe.js`, `journey.py`, `browser.py`; plan W3 |

`tests/conftest.py` holds the shared synthetic builders. `src/` is importable through `[tool.pytest.ini_options] pythonpath`.

### Reproduce the locked environment

From PowerShell 7, run the script by its absolute path (the current directory can remain the outer shell):

```powershell
& '<product-worktree>/scripts/bootstrap.ps1'
```

The script requires an existing uv 0.11.19 executable; it does not install tools globally. It downloads CPython 3.12.13 under `.agent-work/python`, disables Python bin/registry registration, and installs the `test` and `validation` extras into `.venv` with `uv sync --locked`. It refuses missing/stale locks or discovery of a Python interpreter outside the project. It restores the caller's environment variables and does not start browsers, listeners or providers. Dependency downloads require network access on a cold cache. Do not regenerate the lock during bootstrap.

For a fresh-environment check without removing any existing environment, set `UV_PROJECT_ENVIRONMENT` to a new directory beneath `.agent-work` and run the same locked sync with the local interpreter:

```powershell
$project = '<absolute-product-worktree>'
$env:UV_PROJECT_ENVIRONMENT = "$project/.agent-work/clean-environment-<new-id>"
uv sync --project $project --locked --python "$project/.agent-work/python/cpython-3.12.13-windows-x86_64-none/python.exe" --extra test --extra validation --no-python-downloads
uv pip check --python "$env:UV_PROJECT_ENVIRONMENT/Scripts/python.exe"
```

Use a fresh PowerShell process for that check so its environment override cannot affect later commands. The initial clean-environment check installed 26 packages and `uv pip check` reported all installed packages compatible. Browser binaries are a separate installation; the validation extra alone does not install Chromium.

### Run the regression contracts

1. Bootstrap the locked environment as above. `pyproject.toml` declares the `test` extra (`pytest==8.3.4`, `httpx==0.27.2` for `fastapi.testclient`), `testpaths` and `pythonpath`.
2. Use absolute test/config paths to preserve the outer working directory:

   ```powershell
   $project = '<absolute-product-worktree>'
   & "$project/.venv/Scripts/python.exe" -m pytest "$project/tests" -c "$project/pyproject.toml" --collect-only -q
   & "$project/.venv/Scripts/python.exe" -m pytest "$project/tests" -c "$project/pyproject.toml" --ignore="$project/tests/browser/test_ui_journey.py" -q --junitxml="$project/.agent-work/baseline.xml"
   ```

   Excluding `test_ui_journey.py` keeps the pure calibration tests and all other baseline tests. Inspect the JUnit test/failure/error/skip counts and exit code; collection alone is not a runtime pass.
3. Browser tests (later phase only; skipped by default):
   - Install the `validation` extra.
   - Run `playwright install chromium`.
   - Build the UI from the same commit as the source under test: install the pinned web dependencies, then run `npm run build` in `web/`. The Vite config requires `VOICELAB_WEB_PORT`, set to a claimed port. The tests don't compare `VOICELAB_WEB_DIST` with the source, so a stale build makes the results meaningless.
   - Claim and check one host port with agent-ports.
   - Set `VOICELAB_TEST_PORT`, `VOICELAB_TEST_PORT_CLAIM` (the receipt path) and `VOICELAB_WEB_DIST` (`web/dist`).
   - Run the environment's Python with `-m pytest` and absolute test/config paths, as above.

   `tests/browser/conftest.py` skips with a clear reason when any of these is missing and never chooses a port itself. It serves the built UI and the relay from one origin on the claimed port, in a test-only composition that adds static files to the relay app. The separate-origin case reuses the same port with a `localhost` page origin.
4. Record the actual results. Treat any failure as a finding against the source or the test; do not change the test to make it pass.

The integration and security tests drive the ASGI app in-process through TestClient: no port, listener or provider. They create their artifact root under pytest's `tmp_path`. The app refuses an artifact root with a symlink or junction ancestor, so run them where the temp directory has no linked ancestor.

Known sensitivity: `ServerMessage.payload` is becoming a closed typed registry, and `Relay.send` validates each payload before sending. These tests don't construct `ServerMessage` directly, but the integration journey sends every mock message type (`ready`, `state`, `transcript`, `event`, `task`, `audio`, `error`, `ended`), so it must be reconciled once that registry is frozen. The privacy test depends on the `FIELDS` allowlist in `harness/privacy.py`, so re-check it when producer fields change.

## Deferred validation (carried from the construction handoff)

The environment/collection work above does not establish the following gates:

1. Ruff, mypy/pyright and TypeScript semantic checks (not installed by this Python environment package).
2. Coverage beyond the executed Python baseline: TypeScript/wire integration, crash recovery and broader concurrency behavior. Passing the baseline does not establish the complete fault matrix.
3. Browser and device: calibration, cancel/interrupt, recovery, cleanup and soak (`scripts/browser_harness.py`, `scripts/calibrate.py`, `scripts/soak.py`). Needs the optional `validation` extra, an installed Playwright Chromium, and agent-ports claims before anything listens.
4. Reviewed licensed fixtures, plus independent custody and oracle for heldout data.
5. Real provider capabilities and prices, an approved live budget, and the paired comparison.
6. Independent, non-authoring product acceptance.

Skipped or unavailable checks stay unknown. Do not count them as passes.
