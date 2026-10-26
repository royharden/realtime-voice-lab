"""Inventory explicitly supplied public metadata; never grant evidence access or acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PureWindowsPath

from pydantic import TypeAdapter

from voicelab.contracts import EvidenceRecord, RunManifest
from voicelab.evaluation.artifacts import ArtifactReader


# These are missing-evidence prompts from selected plan sections 4, 6, 8 and W7/W8,
# not a parallel implementation of their acceptance predicates.
PREREQUISITES = (
    ('rights', 'Genuine external public-read and fixture/output redistribution authority; '
     'hashes and synthetic labels do not grant access or rights.'),
    ('live', 'Approved live budget, current prices/caps, supported TTS and measured '
     'V/STT/TTS capabilities; actual 40 clean and 8 fault paired tasks plus K evidence.'),
    ('calibration', 'Pinned measured browser before/after calibration (30 known-gap '
     'repetitions, sample anchors and cancellation cutoff); mock arithmetic is insufficient.'),
    ('soak', 'Observed full offline soak (three contexts; 30 minutes or 200 aggregate turns, '
     '40 interruptions, 10 reconnects), cleanup/resources and live soak per arm; '
     'short or incomplete coverage cannot replace these records.'),
    ('custody_w7', 'Appointed nonauthor custodian, licensed disjoint wording/speaker/schedule '
     'dimensions, pre-exposure freeze and genuinely authenticated single-use release; '
     'public metadata inspection cannot authenticate or consume that release.'),
    ('oracle', 'Independent confirmation/journal and rendered spoken-audio adjudication; '
     'provider transcripts, typed mocks and artifact hashes are not an oracle.'),
    ('independent_review_w8', 'Separately appointed nonauthor outside implementation/demo/scorer '
     'and parent/child/fork lineage independently verifies authorized raw evidence and '
     'records discrepancies; a later INT decision owns product acceptance.'),
)


def _hex(value: str, length: int) -> str:
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is None:
        raise ValueError(f'expected lowercase {length}-hex identity')
    return value


def _public_path(value: str) -> str:
    """Fail before byte access to protected or credential-like path components."""
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('forward-slash public relative JSON path required')
    path = Path(value)
    if path.is_absolute() or PureWindowsPath(value).drive or '..' in path.parts:
        raise ValueError('public relative JSON path required')
    prohibited = ('credential', 'password', 'secret', 'token', 'ssh', 'holdout', 'heldout')
    for part in path.parts:
        normalized = part.casefold()
        if (part.startswith('.') or normalized in {'gold', 'acceptance', 'auth', 'private'}
                or any(word in normalized for word in prohibited)):
            raise ValueError('protected or credential-like metadata path refused')
    if path.suffix != '.json':
        raise ValueError('only public JSON metadata can be inspected')
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('nonfinite JSON value')


def _metadata(root: Path, name: str, sha256: str, kind: str):
    reference = {'path': _public_path(name), 'sha256': _hex(sha256, 64), 'kind': kind}
    # Root itself must not disguise a protected directory or link/reparse component.
    absolute = root.absolute()
    for component in (absolute, *absolute.parents):
        if component.name:
            normalized = component.name.casefold()
            if (component.name.startswith('.') or normalized in {'gold', 'acceptance', 'private', 'heldout'}
                    or component.is_symlink() or component.is_junction()):
                raise ValueError('protected or linked public root refused')
    reader = ArtifactReader(root, {'metadata': reference})
    raw = reader.read('metadata', kind, maximum=2_000_000)
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
    return value, reference


def inventory(*, public_root: Path | None = None, manifest: str | None = None,
              manifest_sha256: str | None = None, evidence: str | None = None,
              evidence_sha256: str | None = None, source_commit: str | None = None,
              runtime_commit: str | None = None) -> dict:
    """Validate canonical public DTOs without dereferencing any of their artifacts."""
    report = {
        'schema_version': 1, 'kind': 'public_acceptance_readiness_inventory',
        'status': 'blocked', 'product_acceptance': False, 'live_acceptance': False,
        'heldout_release_verified': False, 'access_authorized': False,
        'source_commit': source_commit, 'runtime_commit': runtime_commit,
        'identity_basis': 'operator-declared commit pins; artifact bytes hash-verified only',
        'checker_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'manifest': None, 'evidence': None, 'missing_evidence': [],
        'prerequisites': [{'id': key, 'status': 'unresolved', 'required_evidence': text}
                          for key, text in PREREQUISITES],
        'limits': ['No network, devices, audio/artifact traversal, custody redemption or writes.',
                   'Public schema/hash consistency cannot authenticate execution, rights, independence '
                   'or completeness. No gate is passed by this inventory.'],
    }
    for key, pin in (('source_commit', source_commit), ('runtime_commit', runtime_commit)):
        if pin is None:
            report['missing_evidence'].append(key + ': exact operator commit pin absent')
        else:
            _hex(pin, 40)
    if bool(manifest) != bool(manifest_sha256) or bool(evidence) != bool(evidence_sha256):
        raise ValueError('each metadata path requires its exact SHA-256 pin')
    if (manifest or evidence) and public_root is None:
        raise ValueError('explicit public metadata root required')
    if evidence and not manifest:
        raise ValueError('evidence requires its exact run manifest')
    if not manifest:
        report['missing_evidence'].append('manifest: canonical public RunManifest absent')
        report['missing_evidence'].append('evidence: canonical EvidenceRecord inventory absent')
        report['evidence_label'] = 'no run metadata; unresolved readiness only'
        return report
    raw, reference = _metadata(public_root, manifest, manifest_sha256, 'manifest')
    run = RunManifest.model_validate(raw)
    report['manifest'] = {'reference': reference, 'pins': run.model_dump(mode='json')}
    report['evidence_label'] = ('public synthetic rehearsal only; not W7/W8 or live acceptance'
                                if run.mode in {'mock', 'replay'} and run.split == 'dev'
                                else 'public run metadata only; actual live/custody authority unverified')
    if not evidence:
        report['missing_evidence'].append('evidence: canonical EvidenceRecord inventory absent')
        return report
    raw, reference = _metadata(public_root, evidence, evidence_sha256, 'evidence')
    records = TypeAdapter(list[EvidenceRecord]).validate_python(raw)
    if len(records) > 10000:
        raise ValueError('public evidence inventory exceeds 10000 records')
    identities = set()
    for record in records:
        if record.run_id != run.run_id:
            raise ValueError('evidence run identity differs from manifest')
        if record.trial_id in identities:
            raise ValueError('duplicate evidence trial identity')
        identities.add(record.trial_id)
        if run.scheduled_tasks and record.trial_id not in run.scheduled_tasks:
            raise ValueError('evidence trial absent from manifest scheduled tasks')
        if record.observation == 'unknown' and not record.unknown_reason:
            raise ValueError('unknown evidence requires a concrete reason')
        if record.observation != 'unknown' and record.unknown_reason:
            raise ValueError('observed/estimated evidence contradicts unknown reason')
        if run.split == 'dev' and record.custody_reference is not None:
            raise ValueError('development evidence cannot claim independent custody release')
    missing = sorted(set(run.scheduled_tasks) - identities)
    report['evidence'] = {
        'reference': reference, 'records': [record.model_dump(mode='json') for record in records],
        'record_count': len(records), 'missing_scheduled_tasks': missing,
        'referenced_artifacts_verified': False,
    }
    if not records:
        report['missing_evidence'].append('evidence: empty record inventory')
    if not run.scheduled_tasks:
        report['missing_evidence'].append('scheduled_tasks: no exact task denominator supplied')
    if missing:
        report['missing_evidence'].append('evidence: scheduled tasks missing records: ' + ', '.join(missing))
    for record in records:
        absent = [key for key in ('audio_hash', 'oracle_hash') if getattr(record, key) is None]
        if absent or record.observation != 'observed':
            report['missing_evidence'].append(record.trial_id + ': ' + ', '.join(
                absent + ([] if record.observation == 'observed' else [record.observation])))
    return report


def human_readable(report: dict) -> str:
    lines = ['Acceptance readiness: ' + report['status'],
             'Product acceptance: false; access authorization: false',
             'Source commit: ' + str(report['source_commit']),
             'Runtime commit: ' + str(report['runtime_commit']), report['identity_basis'],
             report.get('evidence_label', 'invalid public metadata')]
    for key in ('manifest', 'evidence'):
        if report.get(key):
            ref = report[key]['reference']
            lines.append(f"{key}: {ref['path']} sha256={ref['sha256']}")
    lines.extend('Missing: ' + item for item in report['missing_evidence'])
    lines.extend(item['id'] + ' [unresolved]: ' + item['required_evidence']
                 for item in report['prerequisites'])
    lines.extend('Limit: ' + item for item in report['limits'])
    return '\n'.join(lines) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-root', type=Path)
    for option in ('manifest', 'manifest-sha256', 'evidence', 'evidence-sha256',
                   'source-commit', 'runtime-commit'):
        parser.add_argument('--' + option)
    parser.add_argument('--format', choices=('json', 'text'), default='json')
    args = vars(parser.parse_args(argv))
    output_format = args.pop('format')
    try:
        report = inventory(**args)
    except (ValueError, KeyError, OSError, TypeError, RecursionError):
        # Never echo arbitrary metadata values or validation excerpts into telemetry.
        report = {'schema_version': 1, 'kind': 'public_acceptance_readiness_inventory',
                  'status': 'invalid', 'product_acceptance': False, 'access_authorized': False,
                  'error': 'public metadata shape, identity, hash or path rejected'}
        print(json.dumps(report, sort_keys=True) if output_format == 'json'
              else 'Acceptance readiness: invalid\n' + report['error'])
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) if output_format == 'json'
          else human_readable(report), end='\n' if output_format == 'json' else '')
    return 1


if __name__ == '__main__':
    sys.exit(main())
