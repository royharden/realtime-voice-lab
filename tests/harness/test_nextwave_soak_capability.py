"""Independent controls for D10's declared generated-input/serving boundary.

Constructs only synthetic claim DTOs, tiny fake build bytes and generated non-speech
fixtures. No listening server/browser/process, real port claim or rights is created.
Execute with an absolute frozen source/config snapshot; this is not a built-UI run.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def boundaries():
    from voicelab.harness import local_synthetic
    from voicelab.server.app import mount_built_web
    return local_synthetic, mount_built_web


@pytest.fixture
def inputs(tmp_path):
    build = tmp_path / 'fake-built-bytes'
    (build / 'assets').mkdir(parents=True)
    files = {'index.html': b'<p>synthetic fixture only</p>',
             'audio-worklet.js': b'/* synthetic fixture worklet */',
             'assets/main.js': b'/* synthetic fixture bundle */',
             'assets/main.css': b'/* synthetic fixture style */'}
    for name, raw in files.items():
        (build / name).write_bytes(raw)
    manifest = {'sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    build_record = tmp_path / 'build.json'
    build_record.write_text(json.dumps(manifest))
    claim = {'project': 'm6', 'bind': '127.0.0.1', 'owner': {'id': 'synthetic-only'},
             'instance': 'test-double', 'status': 'reserved', 'ports': {'http': 22341}}
    receipt = tmp_path / 'claim.json'
    receipt.write_text(json.dumps(claim))
    return {'root': tmp_path, 'build': build, 'manifest': manifest, 'build_record': build_record,
            'files': files, 'claim': claim, 'receipt': receipt}


def prepare(module, values):
    return module.prepare_local_synthetic(values['root'] / 'fresh-run', 22341,
        values['receipt'], values['build'], values['build_record'])


def test_generated_capability_positive_scope_and_old_rights_reader_refusal(boundaries, inputs):
    """what_bug_this_catches: local synthetic capability being relabeled as external authority."""
    from voicelab.harness.access import public_reader
    from voicelab.harness.browser import _validate_driver_access
    from voicelab.evaluation.exposure import PublicReadAccess
    module, _ = boundaries
    spec = prepare(module, inputs)
    capability = spec['_local_synthetic']
    assert not isinstance(capability, PublicReadAccess)
    assert spec['mode'] == 'mock' and spec['split'] == 'dev'
    assert spec['product_acceptance'] is False
    assert spec['frontend_url'] == 'http://127.0.0.1:22341'
    assert spec['relay_url'] == 'ws://127.0.0.1:22341/ws'
    _validate_driver_access(spec)
    with pytest.raises(ValueError, match='actual_D5_PublicReadAccess_required'):
        public_reader(Path(spec['fixture_root']), {}, capability)
    public = json.loads((inputs['root'] / 'fresh-run' / 'local-synthetic-manifest.json').read_bytes())
    assert public['external_rights_authority'] is False and public['speech'] is False
    assert public['product_acceptance'] is False
    assert hashlib.sha256((inputs['root'] / 'fresh-run' / 'local-synthetic-manifest.json').read_bytes()).hexdigest() == spec['_manifest_hash']
    capability.close()
    with pytest.raises(ValueError):
        _validate_driver_access(spec)


@pytest.mark.parametrize('field, value', [
    ('mode', 'live'), ('split', 'heldout'), ('product_acceptance', True),
    ('frontend_url', 'https://example.invalid'), ('relay_url', 'ws://example.invalid/ws'),
    ('fixture_root', 'different-root'), ('_input_wav', 'external-file.wav'),
    ('_manifest_hash', 'f' * 64), ('run_id', 'other-run'),
])
def test_generated_capability_refuses_mutated_frozen_scope(boundaries, inputs, field, value):
    """what_bug_this_catches: reusing an opaque local capability for live/private/imported inputs."""
    module, _ = boundaries
    spec = prepare(module, inputs)
    altered = {**spec, field: value}
    with pytest.raises(ValueError):
        spec['_local_synthetic'].validate(altered)


@pytest.mark.parametrize('counterfeit', [None, {}, {'approved': True, 'product_acceptance': False}])
def test_json_flags_cannot_replace_local_capability(boundaries, counterfeit):
    """what_bug_this_catches: a serialized flag selecting browser activation before rights checks."""
    from voicelab.harness.browser import _validate_driver_access
    with pytest.raises(ValueError):
        _validate_driver_access({'mode': 'mock', 'split': 'dev', '_local_synthetic': counterfeit})


def test_generated_fixture_byte_tamper_and_output_reuse_refuse(boundaries, inputs):
    """what_bug_this_catches: mutable or previous-run fixtures inheriting the original capability."""
    module, _ = boundaries
    spec = prepare(module, inputs)
    with pytest.raises(ValueError):
        prepare(module, inputs)
    Path(spec['_input_wav']).write_bytes(b'synthetic tampered bytes')
    with pytest.raises(ValueError):
        spec['_local_synthetic'].validate(spec)


@pytest.mark.parametrize('port', [22299, 22400, True])
def test_claim_dto_cannot_expand_roy_m6_port_block(boundaries, inputs, port):
    """what_bug_this_catches: an m6 label authorizing ports outside Roy's22300..22399 grant."""
    module, _ = boundaries
    claim = deepcopy(inputs['claim'])
    claim['ports']['http'] = port
    inputs['receipt'].write_text(json.dumps(claim))
    with pytest.raises(ValueError):
        module.checked_claim(inputs['receipt'], port)


