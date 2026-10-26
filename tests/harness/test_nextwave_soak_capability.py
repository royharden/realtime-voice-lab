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
