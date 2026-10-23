"""Local intent/result accounting, never an independent redemption authority."""
from __future__ import annotations

import json
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import ArtifactReader, canonical_bytes, digest
from .trust import ExternalAuthority, externally_authorized, verify_gate_record


class ExposureRefused(ValueError):
    pass


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ExposureRefused('explicit_UTC_offset_required')
    return parsed.astimezone(timezone.utc)


def root_binding(root: str | Path) -> dict:
    supplied = Path(root).absolute()
    for component in (supplied, *supplied.parents):
        info = component.lstat()
        if component.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ExposureRefused('owned_root_symlink_or_reparse_refused')
    resolved = supplied.resolve(strict=True)
    info = resolved.stat()
    if not resolved.is_dir() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ExposureRefused('owned_root_not_regular_directory')
    return {'path_hash': digest(str(resolved)), 'device': info.st_dev, 'inode': info.st_ino}


class ExposureJournal:
    """SQLite FULL-sync append-only metadata using the established journal pattern.

    LifecycleJournal/AttemptLedger have domain-specific mutable tables/private
    connections, so they cannot represent this accounting without owner changes.
    No row in this journal is a permit, refund, unused-set proof or authority.
    """
    def __init__(self, root: str | Path, name: str):
        if not name or Path(name).name != name or ':' in name or name in {'.', '..'}:
            raise ExposureRefused('flat_owned_journal_name_required')
        self.root = Path(root).resolve(strict=True)
        self.binding = root_binding(root)
        self.path = self.root / name

    def _check(self):
        if root_binding(self.root) != self.binding or self.path.is_symlink():
            raise ExposureRefused('exposure_journal_root_or_path_changed')
        for suffix in ('', '-journal', '-wal', '-shm'):
            candidate = self.path.with_name(self.path.name + suffix)
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ExposureRefused('exposure_journal_or_sidecar_link_not_owned_regular_file')

    @contextmanager
    def _connect(self, *, create: bool):
        self._check()
        if not create and not self.path.is_file():
            raise ExposureRefused('exposure_history_unavailable_not_unused_proof')
        target = str(self.path) if create else self.path.as_uri() + '?mode=ro'
        db = sqlite3.connect(target, uri=not create, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if create:
                db.execute('PRAGMA synchronous=FULL')
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS exposure_events(
                      sequence INTEGER PRIMARY KEY, use_key TEXT NOT NULL, kind TEXT NOT NULL,
                      payload TEXT NOT NULL, previous_hash TEXT, record_hash TEXT NOT NULL UNIQUE);
                    CREATE UNIQUE INDEX IF NOT EXISTS one_intent_per_exact_use
                      ON exposure_events(use_key) WHERE kind='use_intent';
                    CREATE TRIGGER IF NOT EXISTS exposure_no_update BEFORE UPDATE ON exposure_events
                      BEGIN SELECT RAISE(ABORT,'append_only_exposure_history'); END;
                    CREATE TRIGGER IF NOT EXISTS exposure_no_delete BEFORE DELETE ON exposure_events
                      BEGIN SELECT RAISE(ABORT,'append_only_exposure_history'); END;
                """)
            yield db
            self._check()
        finally:
            db.close()

    def append(self, use_key: str, kind: str, payload: dict) -> dict:
        if kind not in {'use_intent', 'redemption_result', 'artifact_intent', 'artifact_result', 'use_result'}:
            raise ExposureRefused('unknown_exposure_accounting_event')
        if len(canonical_bytes(payload)) > 1_048_576:
            raise ExposureRefused('exposure_event_metadata_bound_exceeded')
        with self._connect(create=True) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                exists = db.execute('SELECT 1 FROM exposure_events WHERE use_key=? LIMIT 1', (use_key,)).fetchone()
                if kind == 'use_intent' and exists:
                    raise ExposureRefused('prior_exact_use_requires_external_reconciliation_no_retry')
                if kind != 'use_intent' and not exists:
                    raise ExposureRefused('exposure_intent_missing')
                previous = db.execute('SELECT sequence,record_hash FROM exposure_events ORDER BY sequence DESC LIMIT 1').fetchone()
                if previous and previous['sequence'] >= 250000:
                    raise ExposureRefused('exposure_history_capacity_requires_external_reconciliation')
                body = {'sequence': previous['sequence'] + 1 if previous else 1, 'use_key': use_key,
                        'kind': kind, 'payload': payload, 'previous_hash': previous['record_hash'] if previous else None}
                record = {**body, 'record_hash': digest(body)}
                db.execute('INSERT INTO exposure_events VALUES(?,?,?,?,?,?)',
                           (record['sequence'], use_key, kind, canonical_bytes(payload).decode('utf-8'),
                            record['previous_hash'], record['record_hash']))
                db.execute('COMMIT')
                return record
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def recovery_view(self, use_key: str) -> dict:
        """Read-only refusal/reconciliation view; never reopen or retry redemption."""
        with self._connect(create=False) as db:
            rows = db.execute('SELECT * FROM exposure_events ORDER BY sequence LIMIT 250001').fetchall()
        if len(rows) > 250000:
            raise ExposureRefused('exposure_history_inspection_bound_exceeded')
        previous, sequence, selected = None, 0, []
        for raw in rows:
            body = {'sequence': raw['sequence'], 'use_key': raw['use_key'], 'kind': raw['kind'],
                    'payload': json.loads(raw['payload']), 'previous_hash': raw['previous_hash']}
            if raw['sequence'] != sequence + 1 or raw['previous_hash'] != previous or digest(body) != raw['record_hash']:
                raise ExposureRefused('exposure_history_integrity_unknown')
            sequence, previous = raw['sequence'], raw['record_hash']
            if raw['use_key'] == use_key:
                selected.append({**body, 'record_hash': raw['record_hash']})
        redemption = [row for row in selected if row['kind'] == 'redemption_result']
        state = 'unknown_after_intent_or_crash' if selected and not redemption else 'recorded_attempt_requires_external_reconciliation' if selected else 'no_local_record_not_unused_proof'
        return {'use_key': use_key, 'state': state, 'events': selected, 'head_hash': previous,
                'automatic_retry': False, 'refund': False, 'access_authorized': False,
                'fresh_set_asserted': False, 'external_authority_required': True}


class PrivateExposureUse:
    """One in-memory exact-use lifetime, backed only by actual external redemption."""
    def __init__(self, root: str | Path, references: dict, scope: dict, release: dict, *,
                 authority: ExternalAuthority | None, journal: ExposureJournal | None,
                 freeze_recheck, excluded_actor_refs: frozenset[str] = frozenset()):
        self.root = Path(root)
        if len(references) > 1000:
            raise ExposureRefused('private_inventory_bound_exceeded')
        self.references = json.loads(canonical_bytes(references))
        self.scope, self.release = json.loads(canonical_bytes(scope)), json.loads(canonical_bytes(release))
        self._references_hash, self._release_hash = digest(self.references), digest(self.release)
        self.authority, self.journal = authority, journal
        self.freeze_recheck, self.excluded = freeze_recheck, excluded_actor_refs
        self.use_key = digest(scope)
        self._active, self._entered, self._reads = False, False, 0
        self._reader = None

    def _check(self):
        if (not self._active or digest(self.scope) != self.use_key or digest(self.release) != self._release_hash
                or digest(self.references) != self._references_hash
                or root_binding(self.root) != self.scope['owned_root_binding']):
            raise ExposureRefused('private_use_not_active_or_root_changed')
        if datetime.now(timezone.utc) >= utc(self.release['retention_until_utc']):
            self._active = False
            raise ExposureRefused('private_use_expired_no_further_access')

    def __enter__(self):
        if self._entered or self.authority is None or self.journal is None:
            raise ExposureRefused('real_authority_and_durable_journal_required_no_resume')
        if self.freeze_recheck() is not True:
            raise ExposureRefused('independent_freeze_no_longer_authenticated')
        if root_binding(self.root) != self.scope['owned_root_binding']:
            raise ExposureRefused('private_owned_root_identity_mismatch')
        if datetime.now(timezone.utc) >= utc(self.release['retention_until_utc']):
            raise ExposureRefused('private_release_expired')
        if not all(self.release.get(k) for k in ('ownership_reference', 'rights_reference', 'custody_reference')):
            raise ExposureRefused('private_ownership_rights_custody_evidence_missing')
        if (self.release.get('journal_root_binding') != self.journal.binding
                or not self.release.get('journal_ownership_reference')):
            raise ExposureRefused('durable_accounting_root_ownership_not_bound')
        if type(self.release.get('maximum_artifact_reads')) is not int or not 1 <= self.release['maximum_artifact_reads'] <= 100000:
            raise ExposureRefused('private_read_bound_missing')
        inventory = {name: ref for name, ref in self.references.items() if name not in {'freeze', 'release'}}
        if self.release.get('artifact_scope_hash') != digest(inventory):
            raise ExposureRefused('private_artifact_inventory_not_bound_by_release')
        if self.release.get('owned_root_binding') != self.scope['owned_root_binding']:
            raise ExposureRefused('private_release_does_not_bind_owned_root')
        self._entered = True
        self.journal.append(self.use_key, 'use_intent', {'scope': self.scope, 'result': 'pending_external_redemption'})
        try:
            admitted = externally_authorized(self.authority, 'custody_release', self.release,
                                             self.scope, self.excluded)
        except Exception as error:
            self.journal.append(self.use_key, 'redemption_result', {'result': 'unknown', 'error_type': type(error).__name__,
                                 'automatic_retry': False, 'refund': False})
            raise ExposureRefused('external_redemption_outcome_unknown_no_access_or_retry') from error
        # If this append fails after remote admission, the durable intent remains
        # uncertain and no private access is enabled. Never refund/retry locally.
        self.journal.append(self.use_key, 'redemption_result', {'result': 'admitted' if admitted else 'refused',
                             'automatic_retry': False, 'refund': False})
        if not admitted:
            raise ExposureRefused('external_redemption_refused_no_private_access')
        self._active = True
        return self

    def __call__(self, candidate: dict) -> bool:
        # Existing gate seam: same already-redeemed in-memory use, not a cached
        # receipt reconstructed after a crash and not another external redemption.
        self._check()
        return digest(candidate) == self.scope['release_hash']

    def require_output_root(self, root: str | Path) -> None:
        self._check()
        if root_binding(root) not in self.release.get('allowed_output_root_bindings', []):
            raise ExposureRefused('private_derivative_output_root_not_authorized')

    def reader(self, root: str | Path, references: dict) -> ArtifactReader:
        self._check()
        if root_binding(root) != self.scope['owned_root_binding'] or references != self.references:
            raise ExposureRefused('private_reader_scope_changed')
        if self._reader is None:
            self._reader = _PrivateReader(root, self.references, self)
        return self._reader

    def __exit__(self, error_type, error, traceback):
        active = self._active
        self._active = False
        self.journal.append(self.use_key, 'use_result', {'result': 'closed' if error_type is None and active else 'unknown_or_failed',
                             'error_type': error_type.__name__ if error_type else None,
                             'read_attempts': self._reads, 'automatic_retry': False, 'refund': False})


class _PrivateReader(ArtifactReader):
    def __init__(self, root, references, use):
        super().__init__(root, references)
        self._use = use

    def read(self, name: str, kind: str, *, maximum: int = 32_000_000) -> bytes:
        use = self._use
        use._check()
        if name in {'freeze', 'release'}:
            raise ExposureRefused('public_records_use_imported_metadata_not_private_reader')
        limit = use.release.get('maximum_artifact_reads')
        if type(limit) is not int or not 1 <= limit <= 100000 or use._reads >= limit:
            raise ExposureRefused('private_artifact_read_budget_missing_or_exhausted')
        use._reads += 1
        reference = self.references[name]
        try:
            use.journal.append(use.use_key, 'artifact_intent', {'reference_hash': digest(reference), 'ordinal': use._reads})
            raw = super().read(name, kind, maximum=maximum)
            use._check()
            use.journal.append(use.use_key, 'artifact_result', {'reference_hash': digest(reference),
                               'result': 'verified_bytes_read', 'sha256': self.verified[name]})
        except BaseException as error:
            use._active = False
            use.journal.append(use.use_key, 'artifact_result', {'reference_hash': digest(reference),
                               'result': 'unknown_or_refused', 'error_type': type(error).__name__})
            raise
        return raw


class PublicReadAccess:
    """Real signed public-dev cohort/rights binding; not a private-use shortcut."""
    def __init__(self, authority: ExternalAuthority | None, records: dict, record_hashes: dict,
                 scope: dict, *, excluded_actor_refs: frozenset[str] = frozenset()):
        self.authority = authority
        self.records, self.hashes, self.scope = (json.loads(canonical_bytes(value)) for value in (records, record_hashes, scope))
        self.excluded = excluded_actor_refs

    def check(self, root: str | Path, references: dict) -> None:
        if self.scope.get('split') != 'dev' or self.scope.get('access_classification') != 'public_dev_only':
            raise ExposureRefused('public_access_cannot_admit_private_or_heldout')
        if self.scope.get('owned_root_binding') != root_binding(root) or not self.scope.get('ownership_reference'):
            raise ExposureRefused('public_owned_root_authority_missing_or_changed')
        inventory = {name: ref for name, ref in references.items() if name not in {'freeze', 'release'}}
        if self.scope.get('artifact_scope_hash') != digest(inventory):
            raise ExposureRefused('public_inventory_not_bound_by_real_authority')
        window = self.scope.get('window', {})
        now = datetime.now(timezone.utc)
        if (not window.get('starts_at') or not window.get('expires_at')
                or not utc(window['starts_at']) <= now < utc(window['expires_at'])):
            raise ExposureRefused('public_access_window_missing_or_expired')
        for purpose in ('cohort_authorization', 'fixture_rights'):
            if not verify_gate_record(purpose, self.records.get(purpose, {}), self.scope,
                    expected_record_hash=self.hashes.get(purpose, ''), authority=self.authority,
                    excluded_actor_refs=self.excluded):
                raise ExposureRefused('public_access_actual_external_authority_required')

    def reader(self, root: str | Path, references: dict) -> ArtifactReader:
        self.check(root, references)
        return _PublicReader(root, json.loads(canonical_bytes(references)), self)


class _PublicReader(ArtifactReader):
    def __init__(self, root, references, access):
        super().__init__(root, references)
        self.access = access

    def read(self, name: str, kind: str, *, maximum: int = 32_000_000) -> bytes:
        self.access.check(self.root, self.references)
        raw = super().read(name, kind, maximum=maximum)
        self.access.check(self.root, self.references)
        return raw


def validate_read_access(reader: ArtifactReader, *, private_required: bool = False) -> None:
    if isinstance(reader, _PrivateReader):
        reader._use._check()
    elif isinstance(reader, _PublicReader) and not private_required:
        reader.access.check(reader.root, reader.references)
    else:
        raise ExposureRefused('actual_guarded_read_context_required_not_an_artifact_flag')


def require_output_root(reader: ArtifactReader, root: str | Path) -> None:
    validate_read_access(reader)
    if isinstance(reader, _PrivateReader):
        reader._use.require_output_root(root)
    elif root_binding(root) not in reader.access.scope.get('allowed_output_root_bindings', []):
        raise ExposureRefused('public_derivative_output_root_not_authorized')


def aliased_reader(reader: ArtifactReader, references: dict) -> ArtifactReader:
    """A scoped role view over the same actual read guard, not a new permission."""
    validate_read_access(reader)
    aliases = {}
    for alias, reference in references.items():
        matches = [name for name, original in reader.references.items() if original == reference
                   and name not in {'freeze', 'release'}]
        if not matches:
            raise ExposureRefused('requested_artifact_not_in_authenticated_inventory')
        aliases[alias] = sorted(matches)[0]
    return _AliasReader(reader, references, aliases)


class _AliasReader(ArtifactReader):
    def __init__(self, reader, references, aliases):
        super().__init__(reader.root, json.loads(canonical_bytes(references)))
        self.reader, self.aliases = reader, aliases

    def read(self, name: str, kind: str, *, maximum: int = 32_000_000) -> bytes:
        raw = self.reader.read(self.aliases[name], kind, maximum=maximum)
        self.verified[name] = self.reader.verified[self.aliases[name]]
        if self.verified[name] != self.references[name]['sha256']:
            raise ExposureRefused('aliased_artifact_hash_binding_changed')
        return raw
