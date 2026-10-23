# Evaluation source and evidence handoff

This is unexecuted construction source. No task plan, audio fixture, result, calibration, soak or live comparison was run to create this document. `voicelab.evaluation` prepares a prospective plan and joins saved evidence into the **existing** `voicelab.reporting.write_report` pipeline. It does not launch browsers, speak, call providers, issue custody releases or establish product acceptance.

## Prospective paired design

`create_trial_plan(request)` and `scripts/run_evaluation.py plan` produce a deterministic, hash-pinned schedule from a caller-supplied integer seed. The request contains `run_id`, `seed`, exactly eight distinct `families`, two distinct `speakers`, one `network_profile`, pinned `models` for V/C/K, `fixture_cells` with one `{family,speaker,fixture_id}` per cell, `fault_schedules` keyed by family, and `turns_per_task` (1–8, default eight). Each fault action has a named `kind` and bounded integer `turn_position`. Driver owners define the supported action names and must refuse unsupported ones.

- Clean: two pairs per family/speaker cell (32), plus one extra pair per family with alternating speakers (eight). The extra repeats are fixed before execution.
- Fault: one pair per family, alternating speakers (eight), with the same frozen action schedule applied to both arms.
- Each cohort separately randomizes an exactly balanced AB/BA order (A=V, B=C): 20/20 clean, 4/4 fault. V/C tasks run adjacently in their declared order; the harness must retain same-window execution evidence.
- Every pair also schedules one separate K control on the same stimulus and fault conditions. K never participates in the V/C pair difference or AB/BA count. Total: 48 paired comparisons, 96 V/C arm-tasks and 48 K tasks.
- Each task has prespecified logical-turn slots (at most eight). Unused slots require an explicit `not_started` record; absence is unknown. Retrying transport retains its logical-turn identity and adds new attempt IDs. Do not recycle IDs for a new logical turn.

The selected network profile is a separate cohort. Repeat planning under a new run for other network/model/mode cohorts; never pool them. Fifty-ms each-direction application delay and forty-ms each-direction plus seeded 2% eligible-message drop are synthetic diagnostics, not observations about packet loss. This planner does not create those conditions.

The plan contains ordered `tasks`, `pairs`, canonical `turn_assignments`, `scheduled_turns` and `scheduled_tasks`. Tasks expose `task_id`, `trial_id`, task-level `pair_id`, `arm`, `cohort`, `family`, `speaker`, `repeat`, `network_profile`, `model_version`, `fixture_id`, `fault_schedule`, `logical_turn_ids` and `control`. A turn's canonical `pair_id` adds its position, preventing the reporter from treating several turns in a task as duplicate paired arms. K turn pair IDs are null. No expected answer is included in the executable plan.

`plan_hash` is SHA256 of canonical sorted compact JSON plus LF for the plan without its `plan_hash` field. `validate_trial_plan` reconstructs the same design from its embedded request and rejects changed ordering/membership/counts. Manifest `trial_plan_hash` pins this canonical digest. Artifact references separately hash **exact file bytes**. Changing a plan, model, threshold or fixture needs a new frozen cohort, not an in-place edit. CLI plan output uses exclusive file creation.

## Driver → assembler seam

The browser driver consumes the task plan; the assembler consumes a bundle pointing at saved artifacts under one explicit owned root. `artifacts` maps logical names to `{path,sha256,kind}`. Paths must be relative and resolve inside that root, including symlink resolution; hashes must match exact bytes. Audio also requires `approved_synthetic:true`. This flag is a retention prerequisite, not independent rights or custody proof. Hashes establish content binding, not who observed it.

Required names/kinds:

| Name | Kind | Content |
|---|---|---|
| `manifest` | `manifest` | Canonical RunManifest, explicit complete task/turn denominators |
| `plan` | `plan` | Frozen plan above |
| `fixtures` | `fixtures` | Fixture/license/provenance manifest |
| `trace` | `trace` | JSONL canonical VoiceEvent records |
| `ledger` | `ledger` | AttemptLedger summary/export including attempts/events |
| `observations` or `driver` | `observations` or `driver_observations` | Canonical TurnObservation sidecars, or raw D7 `driver.jsonl` converted here |
| `journal` | `journal` | Authority export with exact `run_id`; adjudicator binds its whole-file hash |
| `adjudication` (optional) | `adjudication` | External independent listening/journal rows |
| `freeze` (optional in dev) | `freeze` | Pre-exposure code/detector/scorer/plan/fixture pins |
| `release` (heldout only) | `release` | Externally verified independent custody release |

