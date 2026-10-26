# Validation

The offline evidence below applies to code commit `5f1c1b594c07f19d51a8630e3eb1b2a7a089cb3b`. It uses synthetic development inputs, typed mock turns and fake microphone devices. It is not a speech benchmark, acoustic measurement, live-provider comparison, heldout release or independent product acceptance.

## Executed evidence

| Check | Actual result | Scope |
|---|---|---|
| Unit phase | 155 pytest cases passed | Python contracts, accounting, boundaries, reporting, integration and synthetic calibration controls; CLI and actual UI suites excluded |
| CLI/control phase | 112 pytest cases passed | 108 reporter-integrity controls plus four actual mock arm-journey/trial-plan cases; not 112 voice tasks |
| Browser phase | 5 pytest cases passed | Built UI in Chromium with fake media, same-origin mock relay and typed stimulus |
| Total pytest | **272 passed; 0 failures, 0 errors, 0 skipped** | Sum of the three disjoint phases, not a task-success denominator |
| Web build | Vite 6.4.5 / TypeScript 5.7.3 | Fresh four-file bundle pinned by file hashes to the tested source |
| npm audit | 0 reported vulnerabilities | Snapshot of the tested dependency lock and audit response; not a general security certification |
| Public-development evaluation | 2 checks passed | Canonical report denominator/cost/repeated-byte arithmetic and a fresh-interpreter SQLite task/attempt reopen journey |
| Isolated Docker evaluation | Same 2 checks passed, exit 0 | Fresh image from the same code commit; no network, published ports or host mounts, non-root execution; not additional pytest or voice tasks |

Counts were verified from actual JUnit testcase elements and matching phase counters. The five browser cases cover control initialization without page errors, protected same-origin metadata POSTs, a typed mock journey with post-End evidence, committed-booking interruption/stale-epoch fencing, and visible refusal of separate-origin serving. They do not establish all browser/device/fault combinations.

The public-development report retains a missing terminal in a 19-row fixture, reports p95 as `>10 s / not attained`, keeps unknown attempt exposure and reproduces the same bytes on repeat. Those 19 rows are fixture inputs, not 19 extra tests or measured voice tasks. SQLite reopen retains one booking effect, two ledger attempts and unknown liability. The Docker execution repeats these same checks from verified image inputs; Docker browser and full-soak execution remain unrun.

The initial environment milestone separately established a locked install of 26 compatible packages, full collection of its then-current 51 tests, and 48 passing non-UI tests. Those historical 48 are not added to the final 272.

## Reproduce the environment

Use PowerShell 7 and uv 0.11.19. Substitute an absolute product checkout for the placeholder; cwd may remain outside that checkout:

```powershell
$project = '<absolute-product-checkout>'
& "$project/scripts/bootstrap.ps1"
```

Bootstrap installs CPython 3.12.13 beneath `.agent-work/python`, suppresses global Python bin/registry registration and installs test plus validation extras into `.venv` from `uv.lock` with `--locked`. A cold cache needs network access for dependencies. No listener, provider or browser is started. Missing locks and wrong uv versions refuse early; repeated bootstrap is idempotent. Do not mutate an environment frozen for another check. For a fresh-install check, use a separate worktree-local environment; preserve previous evidence instead of deleting it.

## Run the bounded validation phases

The runner writes the actual source/test commits, dirty status, commands, return codes, timeouts and JUnit counts. Use a fresh output directory for every invocation:

```powershell
& "$project/.venv/Scripts/python.exe" "$project/scripts/ci_validate.py" --phase unit --source $project --out "$project/.agent-work/unit-<new-id>" --timeout 300
```

`--phase` accepts `unit`, `cli`, `browser` or `all`; default is `unit`. `--source` selects the source/config checkout; tests remain in the runner's own checkout. Pin both when they differ. The runner supplies explicit source `PYTHONPATH` so an interpreter installed elsewhere does not silently test that other checkout. Invalid JUnit structure, contradictory counts, zero cases, skips, timeouts and failed subprocesses cannot produce a passing phase. Inspect `validation.json`, phase logs and XML, not only the shell exit code.

CLI and browser phases require an operator-owned loopback port claim and an immediately successful ownership check before listeners start. Then supply the matching `VOICELAB_TEST_PORT`, `VOICELAB_TEST_PORT_CLAIM` and `VOICELAB_PORT_CHECKED` values. A file path or environment flag alone is not ownership proof. Missing preflight makes those phases `not_run`; do not count that as a pass.

For the browser phase, also install the pinned Chromium binary with the environment's Playwright module, build the exact candidate UI, and set `VOICELAB_WEB_DIST` to its `web/dist`:

```powershell
& "$project/.venv/Scripts/python.exe" -m playwright install chromium
& "$project/.venv/Scripts/python.exe" "$project/scripts/ci_validate.py" --phase browser --source $project --out "$project/.agent-work/browser-<new-id>" --timeout 300
```

The second command is only valid after port preflight and exact-bundle setup. Test fixtures compose the built UI and relay on the same origin; this does not add a production static-hosting command. Use [operations](OPERATIONS.md) for listener and CLI access boundaries. Integration/security tests that use in-process TestClient do not require a listening port. Test artifact roots must have no symlink/junction ancestor.

Web setup uses `npm --prefix "$project/web" ci`, then `npm --prefix "$project/web" run typecheck` and `npm --prefix "$project/web" run build`. Put the pinned environment's Python on the command's PATH for the binding-generator hooks, and set `VOICELAB_WEB_PORT` to the checked frontend port before loading the Vite configuration. Record the exact resulting bundle hashes; an old `web/dist` is not evidence for changed source. The final tested build contained the HTML entry, CSS, JavaScript bundle and audio worklet.

## Generator and evidence qualifications

Generated browser bindings embed raw source-byte hashes. Windows CRLF checkouts can differ from LF Git blobs and make raw-EOL regeneration/checks differ. Record checkout and canonical blob hashes separately; use a reviewed canonical-EOL source/build checkout for exact reproduction. Do not waive parity checks or relabel normalized bytes as identical raw bytes. The successful pinned build does not remove this portability limitation.

Public-development evaluation exercises pure APIs with inline team-authored fixtures. Saved-artifact CLI report success remains blocked until genuine PublicReadAccess and external rights/access authority exist. Input paths, hashes, fixture labels and activation strings cannot issue that authority. No heldout or private inputs are made public by these tests.

Full offline/live soak, physical-device calibration, speech correctness, real provider compatibility/pricing/billing, licensed spoken fixtures, independent custody and product acceptance remain open. No recorded demo or measured latency winner follows from the checks above. See [limitations](LIMITATIONS.md).