@pytest.mark.parametrize('field, value', [('project', 'other-team'), ('bind', '0.0.0.0'),
                                        ('status', 'released'), ('owner', {})])
def test_foreign_or_unowned_claim_refuses_before_output_generation(boundaries, inputs, field, value):
    """what_bug_this_catches: foreign/wildcard/unowned resources entering synthetic orchestration."""
    module, _ = boundaries
    inputs['claim'][field] = value
    inputs['receipt'].write_text(json.dumps(inputs['claim']))
    with pytest.raises(ValueError):
        prepare(module, inputs)
    assert not (inputs['root'] / 'fresh-run').exists()


def test_static_uses_exact_pinned_bytes_and_never_exposes_unrelated_artifacts(boundaries, inputs):
    """what_bug_this_catches: serving changed disk bytes or exposing protected relay artifacts."""
    _, mount = boundaries
    app = FastAPI()
    @app.get('/protected-route')
    def protected():
        return {'existing': True}
    mount(app, inputs['build'], inputs['manifest'], artifact_root=inputs['root'] / 'runtime')
    (inputs['build'] / 'index.html').write_bytes(b'replacement must not be served')
    with TestClient(app) as client:
        assert client.get('/').content == inputs['files']['index.html']
        assert client.get('/protected-route').json() == {'existing': True}
        assert client.get('/runtime/journal.sqlite3').status_code == 404
        response = client.get('/assets/main.js')
        assert response.content == inputs['files']['assets/main.js']
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['cache-control'] == 'no-store'
        assert client.head('/assets/main.js').content == b''
        assert client.post('/assets/main.js').status_code == 405


@pytest.mark.parametrize('defect', ['hash', 'unrelated_root', 'unrelated_asset',
                                  'path_escape', 'overlap', 'incomplete_manifest'])
def test_static_closed_build_refusal(boundaries, inputs, defect):
    """what_bug_this_catches: partial/hash-mismatched/source/overlapping files being served."""
    _, mount = boundaries
    runtime = inputs['root'] / 'runtime'
    manifest = deepcopy(inputs['manifest'])
    if defect == 'hash':
        manifest['sha256']['index.html'] = '0' * 64
    if defect == 'unrelated_root':
        (inputs['build'] / 'source.py').write_bytes(b'synthetic unrelated source')
    if defect == 'unrelated_asset':
        (inputs['build'] / 'assets' / 'extra.js').write_bytes(b'synthetic extra asset')
    if defect == 'path_escape':
        manifest['sha256']['../outside.js'] = '0' * 64
    if defect == 'overlap':
        runtime = inputs['build'] / 'runtime'
    if defect == 'incomplete_manifest':
        manifest['sha256'].pop('audio-worklet.js')
    with pytest.raises(ValueError):
        mount(FastAPI(), inputs['build'], manifest, artifact_root=runtime)