Additional named `audio` references bind stimulus/rendered WAVs and `calibration` references bind saved repeat lists. No raw audio is embedded in trace/result JSONL. Only explicitly referenced evidence is read; no directory discovery or heldout search occurs.

When `driver` is supplied, D5 converts raw events rather than expecting D7 to produce metrics. Ordered task/action/wire observations remain separate from server facts. In particular `turn_start_attempted`, `turn_driver_ended`, DOM state and session admission do not establish a started or successful logical turn. Malformed/missing hooks and sequence/clock ambiguity stay unknown. The converter refuses raw nonce/headers/authorization/PCM/transcript/text fields; producer telemetry must already be redacted. Original raw files are never rewritten.

Observed relay run IDs (including `mock-local`), server namespaces/session/boot IDs and wire turn IDs remain raw. Saved Ready envelopes and wire commits bind them to prespecified task/turn slots. The converter retains these aliases in `driver-correlations.json`, including actual transport and logical parents; the canonical result/attempt join uses the explicitly declared scheduled slot, never an overwritten source trace. ScheduledTurn's optional `observed_trial_id`, `observed_session_id`, `observed_boot_id` and `identity_evidence_reference` retain this separate verified mapping. Ambiguous or conflicting mappings fail/withhold instead of matching by a reused wire turn number alone. Session admission comes from actual Ready envelopes and is counted separately from logical-turn starts.

Recovery can have several transport terminals under one logical parent. Ordered start/terminal records survive in the correlation sidecar; earlier failed/interrupted attempts are not discarded. The final transport outcome does not reset the original logical deadline or erase prior cost. Browser `speech_end_ms` retains the earliest saved anchor for that logical parent; later retry timing is never rebased to a new zero. No browser anchor can be synthesized from driver/server nanoseconds. Observed controller duration uses explicit D4 `span_start_ns`/`span_end_ns`/`span_clock_domain` and boot identity; trace receipt timestamps cannot stand in for controller execution.

`TurnObservation` is defined in `evaluation/assembly.py`:

- Required `run_id`, `trial_id`, `logical_turn_id` bind the immutable assignment.
- `admission`: `started|refused|not_started|unknown`; `outcome` is a canonical terminal enum or null. Missing observation/terminal yields `missing_terminal`, never success. An admitted session is not automatically a started logical turn.
- `timing_domain`: `browser_performance_ms|unknown`; optional nonnegative finite `speech_end_ms`, `first_received_pcm_ms`, `first_nonsilent_render_ms` are driver-observed anchors. Subtraction is allowed only within that browser domain, never across server/provider clocks. Render time denotes the browser output timestamp estimate, not physical sound at an ear. The driver must supply the first non-silent rendered anchor, not scheduled/received audio time.
- Optional `input_audio_artifact`, `rendered_audio_artifact`, `calibration_artifact` are names in the bundle reference map. Missing/unapproved/hash-invalid audio gives unknown adjudication; it does not delete the turn.
- `retry_count`, `duplicate_count` are nonnegative integers; `stages` retain explicit stage/domain/observation data for canonical report analysis. A stage without valid same-domain anchors must remain unknown.

Whole-file trace integrity checks catch malformed canonical events, duplicate/reordered session/connection/domain sequences, nonmonotonic same-domain timestamps and producer integrity errors. Cross-run/unscheduled or wrong arm/trial trace data is rejected. Integrity failures invalidate timing/qualification rather than selectively dropping bad events. D4 owns canonical event/schema evolution; D7 owns raw browser measurements and observation emission. D5 owns joining them. These are source contracts; joined execution remains unverified.

## Fixture, freeze and custody gates

