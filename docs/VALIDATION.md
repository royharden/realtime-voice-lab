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

`tests/conftest.py` holds the shared synthetic builders. Until pytest's `pythonpath` is declared, it also puts `src/` on `sys.path`.

### How to run them later

Only do this once runtime validation is authorized:

1. Pin and lock dependencies (plan §3). Declare the dev dependency `pytest` (and later `pytest-timeout`), plus `[tool.pytest.ini_options] testpaths = ["tests"]`, `pythonpath = ["src"]` in `pyproject.toml`. That file is owned by the integrator; this has been requested and is not yet added.
2. `uv sync --extra dev` (or the equivalent locked install), then `uv run pytest tests -q`.
3. Record the actual results. Treat any failure as a finding against the source or the test; do not change the test to make it pass.

Known sensitivity: `ServerMessage.payload` may become a typed registry. These tests don't construct `ServerMessage`, but the privacy test depends on the `FIELDS` allowlist in `harness/privacy.py`, so re-check it when producer fields change.

## Deferred validation (carried from the construction handoff)

None of the following has been performed. They are listed in the order of the checks that will be needed later.

1. Dependency resolution and locks. Ruff, mypy/pyright and TypeScript semantic checks.
2. Python, TypeScript and wire-protocol paths. Security, accounting, crash and concurrency behavior. Runs this regression suite first.
3. Browser and device: calibration, cancel/interrupt, recovery, cleanup and soak (`scripts/browser_harness.py`, `scripts/calibrate.py`, `scripts/soak.py`). Needs the optional `validation` extra, an installed Playwright Chromium, and agent-ports claims before anything listens.
4. Reviewed licensed fixtures, plus independent custody and oracle for heldout data.
5. Real provider capabilities and prices, an approved live budget, and the paired comparison.
6. Independent, non-authoring product acceptance.

Skipped or unavailable checks stay unknown. Do not count them as passes.
