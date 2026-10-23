"""Sealed saved-report replay; equality is not runtime or product acceptance.

This module starts no application/driver/provider. Its workflow must only be
invoked during separately authorized artifact validation, never construction.
"""
from __future__ import annotations

import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from voicelab.contracts import Hash, Identifier, Model, RunManifest
from voicelab.reporting.reports import REPORT_GENERATOR_VERSION, REPORT_OUTPUT_FILES, write_report

from .artifacts import ArtifactReader, canonical_bytes, digest
from .planning import validate_trial_plan
from .trust import ExternalAuthority, verify_gate_record

GENERATOR_SOURCES = ('contracts.py', 'reporting/reports.py', 'reporting/timing.py',
                     'evaluation/artifacts.py', 'evaluation/reproduction.py', 'evaluation/trust.py')
INPUT_KINDS = {'manifest': 'manifest', 'turns': 'turn_results', 'ledger': 'ledger', 'trace': 'trace',
               'original_manifest': 'manifest', 'raw_ledger': 'ledger_snapshot',
               'oracle': 'adjudication', 'fixtures': 'fixtures', 'plan': 'plan', 'evidence': 'evidence',
               'source_hashes': 'source_hashes', 'driver_correlations': 'driver_correlations',
               'authorization': 'cohort_authorization'}


def _relative(value: str) -> str:
    path = Path(value)
    if (not value or path.is_absolute() or path.drive or '..' in path.parts or '://' in value
            or any(ord(char) < 32 for char in value)):
        raise ValueError('safe relative artifact path required')
    return value


class SealedArtifact(Model):
    path: str = Field(min_length=1, max_length=500)
    sha256: Hash
    kind: str = Field(min_length=1, max_length=64)

    @field_validator('path')
    @classmethod
    def relative(cls, value: str) -> str:
        return _relative(value)


class ReportInputs(Model):
    manifest: SealedArtifact
    original_manifest: SealedArtifact
    turns: SealedArtifact
    ledger: SealedArtifact
    raw_ledger: SealedArtifact
    trace: SealedArtifact
    oracle: SealedArtifact | None
    fixtures: SealedArtifact
    plan: SealedArtifact
    evidence: SealedArtifact | None
    source_hashes: SealedArtifact
    driver_correlations: SealedArtifact | None
    authorization: SealedArtifact | None


class RunPins(Model):
    run_id: Identifier
    mode: Literal['mock', 'replay', 'live']
    split: Literal['dev', 'heldout']
    code_hash: Hash
    build_hash: Hash
    config_hash: Hash
    schema_hash: Hash
    fixture_manifest_hash: Hash
    trial_plan_hash: Hash
    pricing_manifest_hash: Hash | None


class ReproductionExpectation(Model):
    schema_version: Literal[1]
    kind: Literal['sealed_report_reproduction']
    expectation_id: Identifier
    created_at: str
    pins: RunPins
    versions: dict[str, str]
    generator_source_sha256: dict[str, Hash]
    inputs: ReportInputs
    expected_outputs: dict[str, SealedArtifact]
    authorization_record_hash: Hash | None


class ReproductionDifference(ValueError):
    def __init__(self, kind: str, name: str, expected: object = None, actual: object = None):
        self.difference = {'kind': kind, 'name': name, 'expected': expected, 'actual': actual}
        super().__init__(kind + ': ' + name)


def _json(raw: bytes, name: str) -> object:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ReproductionDifference('duplicate_json_key', name)
            value[key] = item
        return value
    def nonfinite(value):
        raise ReproductionDifference('nonfinite_json_value', name)
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReproductionDifference('invalid_json', name) from error


def _read(reader: ArtifactReader, name: str, kind: str) -> bytes:
    try:
        return reader.read(name, kind)
    except FileNotFoundError as error:
        raise ReproductionDifference('artifact_missing', name) from error
    except OSError as error:
        raise ReproductionDifference('artifact_unreadable', name) from error
    except (ValueError, KeyError) as error:
        raise ReproductionDifference('artifact_hash_path_or_kind_mismatch', name) from error


