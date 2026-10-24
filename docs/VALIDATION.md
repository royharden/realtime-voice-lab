# Validation plan

**Status: NOT YET RUN.** Everything below is authored source and a plan for later validation. No test, application import, service, browser, database, provider or audio workflow has been executed. Source was built in a construction phase that allowed static checks only (syntax parsing and source inspection). Do not read any test listed here as passing.

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

`tests/conftest.py` holds the shared synthetic builders. `src/` is importable through `[tool.pytest.ini_options] pythonpath`.

### How to run them later

Only do this once runtime validation is authorized:

1. Pin and lock dependencies (plan §3). `pyproject.toml` already declares the `test` extra (`pytest==8.3.4`), `testpaths` and `pythonpath`. The security and integration tests use `fastapi.testclient`, which also needs `httpx`. That has been requested from the integrator and is not yet declared.
2. `uv sync --extra test` (or the equivalent locked install), then `uv run pytest tests -q`.
3. Record the actual results. Treat any failure as a finding against the source or the test; do not change the test to make it pass.

The integration and security tests drive the ASGI app in-process through TestClient: no port, listener or provider. They create their artifact root under pytest's `tmp_path`. The app refuses an artifact root with a symlink or junction ancestor, so run them where the temp directory has no linked ancestor.

Known sensitivity: `ServerMessage.payload` is becoming a closed typed registry, and `Relay.send` validates each payload before sending. These tests don't construct `ServerMessage` directly, but the integration journey sends every mock message type (`ready`, `state`, `transcript`, `event`, `task`, `audio`, `error`, `ended`), so it must be reconciled once that registry is frozen. The privacy test depends on the `FIELDS` allowlist in `harness/privacy.py`, so re-check it when producer fields change.

## Deferred validation (carried from the construction handoff)

None of the following has been performed. They are listed in the order of the checks that will be needed later.

1. Dependency resolution and locks. Ruff, mypy/pyright and TypeScript semantic checks.
2. Python, TypeScript and wire-protocol paths. Security, accounting, crash and concurrency behavior. Runs this regression suite first.
3. Browser and device: calibration, cancel/interrupt, recovery, cleanup and soak (`scripts/browser_harness.py`, `scripts/calibrate.py`, `scripts/soak.py`). Needs the optional `validation` extra, an installed Playwright Chromium, and agent-ports claims before anything listens.
4. Reviewed licensed fixtures, plus independent custody and oracle for heldout data.
5. Real provider capabilities and prices, an approved live budget, and the paired comparison.
6. Independent, non-authoring product acceptance.

Skipped or unavailable checks stay unknown. Do not count them as passes.