Fixture manifest shape is `{fixtures:[...]}`. Every referenced fixture has `fixture_id`, `split`, `wording_family`, `speaker`, `interruption_schedule`, `synthetic:true`, `audio_artifact`, exact `sha256`, `generator`, `generator_version`, `voice`, nonempty `settings`, actual `sample_rate`, and `license:{reviewed:true,reference,output_redistribution:true}`. The license reference must cover generator code/weights, voice and output rights under the eventual review. Public/upstream fixtures are dev-only. Selected family/speaker metadata must match the plan; input bytes must match the fixture digest. The gate compares the exact fixture-manifest file hash with `RunManifest.fixture_manifest_hash`.

The development corpus target is at least 12 wording families and four synthetic speakers; below-target dev inputs get an explicit coverage limitation. A paired plan selects eight/two from that corpus without claiming heldout status. The independent heldout corpus requires eight new families, two different speakers and unseen interruption schedules; intersections are checked **per dimension** against declared development rows.

Freeze `pins` must exactly match manifest `code_hash`, `build_hash`, `config_hash`, `schema_hash`, `fixture_manifest_hash`, `trial_plan_hash`, `fixture_seal`, plus canonical `thresholds_hash`, `endpoint_hash` and `onset_hash`. `frozen_before_exposure:true` is a required declaration whose validity still needs external evidence. An operator supplies the **expected** canonical freeze digest independently; the assembler never derives an approval pin from its own input and calls that approval. Missing/mismatched freeze leaves dev progress explicitly limited.

Heldout fails closed without an operator-injected `verify_release` callback. That trusted callback must establish actual nonauthor custody/lineage, frozen pre-exposure release and single-use authorization/redemption outside implementation-controlled files. It must not merely trust JSON booleans or verify a self-generated seal. The release must bind `freeze_hash`, `fixture_manifest_hash`, `candidate_hash`, `seal`, `single_use_authorized:true`, and a durable `reference`. The assembler checks bindings **before reading heldout audio**. Single-use bookkeeping/authentication belongs to the independently operated release authority; it is not implemented by an author-written local flag. No default trust callback, gold authoring or release issuer is supplied here. The CLI therefore supports dev evidence assembly only; it cannot unlock heldout with a file flag.

## Independent adjudication and canonical assembly

Programmatic `assemble_evaluation` accepts an optional trusted `verify_oracle(row)` callback. It must verify the real nonauthor reviewer and evidence scope; no file can name/import executable verifier code. The default is absent and all correctness stays unknown. A supplied row needs exact `run_id`, `trial_id`, `logical_turn_id`, `trace_hash`, `journal_hash`, `input_audio_hash`, `rendered_audio_hash`, `method:"independent_manual_listening_and_authority_journal"`, `confirmation_evidence_reviewed:true`, nullable `task_correct`/`spoken_correct` booleans, and optional canonical `task_terminal`. A successful task terminal requires both correctness checks true. Missing audio, missing binding, interpretation uncertainty or provider transcript alone cannot establish success. The authority journal must be inspected for genuine explicit confirmation, wrong slots, false commitments, contradictory confirmation, unrequested bookings and duplicate effects; a provider's proposal is untrusted.

Every scheduled turn gets canonical TurnResult and EvidenceRecord output even when its terminal is missing. Task, arm, family, pairing and clean/fault/network/model membership come from the frozen assignment, not surviving successes. Trace `turn_started|audio_received|commit_received` establishes a start; driver admission references its hashed observation file. Trace `turn_ended.terminal_outcome` reconciles driver terminals; conflicts become integrity failures. Boot identity separates restart clock/sequence namespaces. The original assignment is retained with start evidence references. Reservations/settled/unknown attempts remain available to the reporter's conservative start reconciliation. A reservation without proof of dispatch is ambiguous and causes headline withholding under the existing F2 safeguard. Conflicting start/refusal evidence is recorded as an integrity issue. Refusals stay visible outside response denominators.

Ledger assembly validates canonical Attempt fields, preserves explicit dispatch/start flags separately, retains original events and recomputes known cost/unknown count/reserved exposure from immutable attempt states. Supplied monetary/count summaries must agree. Unknown/reserved states never become free because usage is missing; late usage events do not mutate terminal unknown liability. EvidenceRecord hashes bind exact trace/ledger/adjudication input files and each canonical result; missing adjudication/audio stays unknown. Artifact hashes do not turn dev observations into independent acceptance.