def _versions() -> dict:
    try:
        pydantic = version('pydantic')
    except PackageNotFoundError as error:
        raise ReproductionDifference('version_unavailable', 'pydantic') from error
    return {'python': platform.python_version(), 'pydantic': pydantic,
            'report_generator': str(REPORT_GENERATOR_VERSION), 'schema_version': '1'}


def _generator_hashes() -> dict:
    package = Path(__file__).resolve().parents[1]
    try:
        return {name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in GENERATOR_SOURCES}
    except OSError as error:
        raise ReproductionDifference('generator_source_unavailable', 'canonical_reporter') from error


def reproduction_authorization_scope(expected: dict) -> dict:
    """Pin the contract before adding the authorization artifact; no digest cycle."""
    contract = {key: expected[key] for key in ('expectation_id', 'created_at', 'pins', 'versions',
                                               'generator_source_sha256', 'expected_outputs')}
    contract['inputs'] = {name: value for name, value in expected['inputs'].items() if name != 'authorization'}
    scope = {key: expected['pins'][key] for key in ('run_id', 'code_hash', 'build_hash', 'config_hash',
                                                   'fixture_manifest_hash', 'trial_plan_hash')}
    scope.update(operation='sealed_report_reproduction', expectation_id=expected['expectation_id'],
                 reproduction_contract_hash=digest(contract))
    return scope


def _source_lineage(raw: dict, manifest: dict) -> None:
    sources = _json(raw['source_hashes'], 'source_hashes')
    if not isinstance(sources, dict):
        raise ReproductionDifference('source_hash_mapping_required', 'source_hashes')
    mapping = {'manifest': 'original_manifest', 'trace': 'trace', 'ledger': 'raw_ledger',
               'fixtures': 'fixtures', 'plan': 'plan'}
    if 'oracle' in raw:
        mapping['adjudication'] = 'oracle'
    for name, artifact in mapping.items():
        actual = hashlib.sha256(raw[artifact]).hexdigest()
        if sources.get(name) != actual:
            raise ReproductionDifference('original_source_lineage_mismatch', name, sources.get(name), actual)
    original = RunManifest.model_validate(_json(raw['original_manifest'], 'original_manifest')).model_dump(mode='json')
    for field in original:
        if field != 'turn_assignments' and original[field] != manifest[field]:
            raise ReproductionDifference('original_manifest_pin_mismatch', field)
    saved = _json(raw['raw_ledger'], 'raw_ledger')
    assembled = _json(raw['ledger'], 'ledger')
    if not isinstance(saved, dict) or not isinstance(assembled, dict):
        raise ReproductionDifference('ledger_object_required', 'raw_or_assembled_ledger')
    source_attempts = {row['attempt_id']: row for row in saved['attempts']}
    target_attempts = {row['attempt_id']: row for row in assembled['attempts']}
    if (len(source_attempts) != len(saved['attempts']) or len(target_attempts) != len(assembled['attempts'])
            or set(source_attempts) != set(target_attempts)):
        raise ReproductionDifference('attempt_set_or_duplicate_lineage_mismatch', 'raw_ledger')
    correlations = _json(raw['driver_correlations'], 'driver_correlations') if 'driver_correlations' in raw else {}
    parents = {row['attempt_id']: row for row in correlations.get('attempt_correlations', [])}
    if len(parents) != len(correlations.get('attempt_correlations', [])):
        raise ReproductionDifference('duplicate_attempt_parent_correlations', 'driver_correlations')
    for attempt_id, source in sorted(source_attempts.items()):
        target = target_attempts[attempt_id]
        for field in ('arm', 'component', 'reservation', 'price_version', 'basis', 'state', 'settled'):
            if source.get(field) != target.get(field):
                raise ReproductionDifference('immutable_attempt_lineage_mismatch', attempt_id + '/' + field)
        if source['logical_turn_id'] != target['logical_turn_id']:
            parent = parents.get(attempt_id, {})
            if (parent.get('observed_logical_turn_id') != source['logical_turn_id']
                    or parent.get('logical_turn_id') != target['logical_turn_id']):
                raise ReproductionDifference('attempt_parent_mapping_unverified', attempt_id)
    if saved.get('events') != assembled.get('events'):
        raise ReproductionDifference('immutable_attempt_event_lineage_mismatch', 'ledger_events')
    for field in ('known_cost', 'unknown_exposure', 'total_exposure'):
        if Decimal(str(saved[field])) != Decimal(str(assembled[field])):
            raise ReproductionDifference('run_snapshot_total_mismatch', field)
    if type(saved.get('unknown_count')) is not int or type(assembled.get('unknown_count')) is not int:
        raise ReproductionDifference('invalid_run_snapshot_unknown_count', 'unknown_count')
    if saved.get('unknown_count') != assembled.get('unknown_count'):
        raise ReproductionDifference('run_snapshot_unknown_count_mismatch', 'unknown_count')


