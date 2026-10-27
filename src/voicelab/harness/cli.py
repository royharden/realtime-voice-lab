"""Source entrypoint for later operator activation; never auto-starts services."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from collections.abc import Sequence

DEMO_STEPS = (
    'Show MOCK / DEV diagnostic label, runtime-unverified status and fictional task.',
    'Reset with End then Start: fresh fixture namespace; never delete the journal.',
    'V: hours, list slots, select slot 1, confirm, status. Typed words bypass speech interpretation.',
    'End V. Repeat the same fixed development task with C then K, in fresh namespaces.',
    'Interrupt before confirmation and after booking: committed booking survives cancellation.',
    'Disconnect, explicitly retry within the bounded window, show repeat/unknown recovery and no audio replay.',
    'Export browser trace and retain D4 terminal journal/attempt references for D5 assembly.',
    'Show all attempts and unknown spoken/acoustic correctness. No measured winner.',
    'W7/W8 independent gates remain closed; no video or measured duration is claimed.',
)


def split_audit(manifest: dict) -> dict:
    # D5 owns actual evaluation/custody gates; this is a structural comparison only.
    collisions, complete = {}, {}
    for dimension in ('wording_family', 'speaker', 'interruption_schedule'):
        development = {r[dimension] for r in manifest.get('development', []) if r.get(dimension)}
        heldout = {r[dimension] for r in manifest.get('heldout', []) if r.get(dimension)}
        collisions[dimension] = sorted(development & heldout)
        complete[dimension] = all(bool(manifest.get(side)) and all(
            isinstance(row.get(dimension), str) and bool(row[dimension])
            for row in manifest[side]) for side in ('development', 'heldout'))
    dimensions_complete = all(complete.values())
    return {'dimension_collisions': collisions,
            'disjoint': not any(collisions.values()) if dimensions_complete else None,
            'dimensions_complete': dimensions_complete, 'complete_by_dimension': complete,
            'custody_independence': 'not established by structural comparison',
            'heldout_release': False, 'product_acceptance': False}


def main(argv: Sequence[str] | None = None, *, authority=None, calibration_provider=None,
         attempt_bound_verifier=None, k_approval_verifier=None, public_access=None, artifact_references=None, artifact_root=None,
         excluded_actor_refs: frozenset[str] = frozenset()) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    local = commands.add_parser('local-synthetic-soak', help='Generated non-speech/fake-media scope only; no external rights authority')
    local.add_argument('--profile', choices=['short', 'full'], default='short')
    local.add_argument('--port', type=int, required=True)
    local.add_argument('--port-claim', type=Path, required=True)
    local.add_argument('--web-dist', type=Path, required=True)
    local.add_argument('--web-manifest', type=Path, required=True)
    local.add_argument('--out', type=Path, required=True)
    for operation in ('journey', 'calibrate', 'soak', 'cohort'):
        command = commands.add_parser(operation)
        command.add_argument('--manifest', type=Path, required=True)
        command.add_argument('--activation-reference', required=True)
        command.add_argument('--out', type=Path, required=True)
        if operation == 'journey':
            command.add_argument('--trial-plan', type=Path)
        if operation == 'calibrate':
            command.add_argument('--phase', choices=['pre', 'post'], required=True)
        if operation == 'soak':
            command.add_argument('--profile', choices=['full', 'short'], default='full')
        if operation == 'cohort':
            command.add_argument('--trial-plan', type=Path, required=True)
            command.add_argument('--gate-bundle', type=Path, required=True)
            command.add_argument('--artifact-root', type=Path, required=True)
            command.add_argument('--freeze-hash', required=True)
    demo = commands.add_parser('demo')
    demo.add_argument('--json', action='store_true')
    audit = commands.add_parser('audit-split')
    audit.add_argument('--manifest', type=Path, required=True)
    fixture = commands.add_parser('generate-dev-fixture')
    fixture.add_argument('--out', type=Path, required=True)
    catalog = commands.add_parser('import-fixture-catalog')
    catalog.add_argument('--source-root', type=Path, required=True)
    catalog.add_argument('--catalog', type=Path, required=True)
    catalog.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == 'local-synthetic-soak':
        from .local_synthetic import run_local_synthetic
        result = run_local_synthetic(args)
        print(json.dumps({'summary': result.get('summary'), 'incomplete': result.get('incomplete'),
                          'evidence_label': result['evidence_label'], 'product_acceptance': False}, allow_nan=False))
        return 0 if (type(result.get('incomplete')) is list and not result['incomplete']
                     and result.get('summary', {}).get('coverage_complete') is True
                     and (args.profile != 'full' or result['summary'].get('full_gate_pass') is True)) else 1
    if args.operation == 'demo':
        value = {'evidence': 'planned walkthrough; no recording/rehearsal/measured duration', 'steps': DEMO_STEPS}
        print(json.dumps(value, indent=2) if args.json else '\n'.join(DEMO_STEPS))
        return
    if args.operation == 'audit-split':
        from .access import public_reader, read_owned
        reader = public_reader(artifact_root, artifact_references, public_access)
        relative = args.manifest.resolve().relative_to(reader.root).as_posix()
        print(json.dumps(split_audit(json.loads(read_owned(reader, reader.root, relative,
            maximum=4_000_000))), indent=2))
        return
    if args.operation == 'generate-dev-fixture':
        from .fixtures import generate_development
        generate_development(args.out)
        return
    if args.operation == 'import-fixture-catalog':
        from .catalog import import_catalog
        print(json.dumps(import_catalog(args.source_root, args.catalog, args.out,
            public_access=public_access, references=artifact_references), sort_keys=True))
        return
    if args.operation == 'cohort':
        from .cohort import execute_cohort
        execute_cohort(args, authority=authority, calibration_provider=calibration_provider,
                       public_access=public_access,
                       k_approval_verifier=k_approval_verifier,
                       attempt_bound_verifier=attempt_bound_verifier,
                       excluded_actor_refs=excluded_actor_refs)
        return
    from .activation import load_activation
    from .evidence import EvidenceSink
    spec = load_activation(args.manifest, args.activation_reference, load_default_fixture=False)
    nonce = os.environ.get('VOICELAB_NONCE', '')
    if not 16 <= len(nonce) <= 128:
        parser.error('operator launch nonce required; never persist it in artifacts')
    from .access import public_reader
    reader = public_reader(spec['fixture_root'], artifact_references, public_access)
    spec['_output_reader'] = reader
    sink = EvidenceSink(args.out, spec)
    completed, reason, references = False, 'driver_abandoned', {}
    try:
        from .access import public_reader, read_owned
        from .activation import load_reviewed_fixture
        from voicelab.evaluation.exposure import require_output_root
        reader = public_reader(spec['fixture_root'], artifact_references, public_access)
        require_output_root(reader, sink.root)
        spec['_artifact_reader'] = reader
        if spec.get('fixture_manifest'):
            fixture, audio = load_reviewed_fixture(reader.root, spec['fixture_manifest'], reader)
            spec.update(_fixture=fixture, _input_wav=str(audio))
        async def execute():
            if args.operation == 'journey':
                from .journey import run_journeys
                plan = json.loads(read_owned(reader, reader.root,
                    args.trial_plan.resolve().relative_to(reader.root).as_posix(),
                    maximum=4_000_000)) if args.trial_plan else None
                return await run_journeys(spec, nonce, sink, plan=plan)
            if args.operation == 'calibrate':
                from .calibration import run_calibration
                return await run_calibration(spec, nonce, sink, args.phase)
            from .soak import run_soak
            return await run_soak(spec, nonce, sink, args.profile)
        deadline = spec.get('driver_deadline_seconds', 1900)
        if type(deadline) is not int or not 1 <= deadline <= 1900:
            raise ValueError('driver deadline must be 1..1900 seconds')
        result = asyncio.run(asyncio.wait_for(execute(), timeout=deadline))
        references = result.get('references', {})
        references['summary'] = sink.artifact('summary.json', result, 'driver_summary')
        completed = not result.get('incomplete')
        reason = 'driver_schedule_completed' if completed else 'incomplete_observation_coverage'
    except BaseException as exc:
        reason = 'driver_failed_' + type(exc).__name__
        raise
    finally:
        sink.close(completed=completed, reason=reason, references=references)


if __name__ == '__main__':
    raise SystemExit(main())
