"""Offline driver boundary. A receipt is a checkpoint, not proof of ownership."""
from __future__ import annotations

import hashlib
import json
import re
import wave
from pathlib import Path
from urllib.parse import urlsplit


def loopback_url(value: str, schemes: set[str]) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in schemes or parsed.hostname != '127.0.0.1'
            or parsed.port is None or not 20000 <= parsed.port <= 29999
            or parsed.username or parsed.password or parsed.fragment or parsed.query):
        raise ValueError('explicit registered 127.0.0.1 URL required; no secrets in URL')
    return value


def owned_file(root: Path, relative: str, maximum: int = 64_000_000) -> Path:
    name = Path(relative)
    if name.is_absolute() or '..' in name.parts:
        raise ValueError('relative owned artifact path required')
    path = (root / name).resolve(strict=True)
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('artifact escape or size bound')
    return path


def load_activation(path: Path, reference: str) -> dict:
    spec = json.loads(path.read_text(encoding='utf-8'))
    if (not reference or spec.get('activation_reference') != reference
            or spec.get('runtime_authorized') is not True):
        raise ValueError('later explicit operator runtime authorization required')
    if spec.get('mode') != 'mock' or spec.get('split') != 'dev' or not spec.get('run_id'):
        raise ValueError('keyless mock development runs only; independent gates remain closed')
    loopback_url(spec['frontend_url'], {'http'})
    loopback_url(spec['relay_url'], {'ws'})
    ports = {urlsplit(spec[key]).port for key in ('frontend_url', 'relay_url')}
    claims = spec.get('port_claims', [])
    if (len(claims) != 2 or {row.get('port') for row in claims} != ports
            or not all(row.get('operator_check_reference') and Path(row['receipt']).is_file()
                       for row in claims)):
        raise ValueError('two matching existing claim receipts and immediate operator checks required')
    if not all(re.fullmatch(r'[a-f0-9]{64}', str(spec.get(key, '')))
               for key in ('code_hash', 'config_hash')):
        raise ValueError('operator-pinned candidate/configuration hashes required')
    root = Path(spec['fixture_root']).resolve(strict=True)
    if spec.get('fixture_manifest'):
        fixture, audio = load_fixture(root, spec['fixture_manifest'])
        spec['_fixture'] = fixture
        spec['_input_wav'] = str(audio)
    spec['_manifest_hash'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return spec


def load_fixture(root: Path, manifest: str) -> tuple[dict, Path]:
    fixture = json.loads(owned_file(root, manifest).read_text(encoding='utf-8'))
    if fixture.get('split') != 'dev' or fixture.get('kind') != 'diagnostic_signal_and_typed_stimulus':
        raise ValueError('development diagnostic fixture manifest required')
    audio = owned_file(root, fixture['audio']['path'])
    if hashlib.sha256(audio.read_bytes()).hexdigest() != fixture['audio']['sha256']:
        raise ValueError('development audio hash mismatch')
    with wave.open(str(audio), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 24000, 'NONE'):
            raise ValueError('development microphone requires mono PCM16 24k WAV')
        if not 1 <= wav.getnframes() <= 24000 * 120:
            raise ValueError('development microphone fixture duration bound')
    if (fixture.get('speech') is not False or fixture.get('license', {}).get('reviewed') is not True
            or not fixture['license'].get('reference')):
        raise ValueError('operator rights review required; generated tones are not licensed speech')
    if not isinstance(fixture.get('turns'), list) or not 1 <= len(fixture['turns']) <= 8 or not all(
            isinstance(text, str) and 0 < len(text) <= 2000 for text in fixture['turns']):
        raise ValueError('bounded explicitly reviewed diagnostic wording required')
    return fixture, audio


def bind_task_fixture(spec: dict, task: dict, *, planned: bool) -> dict:
    if not planned:
        if not spec.get('_fixture') or not spec.get('_input_wav'):
            raise ValueError('single-fixture diagnostic mode requires reviewed default fixture')
        return spec
    identity = task.get('fixture_id')
    reference = spec.get('task_fixtures', {}).get(identity)
    if not isinstance(reference, str):
        raise ValueError('each planned fixture_id needs an explicit reviewed manifest; no global substitution')
    root = Path(spec['fixture_root']).resolve(strict=True)
    fixture, audio = load_fixture(root, reference)
    if (fixture.get('fixture_id'), fixture.get('wording_family'), fixture.get('speaker')) != (
            identity, task.get('family'), task.get('speaker')):
        raise ValueError('task fixture/family/speaker provenance mismatch')
    if len(fixture['turns']) != len(task['logical_turn_ids']):
        raise ValueError('fixture wording must bind every prespecified task turn')
    return {**spec, '_fixture': fixture, '_input_wav': str(audio)}