@pytest.mark.parametrize('incomplete, forced', [(False, False), (True, False), (False, True)])
def test_local_producer_persists_exact_hash_lineage_and_cleanup_status(boundaries, inputs, monkeypatch, incomplete, forced):
    """what_bug_this_catches: synthetic provenance detaching from bytes or forced stop looking complete.

    Real factory and EvidenceSink; relay process, health response and workload are
    explicit doubles. This launches no process, listener, browser or actual soak.
    """
    from voicelab.harness import browser, soak
    module, _ = boundaries

    class Stop:
        stopped = False

        def set(self):
            self.stopped = True

    stop = Stop()

    class Process:
        pid = None
        exitcode = None
        terminated = False

        def start(self):
            self.pid = 999999  # synthetic identifier, never an OS process target

        def join(self, timeout):
            if stop.stopped and (not forced or self.terminated):
                self.exitcode = 0

        def is_alive(self):
            return self.pid is not None and self.exitcode is None

        def terminate(self):
            self.terminated = True

    process = Process()
    context = SimpleNamespace(Event=lambda: stop, Process=lambda **kwargs: process)
    monkeypatch.setattr(module.multiprocessing, 'get_context', lambda kind: context)
    diagnostic_paths = []

    def diagnostic(spec, nonce, path):
        diagnostic_paths.append(path)
        if path == '/health':
            return {'mode': 'mock', 'protocol_version': 1}
        if path == '/admission':
            return {'arms': {arm: {'run_id': spec['run_id']} for arm in ('V', 'C')}}
        pytest.fail('unexpected diagnostic request: ' + path)

    monkeypatch.setattr(browser, 'own_diagnostic', diagnostic)

    async def workload(spec, nonce, sink, profile):
        assert profile == 'short' and spec['product_acceptance'] is False
        sink.emit('synthetic_control', evidence_label=module.LABEL, product_acceptance=False)
        return {'references': {}, 'summary': {'full_gate_pass': False, 'product_acceptance': False},
                'incomplete': ['controlled-missing-terminal'] if incomplete else []}

    monkeypatch.setattr(soak, 'run_soak', workload)
    args = SimpleNamespace(out=inputs['root'] / 'fresh-run', port=22341,
        port_claim=inputs['receipt'], web_dist=inputs['build'],
        web_manifest=inputs['build_record'], profile='short')
    module.run_local_synthetic(args)
    assert diagnostic_paths == ['/health', '/admission']
    provenance = json.loads((args.out / 'local-synthetic-provenance.json').read_bytes())
    public_raw = (args.out / 'local-synthetic-manifest.json').read_bytes()
    public = json.loads(public_raw)
    handoff = json.loads((args.out / 'observations' / 'handoff.json').read_bytes())
    assert handoff['run_id'] == public['run_id']
    assert handoff['manifest_hash'] == hashlib.sha256(public_raw).hexdigest()
    assert handoff['driver_completed'] is (not incomplete and not forced)
    assert handoff['product_acceptance'] is False and handoff['heldout_release'] is False
    assert provenance['product_acceptance'] is False
    assert provenance['external_rights_authority'] is False
    assert provenance['forced_stop'] is forced
    assert stop.stopped and process.exitcode == 0
    references = {row['path'] for row in provenance['artifacts']}
    assert {'observations/driver.jsonl', 'observations/handoff.json',
            'observations/local-summary.json'} <= references
    for row in provenance['artifacts']:
        assert row['product_acceptance'] is False and row['evidence_label'] == module.LABEL
        assert hashlib.sha256((args.out / row['path']).read_bytes()).hexdigest() == row['sha256']


