"""Independent controls for the public metadata readiness inventory, never acceptance.

Before integration, M6_READINESS_SOURCE must name the exact frozen candidate script.
All files constructed here contain only team-authored synthetic public DTOs.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def checker():
    path = Path(os.environ.get('M6_READINESS_SOURCE',
                Path(__file__).resolve().parents[2] / 'scripts' / 'acceptance_readiness.py'))
    assert path.is_absolute() and path.is_file(), 'exact absolute readiness candidate required'
    spec = importlib.util.spec_from_file_location('m6_readiness_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def public_inputs(tmp_path, report_builders):
    manifest = report_builders.run_manifest([], [])
    manifest['scheduled_tasks'] = ['trial-1', 'trial-2']
    record = {'run_id': manifest['run_id'], 'trial_id': 'trial-1',
              **{key: 'a' * 64 for key in ('trace_hash', 'audio_hash', 'ledger_hash',
                                          'oracle_hash', 'result_hash')},
              'custody_reference': None, 'observation': 'observed', 'unknown_reason': None}
    return tmp_path, manifest, [record]


def save(root, manifest, records):
    args = {'public_root': root, 'source_commit': '1' * 40, 'runtime_commit': '2' * 40}
    for key, value, name in [('manifest', manifest, 'run.json'), ('evidence', records, 'records.json')]:
        raw = json.dumps(value, sort_keys=True).encode()
        (root / name).write_bytes(raw)
        args[key] = name
        args[key + '_sha256'] = hashlib.sha256(raw).hexdigest()
    return args


def test_no_inputs_is_blocked_and_all_prerequisites_unresolved(checker):
    """what_bug_this_catches: treating no evidence as readiness or passing W7/W8."""
    report = checker.inventory()
    assert report['status'] == 'blocked'
    assert all(report[key] is False for key in ('product_acceptance', 'live_acceptance',
               'heldout_release_verified', 'access_authorized'))
    assert {row['id'] for row in report['prerequisites']} == {
        'rights', 'live', 'calibration', 'soak', 'custody_w7', 'oracle', 'independent_review_w8'}
    assert all(row['status'] == 'unresolved' for row in report['prerequisites'])


def test_complete_public_synthetic_metadata_keeps_source_runtime_separate_and_stays_blocked(checker, public_inputs):
    """what_bug_this_catches: hashes or complete DTOs being promoted to executed acceptance."""
    root, manifest, rows = public_inputs
    rows.append({**rows[0], 'trial_id': 'trial-2'})
    args = save(root, manifest, rows)
    first = checker.inventory(**args)
    assert first == checker.inventory(**args)
    assert first['source_commit'] == '1' * 40 and first['runtime_commit'] == '2' * 40
    assert first['manifest']['pins']['code_hash'] == manifest['code_hash']
    assert first['evidence']['record_count'] == 2
    assert first['evidence']['missing_scheduled_tasks'] == []
    assert first['evidence']['referenced_artifacts_verified'] is False
    assert first['status'] == 'blocked' and first['product_acceptance'] is False
    assert 'synthetic rehearsal' in first['evidence_label']
    assert all(row['status'] == 'unresolved' for row in first['prerequisites'])
    assert sorted(path.name for path in root.iterdir()) == ['records.json', 'run.json']


def test_missing_membership_and_unknown_outcomes_stay_visible(checker, public_inputs):
    """what_bug_this_catches: dropping scheduled missing trials or converting unknown to observed."""
    root, manifest, rows = public_inputs
    rows[0].update(audio_hash=None, oracle_hash=None, observation='unknown', unknown_reason='not measured')
    report = checker.inventory(**save(root, manifest, rows))
    assert report['evidence']['missing_scheduled_tasks'] == ['trial-2']
    assert any('trial-1: audio_hash, oracle_hash, unknown' == item for item in report['missing_evidence'])
    assert any('trial-2' in item for item in report['missing_evidence'])


@pytest.mark.parametrize('defect', ['wrong_run', 'duplicate_trial', 'unplanned_trial',
    'unknown_without_reason', 'observed_with_reason', 'dev_custody', 'extra_approval'])
def test_contradictory_public_record_is_invalid(checker, public_inputs, defect):
    """what_bug_this_catches: contradictory identities or self-issued approval fields being accepted."""
    root, manifest, rows = public_inputs
    if defect == 'wrong_run':
        rows[0]['run_id'] = 'other-run'
    if defect == 'duplicate_trial':
        rows.append(deepcopy(rows[0]))
    if defect == 'unplanned_trial':
        rows[0]['trial_id'] = 'trial-other'
    if defect == 'unknown_without_reason':
        rows[0]['observation'] = 'unknown'
    if defect == 'observed_with_reason':
        rows[0]['unknown_reason'] = 'contradiction'
    if defect == 'dev_custody':
        rows[0]['custody_reference'] = 'self-asserted-release'
    if defect == 'extra_approval':
        rows[0]['product_acceptance'] = True
    with pytest.raises(ValueError):
        checker.inventory(**save(root, manifest, rows))


@pytest.mark.parametrize('content', [b'{"run_id":"one","run_id":"two"}',
                                    b'{"number":NaN}', b'{"number":Infinity}'])
def test_hash_correct_but_ambiguous_or_nonfinite_json_refuses(checker, tmp_path, content):
    """what_bug_this_catches: byte hash validation replacing strict metadata parsing."""
    (tmp_path / 'record.json').write_bytes(content)
    with pytest.raises(ValueError):
        checker.inventory(public_root=tmp_path, manifest='record.json',
                          manifest_sha256=hashlib.sha256(content).hexdigest())


def test_wrong_byte_hash_refuses_before_using_metadata(checker, public_inputs):
    """what_bug_this_catches: parsing a different metadata edition than the owner pinned."""
    root, manifest, rows = public_inputs
    args = save(root, manifest, rows)
    args['evidence_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash mismatch'):
        checker.inventory(**args)


@pytest.mark.parametrize('relative', ['../run.json', '/run.json', 'C:/run.json',
    'gold/run.json', 'acceptance/run.json', 'heldout/run.json', '.hidden/run.json',
    'auth/run.json', 'private/run.json', 'public/secret.json', 'public/run.wav'])
def test_protected_relative_paths_refuse_before_byte_reader(checker, tmp_path, monkeypatch, relative):
    """what_bug_this_catches: public inventory opening protected files before path rejection."""
    def forbidden_read(*args, **kwargs):
        pytest.fail('byte reader must not be called for a protected metadata path')
    monkeypatch.setattr(checker.ArtifactReader, 'read', forbidden_read)
    with pytest.raises(ValueError):
        checker.inventory(public_root=tmp_path, manifest=relative, manifest_sha256='0' * 64)


@pytest.mark.parametrize('name', ['gold', 'acceptance', 'private', 'heldout', '.hidden',
                                 'auth', 'credential-cache', 'ssh', 'secrets'])
def test_protected_root_refuses_before_byte_reader(checker, tmp_path, monkeypatch, name):
    """what_bug_this_catches: choosing a protected directory as the public root to bypass guards.

    The byte reader is replaced before calling the candidate; no sensitive path is read.
    """
    def forbidden_read(*args, **kwargs):
        pytest.fail('protected public-root bypass reached byte reader')
    monkeypatch.setattr(checker.ArtifactReader, 'read', forbidden_read)
    root = tmp_path / name
    root.mkdir()
    with pytest.raises(ValueError):
        checker.inventory(public_root=root, manifest='record.json', manifest_sha256='0' * 64)


def test_cli_blocked_and_invalid_exit_statuses_are_machine_readable(checker):
    """what_bug_this_catches: a human-friendly message hiding exit0 or leaking invalid metadata."""
    base = [sys.executable, str(Path(checker.__file__).resolve())]
    blocked = subprocess.run(base, capture_output=True, text=True, check=False, timeout=20)
    assert blocked.returncode == 1 and json.loads(blocked.stdout)['status'] == 'blocked'
    invalid = subprocess.run(base + ['--source-commit', 'SYNTHETIC-INVALID-SENTINEL'],
                             capture_output=True, text=True, check=False, timeout=20)
    assert invalid.returncode == 2 and json.loads(invalid.stdout)['status'] == 'invalid'
    assert 'SYNTHETIC-INVALID-SENTINEL' not in invalid.stdout + invalid.stderr
    human = subprocess.run(base + ['--format', 'text'], capture_output=True, text=True,
                           check=False, timeout=20)
    assert human.returncode == 1
    assert 'Product acceptance: false' in human.stdout