## Later activation interfaces

These commands are prospective and were **not run**:

```text
python scripts/run_evaluation.py plan --request plan-request.json --out prospective-plan.json
python scripts/run_evaluation.py assemble --bundle bundle.json --artifact-root <owned-evidence-root> --freeze-hash <externally-pinned-canonical-freeze-hash> --out <owned-new-output>
```

Root CLI integration calls `voicelab.evaluation.cli:main(argv)` with `plan`, `assemble`, `run` or `report`. Executable audio/browser driving remains D7's driver and requires later runtime authorization through the `run` composition below. No entrypoint starts a listener or calls a provider. Live evidence additionally needs actual authorized provider/capability/price/budget/fixture prerequisites; parsing saved evidence cannot confer that authorization.

The root CLI's `run` and `eval` routes are now implemented: evaluation `run` validates the frozen mock/dev plan and gates, calls the producer's `activation.bind_task_fixture(spec,task,planned=True)` for every task, compares its real audio digest to the frozen fixture manifest, checks candidate/config identity, and delegates to `harness.cli.main(['journey',...])`. A global fixture cannot substitute for the selected fixture IDs. Missing bindings, wrong family/speaker/hash or unimplemented producer refuse before browser launch. D7 revalidates fixture inputs at execution and emits actual binding evidence; no per-cell correctness is inferred from a tone label.

Prospective root composition (not executed):

```text
voicelab run --manifest driver-activation.json --trial-plan prospective-plan.json --gate-bundle preflight-bundle.json --artifact-root <owned-root> --freeze-hash <external-pin> --activation-reference <later-runtime-grant> --out <new-driver-output>
voicelab eval --bundle bundle.json --artifact-root <owned-root> --out <new-assembled-output>
voicelab eval --manifest assembled-manifest.json --rows assembled-turns.jsonl --ledger assembled-ledger.json --evidence assembled-evidence.jsonl --source-hashes source-hashes.json --driver-correlations driver-correlations.json --out <new-report-output>
```

`report` also accepts the original `--manifest/--rows/--ledger` canonical input form; `scripts/eval.py` is a thin alias to it. Direct CLI report inputs are dev-only; independent heldout requires the trusted programmatic assembly route. `plan` and `assemble` remain supported aliases/interfaces. `run` can start the already-installed authorized D7 driver during later activation; importing the module does not start anything. It never starts relay/frontend listeners or dispatches live providers. Driver activation does not itself supply an oracle or run the metrics generator.

`write_evaluation` writes deterministic assembled manifest/turn/attempt/evidence/ledger/gate/source-hash files, then calls the canonical `write_report` once. No alternate metrics/report generator is introduced. Output uses fixed manifest metadata, sorted canonical serialization and exact input digests. No outputs have been generated during construction. Later verification must exercise malformed/missing/reordered records, independent custody/oracles, balanced-driver compliance, trace/ledger joins, report byte reproduction and full dev/offline/live acceptance gates. All remain unverified.

The canonical reporter also emits `failures.json`, `numeric-fragments.json`, `README-results.md` and `LIMITATIONS-results.md` from the same report object, with exact input hashes. It does not overwrite checked-in README/limitations. Evidence records are checked against canonical result hashes, and their source digests must occur in supplied original source references. Full denominator withholding from the F2 repair remains active. Receipt/render raw, calibrated and correctness-qualified conditional distributions are distinct; all-started clean receipt/render nonattainments retain exact nearest-rank censoring. Stage spans require finite ordered anchors, identical clock domain and scope, and real sample rates for sample clocks. Unknown provider clocks, cross-boot arithmetic, absent native internals and translation identity get no fabricated duration. Family-clustered intervals are percentile bootstrap intervals (1,000 fixed-seed replicates), conditional on both qualified responses attaining; missing/unmatched pairs stay explicit. Costs preserve every component attempt/price version and reserved unknown liability; rates show input/output/session denominators separately and undefined zeros. No winner, measured result or independent acceptance is fabricated.
