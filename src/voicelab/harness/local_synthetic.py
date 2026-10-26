"""Explicit local generated-signal capability, separate from external rights.

No input manifest, verifier or flag can turn this into public/heldout/live access.
Only this invocation's generated fixture, owned output and pinned UI are used.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
from pathlib import Path
import secrets
import time

LABEL = 'LOCAL SYNTHETIC FAKE-MEDIA: non-speech signal and fictional typed stimuli; no product acceptance'
_TOKEN = object()


def _bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _directory(path: Path) -> Path:
    supplied = Path(path).absolute()
    for component in (supplied, *supplied.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError('local_synthetic_link_refused')
    root = supplied.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('local_synthetic_directory_required')
    return root


def _json_record(path: Path, limit: int = 65_536) -> dict:
    path = Path(path).absolute()
    if any(part.lower() in {'.ssh', '.env', 'auth.json', 'credentials.json'}
           or part.lower().startswith(('.env.', 'credential', 'password', 'secret'))
           for part in path.parts):
        raise ValueError('local_synthetic_sensitive_path_refused')
    _directory(path.parent)
    if (path.suffix != '.json' or path.is_symlink() or path.is_junction()
            or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > limit):
        raise ValueError('local_synthetic_bounded_json_record_required')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('local_synthetic_record_bound')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('local_synthetic_json_object_required')
    return value


def checked_claim(path: Path, port: int) -> dict:
    """Validate the one real receipt shape; operator agent-ports check is separate."""
    claim = _json_record(path)
    if (type(port) is not int or not 22300 <= port <= 22399
            or claim.get('project') != 'm6' or claim.get('bind') != '127.0.0.1'
            or not claim.get('owner', {}).get('id') or not claim.get('instance')
            or claim.get('status') not in {'reserved', 'up', 'stopped'}
            or port not in claim.get('ports', {}).values()):
        raise ValueError('local_synthetic_matching_m6_loopback_claim_required')
    return claim


class LocalSyntheticCapability:
    """Invocation-local, generated-input scope. Deliberately not a rights reader."""
    def __init__(self, token, spec: dict, root: Path, generated: dict[str, str]):
        if token is not _TOKEN:
            raise ValueError('local_synthetic_factory_required')
        self._root = _directory(root)
        info = self._root.stat()
        self._identity = info.st_dev, info.st_ino
        self._generated = dict(generated)
        self._spec_hash = hashlib.sha256(_bytes(spec)).hexdigest()
        self._active = True

    def validate(self, spec: dict) -> None:
        if not self._active or spec.get('_local_synthetic') is not self:
            raise ValueError('local_synthetic_capability_inactive')
        value = {key: item for key, item in spec.items() if key != '_local_synthetic'}
        try:
            fingerprint = hashlib.sha256(_bytes(value)).hexdigest()
        except (TypeError, ValueError):
            raise ValueError('local_synthetic_scope_changed') from None
        if (spec.get('mode') != 'mock' or spec.get('split') != 'dev'
                or spec.get('product_acceptance') is not False
                or fingerprint != self._spec_hash):
            raise ValueError('local_synthetic_scope_changed')
        root = _directory(self._root)
        if (root.stat().st_dev, root.stat().st_ino) != self._identity:
            raise ValueError('local_synthetic_root_changed')
        for relative, expected in self._generated.items():
            path = root / relative
            _directory(path.parent)
            if (path.is_symlink() or path.is_junction() or not path.is_file()
                    or path.stat().st_nlink != 1 or path.stat().st_size > 6_000_000):
                raise ValueError('local_synthetic_generated_file_changed')
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('local_synthetic_generated_hash_mismatch')

    def close(self) -> None:
        self._active = False


def prepare_local_synthetic(out: Path, port: int, port_claim: Path,
                            web_dist: Path, web_manifest: Path) -> dict:
    """Generate a fresh closed fixture; accepts no external fixture/URL/mode input."""
    from fastapi import FastAPI
    from voicelab.server.app import mount_built_web
    from .fixtures import generate_development
    claim, build = checked_claim(port_claim, port), _json_record(web_manifest)
    parent = _directory(Path(out).absolute().parent)
    root = parent / Path(out).name
    if root.exists() or root.is_symlink() or root.is_junction():
        raise ValueError('local_synthetic_fresh_output_required')
    # Validate serving bytes before generating data or starting a process.
    hashes = mount_built_web(FastAPI(), web_dist, build, artifact_root=root / 'runtime')
    root.mkdir(exist_ok=False)
    generate_development(root / 'fixtures')
    (root / 'runtime').mkdir()
    fixture = json.loads((root / 'fixtures' / 'manifest.json').read_text(encoding='utf-8'))
    generated = {str(path.relative_to(root)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (root / 'fixtures' / name for name in ('manifest.json', 'silence.wav', 'diagnostic.wav'))}
    public = {'schema_version': 1, 'run_id': 'local-synthetic-' + secrets.token_hex(8),
              'mode': 'mock', 'split': 'dev', 'port': port, 'claim_instance': claim['instance'],
              'generated': generated, 'web_sha256': hashes, 'evidence_label': LABEL,
              'product_acceptance': False, 'speech': False, 'external_rights_authority': False}
    frozen = _bytes(public)
    (root / 'local-synthetic-manifest.json').write_bytes(frozen)
    spec = {'run_id': public['run_id'], 'mode': 'mock', 'split': 'dev',
            'frontend_url': f'http://127.0.0.1:{port}', 'relay_url': f'ws://127.0.0.1:{port}/ws',
            'fixture_root': str(root / 'fixtures'), 'relay_artifact_root': str(root / 'runtime'),
            '_fixture': fixture, '_input_wav': str(root / 'fixtures' / 'silence.wav'),
            '_fixture_stream': False, '_manifest_hash': hashlib.sha256(frozen).hexdigest(),
            '_web_dist': str(Path(web_dist).absolute()), '_web_sha256': hashes,
            'evidence_label': LABEL, 'product_acceptance': False}
    cap = LocalSyntheticCapability(_TOKEN, spec, root, generated)
    spec['_local_synthetic'] = cap
    return spec


def _relay_process(port, nonce, artifact_root, web_dist, build, run_id, manifest_hash, stop):
    """A separate own process keeps relay resource observations separate from driver."""
    import uvicorn
    from voicelab.runtime_logging import listener_logging_config
    from voicelab.server.app import Settings, create_app, mount_built_web
    app = create_app(Settings(nonce=nonce, allowed_origin=f'http://127.0.0.1:{port}',
                              artifact_root=Path(artifact_root), run_id=run_id,
                              run_manifest_hash=manifest_hash, mode='mock'))
    mount_built_web(app, Path(web_dist), build, artifact_root=Path(artifact_root))
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
        ws='websockets', ws_max_size=24_000, access_log=False, log_level='warning',
        log_config=listener_logging_config(), timeout_graceful_shutdown=5))

    async def execute():
        task = asyncio.create_task(server.serve())
        while not stop.is_set() and not task.done():
            await asyncio.sleep(.1)
        server.should_exit = True
        await task
    asyncio.run(execute())


def run_local_synthetic(args) -> dict:
    """One claimed same-origin mock server and bounded fake-media soak, no rights bypass."""
    from .browser import own_diagnostic
    from .evidence import EvidenceSink
    from .soak import run_soak
    spec = prepare_local_synthetic(args.out, args.port, args.port_claim, args.web_dist, args.web_manifest)
    root = Path(args.out).absolute()
    cap = spec['_local_synthetic']
    nonce = secrets.token_urlsafe(32)
    context = multiprocessing.get_context('spawn')
    stop = context.Event()
    process = context.Process(target=_relay_process, args=(args.port, nonce, spec['relay_artifact_root'],
        spec['_web_dist'], {'sha256': spec['_web_sha256']}, spec['run_id'], spec['_manifest_hash'], stop))
    sink = EvidenceSink(root / 'observations', spec)
    result, completed, reason = {}, False, 'local_synthetic_incomplete'
    forced_stop = False
    try:
        cap.validate(spec)
        process.start()
        deadline = time.monotonic() + 15
        while True:
            if not process.is_alive():
                raise ValueError('local_synthetic_relay_start_failed')
            try:
                health = own_diagnostic(spec, nonce, '/health')
                admission = own_diagnostic(spec, nonce, '/admission')
                arms = admission.get('arms', {})
                if (health.get('mode') == 'mock' and health.get('protocol_version') == 1
                        and arms and all(value.get('run_id') == spec['run_id'] for value in arms.values())):
                    break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError('local_synthetic_relay_start_timeout')
            time.sleep(.1)
        sink.emit('local_synthetic_started', evidence_label=LABEL, product_acceptance=False)
        result = asyncio.run(asyncio.wait_for(run_soak(spec, nonce, sink, args.profile), timeout=1900))
        result.update(evidence_label=LABEL, product_acceptance=False)
        completed = not result.get('incomplete')
        reason = 'driver_schedule_completed' if completed else 'incomplete_observation_coverage'
    finally:
        stop.set()
        if process.pid is not None:
            process.join(timeout=10)
            if process.is_alive():
                forced_stop = True
                process.terminate()  # Only this invocation's own failed relay process.
                process.join(timeout=5)
        if forced_stop or process.exitcode not in {None, 0}:
            completed = False
            reason = 'owned_relay_shutdown_incomplete'
            result.setdefault('incomplete', []).append('owned_relay_shutdown_incomplete')
            result.get('summary', {})['full_gate_pass'] = False
        if result:
            result.setdefault('references', {})['local-summary'] = sink.artifact(
                'local-summary.json', result, 'driver_summary')
        sink.close(completed=completed and not forced_stop, reason=reason,
                   references=result.get('references', {}))
        cap.close()
        # Preserve authoritative trace/snapshot bytes; bind their local-only label
        # through this exact-hash provenance index rather than rewriting evidence.
        inventory = []
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or path.is_junction():
                raise ValueError('local_synthetic_output_link_refused')
            if path.is_file():
                inventory.append({'path': path.relative_to(root).as_posix(),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'evidence_label': LABEL, 'product_acceptance': False})
        (root / 'local-synthetic-provenance.json').write_bytes(_bytes({
            'evidence_label': LABEL, 'product_acceptance': False, 'artifacts': inventory,
            'relay_pid': process.pid, 'relay_exit_code': process.exitcode,
            'forced_stop': forced_stop, 'physical_audio': 'unknown', 'external_rights_authority': False}))
    return result