@pytest.mark.parametrize('admission_defect', ['absent', 'wrong-run'])
def test_local_producer_refuses_invalid_admission_before_workload(
        boundaries, inputs, monkeypatch, admission_defect):
    """what_bug_this_catches: healthy relay bypassing missing or foreign-run admission.

    All process/diagnostic/workload boundaries are doubles. A local fake clock
    expires the readiness deadline immediately; no real sleep or network occurs.
    """
    from voicelab.harness import browser, soak
    module, _ = boundaries
    stop = SimpleNamespace(stopped=False)
    stop.set = lambda: setattr(stop, 'stopped', True)

    class Process:
        pid = None
        exitcode = None
        terminated = False

        def start(self):
            self.pid = 999999  # synthetic identifier, never an OS process target

        def join(self, timeout):
            if stop.stopped:
                self.exitcode = 0

        def is_alive(self):
            return self.pid is not None and self.exitcode is None

        def terminate(self):
            self.terminated = True

    process = Process()
    context = SimpleNamespace(Event=lambda: stop, Process=lambda **kwargs: process)
    monkeypatch.setattr(module.multiprocessing, 'get_context', lambda kind: context)
    ticks = iter((0, 16))
    monkeypatch.setattr(module, 'time', SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda seconds: pytest.fail('unexpected sleep')))
    diagnostic_paths, observed_specs, workload_calls = [], [], []

    def diagnostic(spec, nonce, path):
        diagnostic_paths.append(path)
        observed_specs.append(spec)
        if path == '/health':
            return {'mode': 'mock', 'protocol_version': 1}
        if path == '/admission':
            if admission_defect == 'absent':
                return {}
            return {'arms': {arm: {'run_id': spec['run_id'] + '-foreign'}
                             for arm in ('V', 'C')}}
        pytest.fail('unexpected diagnostic request: ' + path)

    async def workload(*args, **kwargs):
        workload_calls.append(True)
        pytest.fail('workload must not start without current-run admission')

    monkeypatch.setattr(browser, 'own_diagnostic', diagnostic)
    monkeypatch.setattr(soak, 'run_soak', workload)
    args = SimpleNamespace(out=inputs['root'] / 'fresh-run', port=22341,
        port_claim=inputs['receipt'], web_dist=inputs['build'],
        web_manifest=inputs['build_record'], profile='short')
    with pytest.raises(TimeoutError, match='^local_synthetic_relay_start_timeout$'):
        module.run_local_synthetic(args)
    assert diagnostic_paths == ['/health', '/admission'] and not workload_calls
    assert stop.stopped and process.exitcode == 0 and not process.terminated
    spec = observed_specs[0]
    with pytest.raises(ValueError, match='local_synthetic_capability_inactive'):
        spec['_local_synthetic'].validate(spec)
    handoff = json.loads((args.out / 'observations' / 'handoff.json').read_bytes())
    assert handoff['driver_completed'] is False
    assert handoff['product_acceptance'] is False and handoff['heldout_release'] is False
    # Failed startup may retain diagnostic metadata, never admitted workload/results.
    # The exact closed shape rejects completed/observed/pass claims at any new key.
    summary_raw = (args.out / 'observations' / 'local-summary.json').read_bytes()
    summary = json.loads(summary_raw)
    assert summary == {
        'incomplete': ['local_synthetic_incomplete'],
        'summary': {'full_gate_pass': False, 'product_acceptance': False},
        'evidence_label': module.LABEL, 'product_acceptance': False,
    }, 'startup summary must remain incomplete with zero workload and no success metadata'
    public_raw = (args.out / 'local-synthetic-manifest.json').read_bytes()
    public = json.loads(public_raw)
    assert handoff['run_id'] == public['run_id'] == spec['run_id']
    assert handoff['manifest_hash'] == spec['_manifest_hash'] == hashlib.sha256(public_raw).hexdigest()
    assert public['mode'] == spec['mode'] == 'mock' and public['split'] == spec['split'] == 'dev'
    assert public['product_acceptance'] is False and public['external_rights_authority'] is False
    assert public['web_sha256'] == spec['_web_sha256'] == inputs['manifest']['sha256']
    for relative, expected in public['generated'].items():
        assert hashlib.sha256((args.out / relative).read_bytes()).hexdigest() == expected
    driver_raw = (args.out / 'observations' / 'driver.jsonl').read_bytes()
    events = [json.loads(raw) for raw in driver_raw.splitlines()]
    assert len(events) == 1  # No local_synthetic_started/admission/workload event.
    terminal = events[0]
    assert terminal['kind'] == 'driver_ended' and terminal['completed'] is False
    assert terminal['reason'] == 'bounded_driver_step_failed'
    assert terminal['run_id'] == spec['run_id']
    assert handoff['counts'] == {'driver_ended': 1}
    assert handoff['references'] == {
        'driver': {'path': 'driver.jsonl', 'kind': 'driver_observations',
                   'sha256': hashlib.sha256(driver_raw).hexdigest()},
        'local-summary': {'path': 'local-summary.json', 'kind': 'driver_summary',
                          'sha256': hashlib.sha256(summary_raw).hexdigest()},
    }
    provenance = json.loads((args.out / 'local-synthetic-provenance.json').read_bytes())
    assert provenance['relay_exit_code'] == 0 and provenance['forced_stop'] is False
    assert provenance['product_acceptance'] is False and provenance['external_rights_authority'] is False
    artifacts = {row['path']: row for row in provenance['artifacts']}
    assert {'local-synthetic-manifest.json', 'observations/driver.jsonl',
            'observations/handoff.json', 'observations/local-summary.json'} <= artifacts.keys()
    for relative, row in artifacts.items():
        assert row['evidence_label'] == module.LABEL and row['product_acceptance'] is False
        assert hashlib.sha256((args.out / relative).read_bytes()).hexdigest() == row['sha256']


@pytest.mark.parametrize('false_claim', ['completed', 'observed_turns', 'full_gate_pass'])
def test_failed_startup_summary_predicate_rejects_false_success_metadata(
        boundaries, inputs, monkeypatch, false_claim):
    """what_bug_this_catches: permissive summary existence checks accepting invented success.

    Corrupt only the actual persisted summary at the artifact boundary, then run
    the full failed-admission control. No workload, process or network is started.
    """
    from voicelab.harness.evidence import EvidenceSink
    original = EvidenceSink.artifact
    injected = []

    def artifact(sink, name, value, kind):
        if name == 'local-summary.json':
            value = deepcopy(value)
            if false_claim == 'full_gate_pass':
                value['summary']['full_gate_pass'] = True
            else:
                value[false_claim] = True if false_claim == 'completed' else 1
            injected.append(false_claim)
        return original(sink, name, value, kind)

    monkeypatch.setattr(EvidenceSink, 'artifact', artifact)
    with pytest.raises(AssertionError, match='startup summary must remain incomplete'):
        test_local_producer_refuses_invalid_admission_before_workload(
            boundaries, inputs, monkeypatch, 'absent')
    assert injected == [false_claim]
