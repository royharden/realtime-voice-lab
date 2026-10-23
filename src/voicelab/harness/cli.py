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


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    for operation in ('journey', 'calibrate', 'soak'):
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
    demo = commands.add_parser('demo')
    demo.add_argument('--json', action='store_true')
    audit = commands.add_parser('audit-split')
    audit.add_argument('--manifest', type=Path, required=True)
    fixture = commands.add_parser('generate-dev-fixture')
    fixture.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == 'demo':
        value = {'evidence': 'planned walkthrough; no recording/rehearsal/measured duration', 'steps': DEMO_STEPS}
        print(json.dumps(value, indent=2) if args.json else '\n'.join(DEMO_STEPS))
        return
    if args.operation == 'audit-split':
        print(json.dumps(split_audit(json.loads(args.manifest.read_text(encoding='utf-8'))), indent=2))
        return
    if args.operation == 'generate-dev-fixture':
        from .fixtures import generate_development
        generate_development(args.out)
        return
    from .activation import load_activation
    from .evidence import EvidenceSink
    spec = load_activation(args.manifest, args.activation_reference)
    nonce = os.environ.get('VOICELAB_NONCE', '')
    if not 16 <= len(nonce) <= 128:
        parser.error('operator launch nonce required; never persist it in artifacts')
    sink = EvidenceSink(args.out, spec)
    completed, reason, references = False, 'driver_abandoned', {}
    try:
        async def execute():
            if args.operation == 'journey':
                from .journey import run_journeys
                plan = json.loads(args.trial_plan.read_text(encoding='utf-8')) if args.trial_plan else None
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
    main()
