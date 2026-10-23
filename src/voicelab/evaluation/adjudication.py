"""Bounded manual review packets and append-only human choices; never a scorer."""
from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from voicelab.contracts import Hash, Identifier, Model, TerminalOutcome

from .artifacts import ArtifactReader, canonical_bytes, digest
from .assembly import assemble_evaluation
from .gates import ReleaseVerifier
from .trust import ExternalAuthority, externally_authorized


Judgment = Literal['correct', 'incorrect', 'unknown']
Reason = Literal['evidence_consistent', 'wrong_spoken_slot', 'false_commitment',
    'contradictory_confirmation', 'unrequested_booking', 'off_script', 'audio_missing',
    'audio_ambiguous', 'confirmation_missing', 'journal_missing', 'trace_incomplete',
    'binding_mismatch', 'not_reviewed', 'insufficient_evidence', 'superseding_correction']


class Judgments(Model):
    spoken: Judgment
    task: Judgment
    confirmation: Judgment


class ReviewBindings(Model):
    run_id: Identifier
    task_id: Identifier
    trial_id: Identifier
    logical_turn_id: Identifier
    response_id: Identifier | None
    code_hash: Hash
    build_hash: Hash
    fixture_manifest_hash: Hash
    trial_plan_hash: Hash
    input_audio_hash: Hash | None
    rendered_audio_hash: Hash | None
    journal_hash: Hash
    trace_hash: Hash
    ledger_hash: Hash
    confirmation_evidence_hash: Hash | None
    confirmation_audio_hash: Hash | None
    observed_trial_id: Identifier | None
    observed_session_id: Identifier | None
    observed_boot_id: Identifier | None


class ReviewArtifact(Model):
    path: str = Field(min_length=1, max_length=500)
    sha256: Hash
    kind: Literal['audio', 'journal', 'trace', 'confirmation_evidence']
    approved_synthetic: bool | None = None

    @field_validator('path')
    @classmethod
    def owned_relative_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or path.drive or '..' in path.parts or '://' in value:
            raise ValueError('review artifact path must be relative inside an approved owned root')
        return value


class ReviewItem(Model):
    item_id: Identifier
    bindings: ReviewBindings
    artifacts: dict[str, ReviewArtifact | None]
    evidence_gaps: list[str] = Field(max_length=100)


class ReviewPacket(Model):
    schema_version: Literal[1]
    kind: Literal['manual_review_packet']
    packet_id: Identifier
    packet_hash: Hash
    run_id: Identifier
    task_id: Identifier
    created_at: str
    source_hashes: dict[str, Hash]
    artifact_root_policy: str
    items: list[ReviewItem] = Field(min_length=1, max_length=8)
    evidence_status: Literal['unadjudicated']
    split: Literal['dev', 'heldout']
    product_acceptance: Literal[False]
    gates: dict[str, object]