def _output_hashes(root: Path) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise ReproductionDifference('unsafe_output_directory', 'regenerated')
    children = list(root.iterdir())
    if len(children) > 64:
        raise ReproductionDifference('unexpected_output_count', 'regenerated')
    output = {}
    for path in sorted(children):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128_000_000:
            raise ReproductionDifference('unsafe_unbounded_or_nonfile_output', path.name)
        output[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return output


def _new_stage(stage_root: str | Path, stage_name: str, bundle_root: Path) -> Path:
    # One explicitly named new child; no reuse, reset, recursive delete or cleanup.
    if not stage_name or not stage_name.isascii() or not all(c.isalnum() or c in '_-' for c in stage_name) or len(stage_name) > 96:
        raise ReproductionDifference('unsafe_stage_name', 'stage_name')
    parent = Path(stage_root).resolve(strict=True)
    if not parent.is_dir():
        raise ReproductionDifference('stage_parent_not_directory', 'stage_root')
    stage = parent / stage_name
    if stage.exists() or stage.is_symlink():
        raise ReproductionDifference('stage_already_exists_no_ownership_assumed', 'stage')
    if stage.is_relative_to(bundle_root) or bundle_root.is_relative_to(stage):
        raise ReproductionDifference('stage_overlaps_approved_bundle', 'stage')
    project = Path(__file__).resolve().parents[3]
    protected = (project / 'src', project / 'docs', project / 'planning', project / '.git')
    if any(stage.is_relative_to(path.resolve()) or path.resolve().is_relative_to(stage) for path in protected):
        raise ReproductionDifference('stage_overlaps_source_or_docs', 'stage')
    stage.mkdir(exist_ok=False)
    return stage


def reproduce_reports(bundle_root: str | Path, expectation_path: str, *, expectation_sha256: str,
                       stage_root: str | Path, stage_name: str,
                       access_reader: ArtifactReader | None = None,
                       authority: ExternalAuthority | None = None,
                       excluded_actor_refs: frozenset[str] = frozenset()) -> dict:
    """Validate a sealed bundle, regenerate once and compare exact byte/file sets.

    No authority/approval is manufactured. CLI defaults allow only dev/mock or
    replay metadata; private/live inputs require the real external read authority.
    No actual workflow is executed during Dirty Mode source construction.
    """
    result = {'schema_version': 1, 'kind': 'report_reproduction_comparison', 'created_at': None,
              'expectation_sha256': expectation_sha256, 'status': 'refused', 'reproduced': False,
              'differences': [], 'actual_outputs': {}, 'product_acceptance': False,
              'independence_acceptance': False, 'runtime_acceptance': False}
    stage = None
    try:
        root = Path(bundle_root).resolve(strict=True)
        descriptor = SealedArtifact(path=expectation_path, sha256=expectation_sha256, kind='reproduction_expectation')
        reader = ArtifactReader(root, {'expectation': descriptor.model_dump(mode='json')})
        sealed_raw = _read(reader, 'expectation', 'reproduction_expectation')
        try:
            expected = ReproductionExpectation.model_validate(_json(sealed_raw, 'expectation')).model_dump(mode='json')
        except ValueError as error:
            if isinstance(error, ReproductionDifference):
                raise
            raise ReproductionDifference('invalid_expectation_schema', 'expectation') from error
        result['created_at'] = expected['created_at']
        result['expectation_id'] = expected['expectation_id']
        if set(expected['expected_outputs']) != set(REPORT_OUTPUT_FILES):
            raise ReproductionDifference('expected_file_set_contract_mismatch', 'expected_outputs', sorted(REPORT_OUTPUT_FILES), sorted(expected['expected_outputs']))
        current_versions = _versions()
        if expected['versions'] != current_versions:
            raise ReproductionDifference('version_mismatch', 'versions', expected['versions'], current_versions)
        current_sources = _generator_hashes()
        if expected['generator_source_sha256'] != current_sources:
            raise ReproductionDifference('generator_source_mismatch', 'generator_source_sha256', expected['generator_source_sha256'], current_sources)
        references = {name: value for name, value in expected['inputs'].items() if value is not None}
        if any(reference['kind'] != INPUT_KINDS[name] for name, reference in references.items()):
            raise ReproductionDifference('input_kind_mismatch', 'inputs')
        paths = [reference['path'] for reference in references.values()] + [r['path'] for r in expected['expected_outputs'].values()] + [expectation_path]
        if len(paths) != len(set(paths)):
            raise ReproductionDifference('aliased_immutable_bundle_paths', 'inputs_or_expected_outputs')
        from .exposure import aliased_reader, require_output_root, validate_read_access
        if access_reader is None:
            raise ReproductionDifference('actual_read_authority_missing_no_dataset_access', 'inputs')
        validate_read_access(access_reader, private_required=expected['pins']['split'] == 'heldout')
        if access_reader.root != root:
            raise ReproductionDifference('authorized_read_root_mismatch', 'inputs')
        reader = aliased_reader(access_reader, references)
        manifest_raw = _read(reader, 'manifest', 'manifest')
        try:
            manifest = RunManifest.model_validate(_json(manifest_raw, 'manifest')).model_dump(mode='json')
        except ValueError as error:
            raise ReproductionDifference('invalid_run_manifest', 'manifest') from error
        if any(manifest[key] != value for key, value in expected['pins'].items()):
            raise ReproductionDifference('run_code_schema_config_pins_mismatch', 'manifest')
        if manifest['created_at'] != expected['created_at']:
            raise ReproductionDifference('source_timestamp_mismatch', 'created_at', expected['created_at'], manifest['created_at'])
        result['run_id'] = manifest['run_id']
        if manifest['split'] != 'dev' or manifest['mode'] == 'live':
            if references.get('authorization') is None or expected['authorization_record_hash'] is None:
                raise ReproductionDifference('external_read_authorization_absent', 'authorization')
            proof = _json(_read(reader, 'authorization', 'cohort_authorization'), 'authorization')
            scope = reproduction_authorization_scope(expected)
            if not verify_gate_record('cohort_authorization', proof, scope,
                    expected_record_hash=expected['authorization_record_hash'], authority=authority,
                    excluded_actor_refs=excluded_actor_refs):
                raise ReproductionDifference('external_read_authorization_unverified', 'authorization')
        # All input/expected bytes are checked before any new stage is created.
        raw = {name: _read(reader, name, reference['kind']) for name, reference in sorted(references.items())}
        _source_lineage(raw, manifest)
        if hashlib.sha256(raw['fixtures']).hexdigest() != manifest['fixture_manifest_hash']:
            raise ReproductionDifference('fixture_manifest_pin_mismatch', 'fixtures')
        plan = _json(raw['plan'], 'plan')
        try:
            validate_trial_plan(plan)
        except (ValueError, KeyError, TypeError) as error:
            raise ReproductionDifference('invalid_frozen_plan', 'plan') from error
        if plan['plan_hash'] != manifest['trial_plan_hash'] or plan['run_id'] != manifest['run_id']:
            raise ReproductionDifference('trial_plan_pin_mismatch', 'plan')
        rows = [_json(line.encode('utf-8'), 'turns') for line in raw['turns'].decode('utf-8').splitlines() if line.strip()]
        if 'oracle' not in raw and any(row.get('task_correct') is True or row.get('spoken_correct') is True for row in rows):
            raise ReproductionDifference('positive_correctness_without_pinned_oracle', 'oracle')
        expected_reader = aliased_reader(access_reader, expected['expected_outputs'])
        for name in sorted(REPORT_OUTPUT_FILES):
            if expected['expected_outputs'][name]['kind'] != 'report_output':
                raise ReproductionDifference('expected_output_kind_mismatch', name)
            _read(expected_reader, name, 'report_output')
        require_output_root(access_reader, stage_root)
        stage = _new_stage(stage_root, stage_name, root)
        with (stage / '.reproduction-owner.json').open('xb') as stream:
            stream.write(canonical_bytes({'schema_version': 1, 'run_id': manifest['run_id'],
                'expectation_sha256': expectation_sha256, 'role': 'new_explicitly_owned_reproduction_stage',
                'source_overwrite_permitted': False}))
        inputs = stage / 'inputs'
        inputs.mkdir(exist_ok=False)
        copied = {}
        for name, content in sorted(raw.items()):
            require_output_root(access_reader, stage_root)
            path = inputs / (name + ('.jsonl' if name in {'turns', 'trace', 'oracle', 'evidence'} else '.json'))
            with path.open('xb') as stream:
                stream.write(content)
            copied[name] = path
        optional = {argument: copied[name] for argument, name in (
            ('evidence_path', 'evidence'), ('source_hashes_path', 'source_hashes'),
            ('driver_correlations_path', 'driver_correlations')) if name in copied}
        try:
            require_output_root(access_reader, stage_root)
            (stage / 'regenerated').mkdir(exist_ok=False)
            write_report(copied['manifest'], copied['turns'], copied['ledger'], stage / 'regenerated', **optional)
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise ReproductionDifference('canonical_reporter_refused_inputs', 'regeneration') from error
        actual = _output_hashes(stage / 'regenerated')
        result['actual_outputs'] = actual
        expected_hashes = {name: expected['expected_outputs'][name]['sha256'] for name in REPORT_OUTPUT_FILES}
        for name in sorted(set(actual) | set(expected_hashes)):
            if name not in expected_hashes:
                result['differences'].append({'kind': 'unexpected_output', 'name': name, 'expected': None, 'actual': actual[name]})
            elif name not in actual:
                result['differences'].append({'kind': 'missing_output', 'name': name, 'expected': expected_hashes[name], 'actual': None})
            elif actual[name] != expected_hashes[name]:
                result['differences'].append({'kind': 'output_byte_hash_mismatch', 'name': name, 'expected': expected_hashes[name], 'actual': actual[name]})
        # Re-read sealed originals to detect source mutation during regeneration.
        _read(ArtifactReader(root, {'expectation': descriptor.model_dump(mode='json')}), 'expectation', 'reproduction_expectation')
        for name, reference in sorted(references.items()):
            _read(reader, name, reference['kind'])
        for name in sorted(REPORT_OUTPUT_FILES):
            _read(expected_reader, name, 'report_output')
        if _generator_hashes() != current_sources:
            raise ReproductionDifference('generator_source_changed_during_regeneration', 'generator_source_sha256')
        if _versions() != current_versions:
            raise ReproductionDifference('version_changed_during_regeneration', 'versions')
        result.update(status='equal' if not result['differences'] else 'different', reproduced=not result['differences'],
                      input_sha256={name: reference['sha256'] for name, reference in sorted(references.items())},
                      versions=expected['versions'], generator_source_sha256=current_sources,
                      claim='byte/file-set reproduction only; no product/runtime/independence acceptance')
    except ReproductionDifference as error:
        result['differences'].append(error.difference)
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as error:
        result['differences'].append({'kind': 'invalid_or_unavailable_bundle', 'name': type(error).__name__,
                                      'expected': None, 'actual': None})
    result['differences'].sort(key=lambda item: (item['kind'], item['name']))
    if stage is not None:
        # Fixed name in this invocation's brand-new owned stage only; never inputs/docs.
        try:
            require_output_root(access_reader, stage_root)
            with (stage / 'comparison.json').open('xb') as stream:
                stream.write(canonical_bytes(result))
        except (OSError, ValueError):
            result.update(status='refused', reproduced=False)
            result['differences'].append({'kind': 'comparison_write_refused', 'name': 'comparison.json',
                                           'expected': None, 'actual': None})
    result['differences'].sort(key=lambda item: (item['kind'], item['name']))
    return result
