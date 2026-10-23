"""Plans/saved reports, plus explicitly activated mock/dev driver orchestration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import canonical_bytes
from .assembly import assemble_evaluation, prepare_inputs, write_evaluation
from .planning import create_trial_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="create a frozen prospective 40+8 paired schedule; execute nothing")
    plan.add_argument("--request", required=True)
    plan.add_argument("--out", required=True)
    for operation in ('assemble', 'report'):
        command = sub.add_parser(operation, help="saved-evidence assembly or canonical report; never runs the driver")
        inputs = command.add_mutually_exclusive_group(required=True)
        inputs.add_argument('--bundle')
        inputs.add_argument('--manifest')
        command.add_argument('--rows')
        command.add_argument('--ledger')
        command.add_argument('--evidence')
        command.add_argument('--source-hashes')
        command.add_argument('--driver-correlations')
        command.add_argument('--artifact-root')
        command.add_argument('--freeze-hash', help='externally supplied expected canonical freeze hash')
        command.add_argument('--out', required=True)
    run = sub.add_parser('run', help='later authorized mock/dev driver, gated by the canonical trial plan')
    run.add_argument('--manifest', required=True, help='D7 driver activation manifest')
    run.add_argument('--trial-plan', required=True)
    run.add_argument('--gate-bundle', required=True, help='pins manifest/plan/fixtures/freeze before exposure')
    run.add_argument('--artifact-root', required=True)
    run.add_argument('--freeze-hash', required=True)
    run.add_argument('--activation-reference', required=True)
    run.add_argument('--out', required=True)
    review = sub.add_parser('review-packet', help='prepare one bounded unadjudicated task packet from approved synthetic evidence')
    review.add_argument('--bundle', required=True)
    review.add_argument('--artifact-root', required=True)
    review.add_argument('--task-id', required=True)
    review.add_argument('--freeze-hash')
    review.add_argument('--out', required=True)
    append = sub.add_parser('review-append', help='append explicit human choices; CLI does not authenticate independence')
    append.add_argument('--packet', required=True)
    append.add_argument('--submission', required=True)
    append.add_argument('--journal', required=True)
    export = sub.add_parser('review-export', help='export canonical oracle claims; default CLI keeps correctness unknown')
    export.add_argument('--packet', required=True)
    export.add_argument('--journal', required=True)
    export.add_argument('--artifact-root', required=True)
    export.add_argument('--expected-head', help='externally retained immutable final decision head; absent means unknown')
    export.add_argument('--out', required=True)
    custody = sub.add_parser('custody-import', help='structural import only; no self-issued custodian/release approval')
    custody.add_argument('--bundle', required=True)
    custody.add_argument('--artifact-root', required=True)
    custody.add_argument('--freeze-hash', required=True)
    custody.add_argument('--release-hash', required=True)
    custody.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        result = create_trial_plan(json.loads(Path(args.request).read_text(encoding="utf-8")))
        # Exclusive creation: changing a published plan requires a new file and cohort.
        with Path(args.out).open("xb") as stream:
            stream.write(canonical_bytes(result))
    elif args.command == 'review-packet':
        from .adjudication import prepare_review_packet
        packet = prepare_review_packet(json.loads(Path(args.bundle).read_text(encoding='utf-8')),
            args.artifact_root, args.task_id, expected_freeze_hash=args.freeze_hash)
        with Path(args.out).open('xb') as stream:
            stream.write(canonical_bytes(packet))
    elif args.command == 'review-append':
        from .adjudication import append_decision
        record = append_decision(args.journal, json.loads(Path(args.packet).read_text(encoding='utf-8')),
                                 json.loads(Path(args.submission).read_text(encoding='utf-8')))
        print(json.dumps({'record_hash': record['record_hash'], 'actor_verification': record['actor_verification']}, sort_keys=True))
    elif args.command == 'review-export':
        from .adjudication import export_oracle, read_decision_journal
        packet = json.loads(Path(args.packet).read_text(encoding='utf-8'))
        records = read_decision_journal(args.journal, packet, expected_head=args.expected_head)
        rows, _ = export_oracle(packet, records, artifact_root=args.artifact_root, expected_head=args.expected_head)
        with Path(args.out).open('xb') as stream:
            stream.write(b''.join(canonical_bytes(row) for row in rows))
    elif args.command == 'custody-import':
        from .custody import import_custody
        receipt, _ = import_custody(json.loads(Path(args.bundle).read_text(encoding='utf-8')),
            args.artifact_root, expected_freeze_hash=args.freeze_hash, expected_release_hash=args.release_hash)
        with Path(args.out).open('xb') as stream:
            stream.write(canonical_bytes(receipt))
    elif args.command == 'run':
        bundle = json.loads(Path(args.gate_bundle).read_text(encoding='utf-8'))
        reader, manifest, frozen_plan, gates = prepare_inputs(bundle, args.artifact_root,
                                                       expected_freeze_hash=args.freeze_hash)
        supplied_plan = json.loads(Path(args.trial_plan).read_text(encoding='utf-8'))
        if supplied_plan != frozen_plan or manifest['mode'] != 'mock' or manifest['split'] != 'dev' or not gates['freeze_valid']:
            parser.error('run supports only the exact frozen, mock/dev plan; live/heldout remain closed')
        # Producer-owned validation must prove per-task fixture selection before any browser.
        # A manifest flag cannot turn the former one-fixture driver into a matched-cell driver.
        from voicelab.harness import activation, cli as harness_cli
        driver_spec = activation.load_activation(Path(args.manifest), args.activation_reference)
        bind_fixture = getattr(activation, 'bind_task_fixture', None)
        if bind_fixture is None:
            parser.error('paired driver fixture binding source unavailable; no driver started')
        fixtures = {row['fixture_id']: row for row in reader.json('fixtures', 'fixtures')['fixtures']}
        for task in frozen_plan['tasks']:
            bound = bind_fixture(driver_spec, task, planned=True)
            if bound['_fixture']['audio']['sha256'] != fixtures[task['fixture_id']]['sha256']:
                parser.error('actual driver fixture hash differs from frozen evaluation input')
        if driver_spec['run_id'] != manifest['run_id']:
            parser.error('driver run differs from frozen plan')
        if any(driver_spec[key] != manifest[key] for key in ('code_hash', 'config_hash')):
            parser.error('driver candidate/configuration differs from frozen manifest')
        harness_cli.main(['journey', '--manifest', args.manifest, '--trial-plan', args.trial_plan,
                          '--activation-reference', args.activation_reference, '--out', args.out])
    elif args.bundle:
        if not args.artifact_root:
            parser.error('--bundle requires --artifact-root')
        assembled = assemble_evaluation(json.loads(Path(args.bundle).read_text(encoding="utf-8")),
                                        args.artifact_root, expected_freeze_hash=args.freeze_hash)
        # No input file can load code or install a verifier. CLI defaults fail closed for heldout.
        write_evaluation(assembled, args.out)
    else:
        if not args.rows or not args.ledger:
            parser.error('canonical --manifest report requires --rows and --ledger')
        supplied_manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        if supplied_manifest.get('split') != 'dev':
            parser.error('direct CLI reporting is dev-only; heldout needs externally verified programmatic assembly')
        from voicelab.reporting import write_report
        write_report(args.manifest, args.rows, args.ledger, args.out,
                     evidence_path=args.evidence, source_hashes_path=args.source_hashes,
                     driver_correlations_path=args.driver_correlations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
