"""Reviewed development fixture import/binding source; no approval authentication.

Only explicit owned files are read. Spoken text is returned solely in the private
in-memory task binding, never in public validation/import receipts. D5 owns the
external authorization/custody/rights/freeze gates. Nothing executes on import.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct
from decimal import Decimal, InvalidOperation


MAX_AUDIO_BYTES = 6_000_000
MAX_REFERENCE_BYTES = 1_000_000
MAX_TEXT_BYTES = 8_192
MAX_CATALOG_BYTES = 4_000_000
RATE = 24_000


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True, allow_nan=False) + '\n').encode('utf-8')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('catalog_duplicate_json_key')
            result[key] = value
        return result
    def constant(_value):
        raise ValueError('catalog_nonfinite_json_value')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def _identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', value):
        raise ValueError(name + '_identifier_required')
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('sha256_required')
    return value


def _settings(value: object, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError('catalog_settings_depth_bound')
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError('catalog_settings_field_bound')
        for key, item in value.items():
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key):
                raise ValueError('catalog_settings_metadata_key_required')
            folded = key.casefold()
            if (folded in {'text', 'prompt', 'transcript', 'audio', 'pcm16', 'samples'}
                    or any(marker in folded for marker in ('auth', 'header', 'credential', 'secret', 'token', 'apikey', 'nonce'))):
                raise ValueError('catalog_settings_content_or_credential_field_refused')
            _settings(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 64:
            raise ValueError('catalog_settings_list_bound')
        for item in value:
            _settings(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 256 or '://' in value or value.casefold().startswith('bearer '):
            raise ValueError('catalog_settings_bounded_metadata_value_required')
    elif value is not None and type(value) not in {bool, int, float}:
        raise ValueError('catalog_settings_json_values_required')


def _closed(value: object, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - (optional or set()):
        raise ValueError('catalog_closed_object_shape')
    return value


def _root(root: Path) -> Path:
    absolute = root.absolute()
    for ancestor in (absolute, *absolute.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError('catalog_root_link_refused')
    resolved = absolute.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError('catalog_root_directory_required')
    return resolved


def _relative(value: object) -> Path:
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('catalog_owned_relative_path_required')
    path = Path(value)
    if path.is_absolute() or any(part in {'', '.', '..'} or ':' in part for part in path.parts):
        raise ValueError('catalog_path_escape_refused')
    # Never read credential/token files on the pretext of fixture provenance.
    for part in path.parts:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', part) or part.endswith('.'):
            raise ValueError('catalog_asset_path_component_grammar')
        folded = re.sub(r'[^a-z0-9]', '', part.casefold())
        if (part.casefold().startswith('.env') or folded.startswith('auth')
                or any(marker in folded for marker in ('credential', 'token', 'secret', 'apikey'))):
            raise ValueError('catalog_sensitive_path_refused')
        if part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *{f'COM{i}' for i in range(1, 10)}, *{f'LPT{i}' for i in range(1, 10)}}:
            raise ValueError('catalog_device_path_refused')
    return path


def _read(root: Path, reference: dict, maximum: int, kind: str, reader=None) -> tuple[bytes, dict]:
    _closed(reference, {'path', 'sha256'})
    name = _relative(reference['path'])
    cursor = root
    for part in name.parts:
        cursor /= part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError('catalog_asset_link_refused')
    path = cursor.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('catalog_asset_escape_refused')
    expected = _sha(reference['sha256'])
    before = path.stat()
    if before.st_size > maximum:
        raise ValueError('catalog_asset_size_bound')
    if reader is None:
        with path.open('rb') as handle:
            raw = handle.read(maximum + 1)
    else:
        from .access import read_owned
        raw = read_owned(reader, root, name.as_posix(), maximum=maximum)
    after = path.stat()
    if len(raw) > maximum or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ValueError('catalog_asset_changed_during_read')
    if _digest(raw) != expected:
        raise ValueError('catalog_asset_hash_mismatch')
    return raw, {'path': name.as_posix(), 'sha256': expected, 'kind': kind}


def _wav(raw: bytes, declared: dict) -> dict:
    _closed(declared, {'path', 'sha256', 'pcm_sha256', 'format', 'channels', 'sample_rate',
                       'frame_count', 'duration_seconds'})
    if (declared['format'], declared['channels'], declared['sample_rate']) != ('PCM16LE', 1, RATE):
        raise ValueError('catalog_requires_mono_pcm16_24k')
    if type(declared['channels']) is not int or type(declared['sample_rate']) is not int:
        raise ValueError('catalog_audio_integer_format_fields')
    if (len(raw) < 44 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE'
            or struct.unpack_from('<I', raw, 4)[0] != len(raw) - 8):
        raise ValueError('catalog_wav_container_truncated_or_trailing')
    position, chunks, pcm = 12, [], None
    while position < len(raw):
        if position + 8 > len(raw):
            raise ValueError('catalog_wav_chunk_header_truncated')
        tag, size = struct.unpack_from('<4sI', raw, position)
        start, stop = position + 8, position + 8 + size
        padded_stop = stop + (size & 1)
        if stop > len(raw) or padded_stop > len(raw):
            raise ValueError('catalog_wav_chunk_truncated')
        if tag == b'fmt ' and not chunks and size == 16:
            if struct.unpack_from('<HHIIHH', raw, start) != (1, 1, RATE, RATE * 2, 2, 16):
                raise ValueError('catalog_wav_pcm_format_mismatch')
        elif tag == b'data' and chunks == [b'fmt '] and size > 0 and size % 2 == 0:
            pcm = raw[start:stop]
        else:
            raise ValueError('catalog_wav_unknown_duplicate_or_unexplained_chunk')
        chunks.append(tag)
        position = padded_stop
    if chunks != [b'fmt ', b'data'] or position != len(raw) or pcm is None:
        raise ValueError('catalog_wav_exact_fmt_data_required')
    count = len(pcm) // 2
    if (type(declared['frame_count']) is not int or declared['frame_count'] != count
            or not 1 <= count <= RATE * 120):
        raise ValueError('catalog_audio_frame_count_or_duration_bound')
    if _sha(declared['pcm_sha256']) != _digest(pcm):
        raise ValueError('catalog_pcm_hash_mismatch')
    if isinstance(declared['duration_seconds'], bool):
        raise ValueError('catalog_audio_duration_number_required')
    try:
        duration = Decimal(str(declared['duration_seconds']))
    except (InvalidOperation, ValueError):
        raise ValueError('catalog_audio_duration_number_required') from None
    if not duration.is_finite() or duration <= 0 or abs(duration - Decimal(count) / RATE) > Decimal(1) / RATE:
        raise ValueError('catalog_audio_duration_mismatch')
    return {'frame_count': count, 'sample_rate': RATE, 'channels': 1, 'format': 'PCM16LE',
            'duration_seconds': str(Decimal(count) / RATE), 'pcm_sha256': _digest(pcm)}


def _fixture(root: Path, row: dict, reader=None) -> tuple[dict, list[dict], list[str]]:
    _closed(row, {'fixture_id', 'wording_family', 'speaker', 'task_template_id', 'audio',
                  'license', 'generator', 'generator_version', 'voice', 'settings',
                  'generator_reference', 'voice_reference', 'provenance_reference',
                  'utterances', 'task_bindings', 'synthetic', 'speech'})
    for field in ('fixture_id', 'wording_family', 'speaker', 'task_template_id',
                  'generator', 'voice'):
        _identifier(row[field], field)
    if (not isinstance(row['generator_version'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,95}', row['generator_version'])):
        raise ValueError('catalog_generator_version_pin_required')
    if row['synthetic'] is not True or row['speech'] is not True:
        raise ValueError('catalog_reviewed_synthetic_speech_declarations_required')
    if not isinstance(row['settings'], dict) or not row['settings'] or len(_canonical(row['settings'])) > 16_384:
        raise ValueError('catalog_generator_settings_required_and_bounded')
    _settings(row['settings'])
    license_info = _closed(row['license'], {'reviewed', 'input_use', 'output_redistribution', 'reference'})
    if any(license_info[field] is not True for field in ('reviewed', 'input_use', 'output_redistribution')):
        raise ValueError('catalog_explicit_reviewed_input_output_rights_required')
    assets = []
    for field, kind in (('generator_reference', 'generator_provenance'),
                        ('voice_reference', 'voice_provenance'), ('provenance_reference', 'fixture_provenance')):
        _, metadata = _read(root, row[field], MAX_REFERENCE_BYTES, kind, reader)
        assets.append(metadata)
    _, metadata = _read(root, license_info['reference'], MAX_REFERENCE_BYTES, 'license_review', reader)
    assets.append(metadata)
    audio = row['audio']
    if not isinstance(audio, dict):
        raise ValueError('catalog_audio_object_required')
    raw, metadata = _read(root, {key: audio.get(key) for key in ('path', 'sha256')}, MAX_AUDIO_BYTES, 'audio', reader)
    audio_observation = _wav(raw, audio)
    assets.append(metadata)
    utterances = row['utterances']
    if not isinstance(utterances, list) or not 1 <= len(utterances) <= 8:
        raise ValueError('catalog_one_to_eight_ordered_utterances_required')
    identifiers, texts, observed, previous_end = set(), [], [], 0
    for utterance in utterances:
        _closed(utterance, {'utterance_id', 'start_frame', 'end_frame', 'text'})
        identity = _identifier(utterance['utterance_id'], 'utterance')
        start, end = utterance['start_frame'], utterance['end_frame']
        if (identity in identifiers or type(start) is not int or type(end) is not int
                or not previous_end <= start < end <= audio_observation['frame_count']):
            raise ValueError('catalog_utterance_order_window_or_identity_invalid')
        identifiers.add(identity)
        previous_end = end
        raw_text, metadata = _read(root, utterance['text'], MAX_TEXT_BYTES, 'utterance_text', reader)
        try:
            text = raw_text.decode('utf-8').strip()
        except UnicodeDecodeError:
            raise ValueError('catalog_utterance_text_utf8_required') from None
        if not 1 <= len(text) <= 2000 or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError('catalog_utterance_text_single_line_bound')
        assets.append(metadata)
        texts.append(text)
        observed.append({'utterance_id': identity, 'start_frame': start, 'end_frame': end,
                         'text_sha256': metadata['sha256'], 'text_reference': metadata['path'],
                         'audio_sha256': audio['sha256'], 'sample_rate': RATE})
    bindings = row['task_bindings']
    if not isinstance(bindings, list) or not 1 <= len(bindings) <= 144:
        raise ValueError('catalog_explicit_task_bindings_required')
    seen_tasks = set()
    ordered_ids = [item['utterance_id'] for item in observed]
    for binding in bindings:
        _closed(binding, {'task_id', 'logical_turn_ids', 'utterance_ids'})
        task_id = _identifier(binding['task_id'], 'task')
        logical = binding['logical_turn_ids']
        if (task_id in seen_tasks or binding['utterance_ids'] != ordered_ids
                or not isinstance(logical, list) or len(logical) != len(ordered_ids)):
            raise ValueError('catalog_task_utterance_mapping_invalid')
        for turn in logical:
            _identifier(turn, 'logical_turn')
        if len(set(logical)) != len(logical):
            raise ValueError('catalog_repeated_logical_turn_identity')
        seen_tasks.add(task_id)
    return {'audio': {**audio, **audio_observation}, 'utterances': observed,
            'fixture_manifest_hash': _digest(_canonical(row))}, assets, texts


def validate_catalog(root: Path, catalog: dict, *, reader=None) -> dict:
    """Validate bytes/declarations only; return metadata, never spoken text."""
    from voicelab.evaluation.exposure import validate_read_access
    validate_read_access(reader)
    root = _root(root)
    _closed(catalog, {'schema_version', 'catalog_id', 'split', 'run_id', 'trial_plan_hash', 'fixtures'})
    if catalog['schema_version'] != 1 or type(catalog['schema_version']) is not int or catalog['split'] != 'dev':
        raise ValueError('catalog_development_schema_one_only')
    for name in ('catalog_id', 'run_id'):
        _identifier(catalog[name], name)
    _sha(catalog['trial_plan_hash'])
    rows = catalog['fixtures']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 256 or len(_canonical(catalog)) > MAX_CATALOG_BYTES:
        raise ValueError('catalog_fixture_count_or_size_bound')
    seen_ids, seen_audio, seen_pcm, seen_tasks, seen_turns = set(), set(), set(), set(), set()
    assets, metadata = {}, []
    for row in rows:
        observation, references, _ = _fixture(root, row, reader)
        identity, audio_hash, pcm_hash = row['fixture_id'], row['audio']['sha256'], row['audio']['pcm_sha256']
        # Reuse one row through explicit task bindings, not aliases with new labels.
        if identity in seen_ids or audio_hash in seen_audio or pcm_hash in seen_pcm:
            raise ValueError('catalog_duplicate_fixture_audio_or_pcm_relabeling')
        seen_ids.add(identity)
        seen_audio.add(audio_hash)
        seen_pcm.add(pcm_hash)
        for binding in row['task_bindings']:
            if binding['task_id'] in seen_tasks or seen_turns.intersection(binding['logical_turn_ids']):
                raise ValueError('catalog_task_or_logical_turn_bound_to_multiple_fixtures')
            seen_tasks.add(binding['task_id'])
            seen_turns.update(binding['logical_turn_ids'])
        for reference in references:
            key = reference['path']
            if key in assets and assets[key]['sha256'] != reference['sha256']:
                raise ValueError('catalog_one_asset_path_multiple_hashes')
            assets[key] = reference
        metadata.append({'fixture_id': identity, 'wording_family': row['wording_family'],
            'speaker': row['speaker'], 'task_template_id': row['task_template_id'],
            'audio_sha256': audio_hash, 'pcm_sha256': pcm_hash,
            'fixture_manifest_hash': observation['fixture_manifest_hash'],
            'utterance_ids': [item['utterance_id'] for item in observation['utterances']],
            'task_ids': [binding['task_id'] for binding in row['task_bindings']]})
    if len(seen_tasks) > 144:
        raise ValueError('catalog_frozen_cohort_task_bound')
    return {'schema_version': 1, 'catalog_id': catalog['catalog_id'], 'split': 'dev',
        'run_id': catalog['run_id'], 'trial_plan_hash': catalog['trial_plan_hash'],
        'catalog_hash': _digest(_canonical(catalog)), 'fixture_count': len(rows),
        'task_count': len(seen_tasks), 'utterance_count': sum(len(row['utterances']) for row in rows),
        'asset_references': sorted(assets.values(), key=lambda item: item['path']),
        'fixture_metadata': metadata, 'approval_authentication': 'not_established',
        'custody_release': False, 'product_acceptance': False}


def bind_catalog_task(spec: dict, task: dict) -> dict:
    """Bind exact frozen task IDs to actual reviewed bytes; plaintext stays private."""
    root = _root(Path(spec['fixture_root']))
    catalog_name = _relative(spec['fixture_catalog'])
    path = root / catalog_name
    # Use an explicit declared digest when supplied; never replace a freeze pin.
    expected = spec.get('fixture_catalog_hash')
    cursor = root
    for part in catalog_name.parts:
        cursor /= part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError('catalog_manifest_link_refused')
    from .access import read_owned
    reader = spec.get('_artifact_reader')
    raw = read_owned(reader, root, catalog_name.as_posix(), maximum=MAX_CATALOG_BYTES)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError('catalog_manifest_size_bound')
    catalog = _json(raw)
    receipt = validate_catalog(root, catalog, reader=reader)
    if expected is not None and _sha(expected) != receipt['catalog_hash']:
        raise ValueError('catalog_frozen_hash_mismatch')
    if catalog['run_id'] != spec['run_id'] or catalog['trial_plan_hash'] != spec.get('trial_plan_hash'):
        raise ValueError('catalog_run_or_trial_plan_pin_mismatch')
    selected = [row for row in catalog['fixtures'] if row['fixture_id'] == task.get('fixture_id')]
    if len(selected) != 1:
        raise ValueError('catalog_fixture_not_found')
    row = selected[0]
    if (row['wording_family'], row['speaker']) != (task.get('family'), task.get('speaker')):
        raise ValueError('catalog_task_family_or_speaker_relabeling')
    if task.get('task_template_id', row['task_template_id']) != row['task_template_id']:
        raise ValueError('catalog_task_template_mismatch')
    bindings = [binding for binding in row['task_bindings'] if binding['task_id'] == task.get('task_id')]
    if len(bindings) != 1 or bindings[0]['logical_turn_ids'] != task.get('logical_turn_ids'):
        raise ValueError('catalog_exact_task_and_turn_mapping_required')
    observation, _, texts = _fixture(root, row, reader)
    audio_path = root / _relative(row['audio']['path'])
    private_fixture = {**row, 'split': 'dev', 'kind': 'reviewed_catalog_utterances',
        'speech': row['speech'], 'audio': observation['audio'], 'turns': texts,
        'utterances': observation['utterances'], 'task_binding': bindings[0]}
    return {**spec, '_fixture': private_fixture, '_input_wav': str(audio_path),
        '_catalog_hash': receipt['catalog_hash'], '_fixture_manifest_hash': observation['fixture_manifest_hash']}


def import_catalog(source_root: Path, catalog_path: Path, destination: Path, *, public_access=None, references=None) -> dict:
    """Explicit future import only. Copy approved named assets into a new directory.

    No generation, downloads, installs or existing-file replacement. A failed import
    remains partial without a committed catalog/receipt; it is never called complete.
    """
    source_root = _root(source_root)
    from .access import public_reader, read_owned
    from voicelab.evaluation.exposure import require_output_root
    reader = public_reader(source_root, references, public_access)
    require_output_root(reader, destination.absolute().parent)
    relative_catalog = _relative(str(catalog_path))
    cursor = source_root
    for part in relative_catalog.parts:
        cursor /= part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError('catalog_import_manifest_link_refused')
    raw = read_owned(reader, source_root, relative_catalog.as_posix(), maximum=MAX_CATALOG_BYTES)
    if len(raw) > MAX_CATALOG_BYTES:
        raise ValueError('catalog_manifest_size_bound')
    catalog = _json(raw)
    receipt = validate_catalog(source_root, catalog, reader=reader)
    target = destination.absolute()
    parent = _root(target.parent)
    target = parent / target.name
    if target.exists() or target.is_symlink() or target.is_junction():
        raise ValueError('catalog_import_new_destination_required')
    if target.is_relative_to(source_root) or source_root.is_relative_to(target):
        raise ValueError('catalog_import_source_destination_overlap')
    if any(item['path'] in {'catalog.json', 'import-receipt.json'} for item in receipt['asset_references']):
        raise ValueError('catalog_import_reserved_output_name')
    target.mkdir()
    for asset in receipt['asset_references']:
        maximum = MAX_AUDIO_BYTES if asset['kind'] == 'audio' else MAX_REFERENCE_BYTES
        content, _ = _read(source_root, {key: asset[key] for key in ('path', 'sha256')}, maximum, asset['kind'], reader)
        path = target / _relative(asset['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        if any(item.is_symlink() or item.is_junction() for item in (path.parent, *path.parent.parents)
               if item.is_relative_to(target)):
            raise ValueError('catalog_import_target_link_refused')
        with path.open('xb') as handle:
            if handle.write(content) != len(content):
                raise ValueError('catalog_import_incomplete_write')
    imported = dict(receipt)
    with (target / 'catalog.json').open('xb') as handle:
        handle.write(_canonical(catalog))
    imported = {**imported, 'catalog_reference': 'catalog.json',
                'import_status': 'structurally_complete_external_gates_still_required'}
    with (target / 'import-receipt.json').open('xb') as handle:
        handle.write(_canonical(imported))
    return imported