class DecisionSubmission(Model):
    schema_version: Literal[1] = 1
    packet_hash: Hash
    item_id: Identifier
    actor_ref: str = Field(min_length=1, max_length=160)
    appointment_ref: str = Field(min_length=1, max_length=500)
    decided_at: str = Field(min_length=1, max_length=40)
    judgments: Judgments
    reasons: list[Reason] = Field(min_length=1, max_length=8)
    task_terminal: TerminalOutcome | None = None
    supersedes: Hash | None = None

    @field_validator('decided_at')
    @classmethod
    def recorded_human_time(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('human decision timestamp must include an explicit offset')
        return value


class DecisionRecord(Model):
    schema_version: Literal[1]
    kind: Literal['manual_review_decision']
    sequence: int = Field(strict=True, ge=0, le=9_999)
    previous_hash: Hash | None
    submission: DecisionSubmission
    bindings_hash: Hash
    actor_verification: Literal['unverified', 'externally_verified']
    record_hash: Hash


def validate_packet(packet: dict) -> None:
    if len(canonical_bytes(packet)) > 512_000:
        raise ValueError('manual review packet exceeds bounded metadata size')
    ReviewPacket.model_validate(packet)
    if packet.get('kind') != 'manual_review_packet' or packet.get('schema_version') != 1:
        raise ValueError('unsupported manual packet')
    body = {key: value for key, value in packet.items() if key != 'packet_hash'}
    if packet.get('packet_hash') != digest(body):
        raise ValueError('manual packet content hash mismatch')
    items = packet.get('items', [])
    if not 1 <= len(items) <= 8 or len({item['item_id'] for item in items}) != len(items):
        raise ValueError('manual packet requires one task and 1..8 unique turn/response items')
    for item in items:
        bound = item['bindings']
        if bound['run_id'] != packet['run_id'] or bound['task_id'] != packet['task_id']:
            raise ValueError('packet item belongs to a different run/task')
        if item['item_id'] != bound['logical_turn_id']:
            raise ValueError('manual item/logical turn mismatch')
        if set(item['artifacts']) != {'input_audio', 'rendered_audio', 'confirmation_audio', 'confirmation', 'journal', 'trace'}:
            raise ValueError('manual packet artifact roles must be the closed approved set')
        for key in ('input_audio', 'rendered_audio', 'confirmation_audio'):
            reference = item['artifacts'].get(key)
            if reference is not None and (reference.get('kind') != 'audio'
                    or reference.get('approved_synthetic') is not True):
                raise ValueError('manual audio must reference approved synthetic artifacts')
        for role, binding in (('input_audio', 'input_audio_hash'), ('rendered_audio', 'rendered_audio_hash'),
                               ('confirmation_audio', 'confirmation_audio_hash'), ('confirmation', 'confirmation_evidence_hash'),
                               ('journal', 'journal_hash'), ('trace', 'trace_hash')):
            reference = item['artifacts'][role]
            expected_kind = 'audio' if role in {'input_audio', 'rendered_audio', 'confirmation_audio'} else 'confirmation_evidence' if role == 'confirmation' else role
            if reference is not None and reference['kind'] != expected_kind:
                raise ValueError('manual artifact role/kind mismatch')
            if reference is not None and reference['sha256'] != bound[binding]:
                raise ValueError('review artifact bytes differ from exact decision binding')


def prepare_review_packet(bundle: dict, artifact_root: str | Path, task_id: str, *,
                          expected_freeze_hash: str | None = None,
                          verify_release: ReleaseVerifier | None = None) -> dict:
    # Reuse the canonical join/gates, leaving the oracle absent. This never runs
    # the report writer, experiment, audio playback or a provider/model.
    assembled = assemble_evaluation(bundle, artifact_root, expected_freeze_hash=expected_freeze_hash,
                                    verify_release=verify_release)
    selected = [item for item in assembled['review_bindings'] if item['bindings']['task_id'] == task_id]
    for item in selected:
        if len(item['evidence_gaps']) > 100:
            item['evidence_gaps'] = item['evidence_gaps'][:99] + ['additional integrity findings remain in the exact hashed source trace']
    if not 1 <= len(selected) <= 8:
        raise ValueError('unknown task or manual-review task exceeds eight logical turns')
    manifest = assembled['manifest']
    needed = {value for item in selected for value in item['bindings'].values() if isinstance(value, str)}
    common = {'manifest', 'plan', 'fixtures', 'freeze', 'release', 'trace', 'ledger', 'journal', 'driver', 'observations'}
    sources = {name: value for name, value in assembled['source_hashes'].items() if name in common or value in needed}
    body = {'schema_version': 1, 'kind': 'manual_review_packet',
            'packet_id': 'review-' + digest(task_id)[:32], 'run_id': manifest['run_id'], 'task_id': task_id,
            'created_at': manifest['created_at'], 'source_hashes': sources,
            'artifact_root_policy': 'resolve relative references inside separately authorized owned root; no inline audio',
            'items': selected, 'evidence_status': 'unadjudicated', 'split': manifest['split'],
            'product_acceptance': False, 'gates': assembled['gates']}
    packet = {**body, 'packet_hash': digest(body)}
    validate_packet(packet)
    return packet


def validate_decision_chain(packet: dict, records: list[dict]) -> None:
    validate_packet(packet)
    if len(records) > 10_000:
        raise ValueError('manual journal exceeds bounded record count')
    previous, latest = None, {}
    by_item = {item['item_id']: item for item in packet['items']}
    for index, record in enumerate(records):
        DecisionRecord.model_validate(record)
        submission = DecisionSubmission.model_validate(record['submission']).model_dump(mode='json')
        expected = digest({key: value for key, value in record.items() if key != 'record_hash'})
        if (record.get('schema_version') != 1 or record.get('kind') != 'manual_review_decision'
                or record['sequence'] != index or record['previous_hash'] != previous
                or record['record_hash'] != expected):
            raise ValueError('decision journal sequence/hash lineage invalid')
        item = by_item.get(submission['item_id'])
        if (submission['packet_hash'] != packet['packet_hash'] or item is None
                or record['bindings_hash'] != digest(item['bindings'])
                or submission['supersedes'] != latest.get(submission['item_id'])):
            raise ValueError('decision packet/item/supersession binding invalid')
        latest[submission['item_id']] = expected
        previous = expected


def read_decision_journal(root: str | Path, packet: dict, *, expected_head: str | None = None) -> list[dict]:
    directory = Path(root) / 'records'
    if not directory.exists():
        validate_packet(packet)
        if expected_head is not None:
            raise ValueError('pinned decision head missing from absent journal')
        return []
    if directory.is_symlink():
        raise ValueError('decision records directory must not be a link')
    files = sorted(directory.iterdir())
    if len(files) > 10_000:
        raise ValueError('manual journal exceeds bounded record count')
    records = []
    for index, path in enumerate(files):
        if (path.is_symlink() or not re.fullmatch(r'\d{8}-[a-f0-9]{64}\.json', path.name)
                or not path.is_file() or path.stat().st_size > 16_384):
            raise ValueError('unexpected/unbounded decision record; no silent omission')
        record = json.loads(path.read_text(encoding='utf-8'))
        if path.name != f"{index:08d}-{record['record_hash']}.json":
            raise ValueError('decision filename/sequence/hash mismatch')
        records.append(record)
    validate_decision_chain(packet, records)
    if expected_head is not None and (not records or records[-1]['record_hash'] != expected_head):
        raise ValueError('decision journal differs from externally retained head; truncation/stale view refused')
    return records


def append_decision(root: str | Path, packet: dict, submission: dict, *,
                    authority: ExternalAuthority | None = None,
                    excluded_actor_refs: frozenset[str] = frozenset()) -> dict:
    validate_packet(packet)
    submitted = DecisionSubmission.model_validate(submission).model_dump(mode='json')
    item = next((row for row in packet['items'] if row['item_id'] == submitted['item_id']), None)
    if item is None or submitted['packet_hash'] != packet['packet_hash']:
        raise ValueError('decision is not bound to this immutable packet')
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / '.append-lock'
    token = uuid.uuid4().hex
    # Exclusive create serializes writers; stale locks fail visibly, never auto-delete.
    with lock.open('x', encoding='utf-8') as handle:
        handle.write(token)
    try:
        records = read_decision_journal(directory, packet)
        if len(records) >= 10_000:
            raise ValueError('manual decision journal capacity exhausted')
        latest = next((r for r in reversed(records) if r['submission']['item_id'] == submitted['item_id']), None)
        if submitted['supersedes'] != (latest['record_hash'] if latest else None):
            raise ValueError('correction must supersede the exact current item decision')
        authorized = externally_authorized(authority, 'manual_decision', submitted,
            {'packet_hash': packet['packet_hash'], 'bindings': item['bindings']}, excluded_actor_refs)
        body = {'schema_version': 1, 'kind': 'manual_review_decision', 'sequence': len(records),
                'previous_hash': records[-1]['record_hash'] if records else None,
                'submission': submitted, 'bindings_hash': digest(item['bindings']),
                'actor_verification': 'externally_verified' if authorized else 'unverified'}
        record = {**body, 'record_hash': digest(body)}
        raw = canonical_bytes(record)
        if len(raw) > 16_384:
            raise ValueError('manual decision record exceeds bound')
        storage = directory / 'records'
        storage.mkdir(exist_ok=True)
        with (storage / f"{len(records):08d}-{record['record_hash']}.json").open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return record
    finally:
        # Remove only the transient lock this invocation created and still owns.
        if lock.is_file() and not lock.is_symlink() and lock.read_text(encoding='utf-8') == token:
            lock.unlink()


def export_oracle(packet: dict, records: list[dict], *, artifact_root: str | Path | None = None,
                  authority: ExternalAuthority | None = None,
                  expected_head: str | None = None,
                  excluded_actor_refs: frozenset[str] = frozenset()) -> tuple[list[dict], Callable[[dict], bool]]:
    """Return canonical oracle rows and an in-memory verifier for the existing assembler.

    No serialized flag or claimed name can recreate this trust boundary. Every
    final decision is reauthenticated; older decisions/supersessions stay intact.
    """
    validate_decision_chain(packet, records)
    if expected_head is not None and (not records or records[-1]['record_hash'] != expected_head):
        raise ValueError('oracle export journal does not match retained immutable head')
    latest = {record['submission']['item_id']: record for record in records}
    output, authenticated = [], set()
    for item in packet['items']:
        bound, record = item['bindings'], latest.get(item['item_id'])
        submitted = record['submission'] if record else None
        authorized = bool(submitted) and externally_authorized(authority, 'manual_decision', submitted,
            {'packet_hash': packet['packet_hash'], 'bindings': bound,
             'decision_hash': record['record_hash'] if record else None,
             'decision_chain_head': expected_head}, excluded_actor_refs)
        gaps = list(item['evidence_gaps'])
        if expected_head is None:
            gaps.append('decision journal head not externally retained/pinned; valid-prefix rollback cannot be excluded')
        for key in ('response_id', 'input_audio_hash', 'rendered_audio_hash', 'journal_hash', 'trace_hash',
                    'ledger_hash', 'confirmation_evidence_hash', 'confirmation_audio_hash'):
            if not bound.get(key):
                gaps.append('missing required binding: ' + key)
        for role, reference in item['artifacts'].items():
            if reference is None:
                gaps.append('missing required review artifact: ' + role)
        if artifact_root is None:
            gaps.append('packet artifact bytes not revalidated at export')
        else:
            references = {key: value for key, value in item['artifacts'].items() if value is not None}
            reader = ArtifactReader(artifact_root, references)
            for name, reference in references.items():
                if reference['kind'] == 'audio':
                    reader.audio(name)
                else:
                    reader.read(name, reference['kind'])
        if not authorized:
            gaps.append('independent actor/appointment/exact human issuance not externally verified')
        choices = submitted['judgments'] if submitted else {'spoken': 'unknown', 'task': 'unknown', 'confirmation': 'unknown'}
        if submitted:
            unresolved = {'audio_missing', 'audio_ambiguous', 'confirmation_missing', 'journal_missing',
                          'trace_incomplete', 'binding_mismatch', 'not_reviewed', 'insufficient_evidence'}
            if unresolved.intersection(submitted['reasons']):
                gaps.append('human reason explicitly reports missing/ambiguous/unreviewed evidence')
            if (choices['spoken'] == 'correct' and {'wrong_spoken_slot', 'off_script'}.intersection(submitted['reasons'])
                    or choices['task'] == 'correct' and {'false_commitment', 'contradictory_confirmation', 'unrequested_booking'}.intersection(submitted['reasons'])):
                gaps.append('human choice/reason contradiction; no positive correctness inferred')
        complete = not gaps and authorized
        value = lambda choice: True if choice == 'correct' else False if choice == 'incorrect' else None
        confirmation_reviewed = complete and choices['confirmation'] != 'unknown'
        task_ok = value(choices['task']) if complete else None
        spoken_ok = value(choices['spoken']) if complete else None
        if choices['confirmation'] != 'correct' and task_ok is True:
            task_ok = None
            gaps.append('positive task decision lacks human-confirmed genuine confirmation')
        terminal = submitted.get('task_terminal') if submitted and complete else None
        if terminal == 'success' and (task_ok is not True or spoken_ok is not True):
            terminal = None
        for dimension, choice in choices.items():
            if choice == 'unknown':
                gaps.append('human decision unknown: ' + dimension)
        row = {**bound, 'method': 'independent_manual_listening_and_authority_journal',
               'task_correct': task_ok, 'spoken_correct': spoken_ok, 'task_terminal': terminal,
               'confirmation_evidence_reviewed': bool(confirmation_reviewed),
               'actor_ref': submitted['actor_ref'] if submitted else None,
               'appointment_ref': submitted['appointment_ref'] if submitted else None,
               'packet_hash': packet['packet_hash'], 'decision_hash': record['record_hash'] if record else None,
               'decision_chain_head': records[-1]['record_hash'] if records else None,
               'unknown_reasons': sorted(set(gaps)), 'product_acceptance': False}
        output.append(row)
        if authorized and expected_head is not None:
            authenticated.add(digest(row))
    return output, lambda row: digest(row) in authenticated
