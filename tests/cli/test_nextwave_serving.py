"""Same-origin build serving contracts; in-process only, never starts a listener.

All files are generated synthetic bytes. The CLI checkpoint is a boundary double,
not evidence of a real port claim or authorization to bind a socket.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from voicelab import cli
from voicelab.server import app as server


@pytest.fixture
def build(tmp_path):
    root = tmp_path / 'build'
    (root / 'assets').mkdir(parents=True)
    contents = {'index.html': b'<html>synthetic built page</html>',
                'audio-worklet.js': b'/* synthetic worklet */',
                'assets/app.js': b'/* synthetic script */',
                'assets/app.css': b'/* synthetic style */'}
    for name, raw in contents.items():
        (root / name).write_bytes(raw)
    manifest = {'sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}}
    return root, manifest, contents


def mount(build, tmp_path):
    root, manifest, _ = build
    app = server.create_app(server.Settings(nonce='synthetic-nonce-0123456789',
        allowed_origin='http://127.0.0.1:22342', artifact_root=tmp_path / 'runtime'))
    function = getattr(server, 'mount_built_web', None)
    assert callable(function), 'declared same-origin mount_built_web seam missing'
    function(app, root, manifest, artifact_root=tmp_path / 'runtime')
    return app


def test_exact_build_serves_after_protected_routes_and_retains_pinned_bytes(build, tmp_path):
    """what_bug_this_catches: static mount shadows relay security or rereads replaced files."""
    app = mount(build, tmp_path)
    root, _, contents = build
    (root / 'assets/app.js').write_bytes(b'replacement bytes must not be served')
    with TestClient(app, base_url='http://127.0.0.1:22342') as client:
        for name, raw in contents.items():
            response = client.get('/' if name == 'index.html' else '/' + name)
            assert response.status_code == 200
            assert response.content == raw
        assert client.get('/not-in-build.txt').status_code == 404
        assert client.post('/assets/app.js').status_code == 405
        assert client.post('/admission', json={}).status_code in {401, 403}
        response = client.post('/admission', json={}, headers={
            'X-VoiceLab-Nonce': 'synthetic-nonce-0123456789',
            'Origin': 'http://127.0.0.1:22342'})
        assert response.status_code == 200
        assert response.headers.get('content-type', '').startswith('application/json')


@pytest.mark.parametrize('kind', ['extra_file', 'extra_asset', 'hash_mismatch',
                                 'path_escape', 'absolute_path', 'missing_file',
                                 'runtime_inside_build', 'build_inside_runtime'])
def test_invalid_build_refused_before_static_mount(build, tmp_path, kind):
    """what_bug_this_catches: arbitrary/source/unrelated bytes become publicly servable,
    an unpinned asset is accepted, or mutable runtime artifacts overlap the web root."""
    root, manifest, _ = build
    artifacts = tmp_path / 'runtime'
    if kind == 'extra_file':
        (root / 'unrelated.txt').write_text('synthetic unrelated marker')
    elif kind == 'extra_asset':
        (root / 'assets/unrelated.js').write_text('synthetic unrelated marker')
    elif kind == 'hash_mismatch':
        (root / 'assets/app.js').write_bytes(b'changed')
    elif kind in {'path_escape', 'absolute_path'}:
        manifest['sha256']['../outside.js' if kind == 'path_escape' else str(tmp_path / 'outside.js')] = '0' * 64
    elif kind == 'missing_file':
        # Build a fresh incomplete directory, retaining every previously created file.
        root = tmp_path / 'incomplete'
        root.mkdir()
    elif kind == 'runtime_inside_build':
        artifacts = root / 'runtime'
    else:
        artifacts = tmp_path
    function = getattr(server, 'mount_built_web', None)
    assert callable(function), 'declared same-origin mount_built_web seam missing'
    with pytest.raises((ValueError, OSError)):
        function(server.create_app(server.Settings(nonce='synthetic-nonce-0123456789',
            allowed_origin='http://127.0.0.1:22342', artifact_root=artifacts)), root, manifest,
            artifact_root=artifacts)


def test_cli_web_pair_mounts_exact_build_without_starting_real_listener(build, tmp_path, monkeypatch):
    """what_bug_this_catches: declared CLI flags do not reach same-origin composition."""
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: calls.append((app, kwargs)))
    monkeypatch.setenv('VOICELAB_NONCE', 'synthetic-nonce-0123456789')
    root, manifest, contents = build
    manifest_path = tmp_path / 'build-record.json'
    manifest_path.write_text(json.dumps(manifest))
    checkpoint = tmp_path / 'synthetic-checkpoint.json'
    checkpoint.write_text('{}')  # No listener: CLI boundary double only.
    result = cli.main(['serve', '--port', '22342', '--port-claim', str(checkpoint),
        '--allowed-origin', 'http://127.0.0.1:22342', '--artifact-root', str(tmp_path / 'runtime'),
        '--web-dist', str(root), '--web-manifest', str(manifest_path)])
    assert result in {None, 0}
    assert len(calls) == 1
    app, options = calls[0]
    assert options['host'] == '127.0.0.1' and options['port'] == 22342
    with TestClient(app, base_url='http://127.0.0.1:22342') as client:
        assert client.get('/').content == contents['index.html']


@pytest.mark.parametrize('flag', ['--web-dist', '--web-manifest'])
def test_cli_partial_web_selection_refuses_without_listener(build, tmp_path, monkeypatch, capsys, flag):
    """what_bug_this_catches: partial UI selection silently falls back to relay-only."""
    import uvicorn
    calls = []
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: calls.append(True))
    monkeypatch.setenv('VOICELAB_NONCE', 'synthetic-nonce-0123456789')
    root, manifest, _ = build
    manifest_path = tmp_path / 'build-record.json'
    manifest_path.write_text(json.dumps(manifest))
    checkpoint = tmp_path / 'synthetic-checkpoint.json'
    checkpoint.write_text('{}')
    args = ['serve', '--port', '22342', '--port-claim', str(checkpoint),
            '--allowed-origin', 'http://127.0.0.1:22342',
            '--artifact-root', str(tmp_path / 'runtime'),
            flag, str(root if flag == '--web-dist' else manifest_path)]

    def assert_pair_refused(invoke):
        with pytest.raises(SystemExit) as refused:
            invoke(args)
        assert refused.value.code == 2
        assert '--web-dist and --web-manifest must be supplied together' in capsys.readouterr().err
        assert calls == []

    assert_pair_refused(cli.main)
    assert calls == []

    # Countercontrol: ignore the partial selection and reach otherwise valid relay-only
    # startup. The same oracle must fail; uvicorn remains intercepted throughout.
    def ignoring_partial(argv):
        return cli.main(argv[:-2])

    with pytest.raises(pytest.fail.Exception, match='DID NOT RAISE'):
        assert_pair_refused(ignoring_partial)
    assert calls == [True]
