from __future__ import annotations

import json
import hashlib
import sqlite3
from decimal import Decimal, localcontext
from pathlib import Path
from contextlib import contextmanager
import re
import time

from pydantic import TypeAdapter
from voicelab.contracts import Identifier, ReservationTerms


BUDGET_REFUSAL_CODES = frozenset({'ledger_budget_mismatch', 'ledger_admission_halted',
    'ledger_owner_required_for_recovery', 'ledger_owner_mismatch', 'reservation_arm_invalid',
    'known_price_and_enforceable_terms_required', 'reservation_terms_mismatch',
    'live_reservation_owner_and_trial_required', 'fresh_attempt_id_required',
    'component_attempt_limit', 'budget_exhausted', 'dispatch_requires_reserved_known_terms',
    'dispatch_owner_mismatch', 'dispatch_requires_active_authorization',
    'sent_without_dispatch_authorization', 'attempt_bound_enforcement_refused',
    'synchronous_attempt_bound_verifier_required', 'run_price_terms_mismatch', 'budget_policy_refused'})


class BudgetRefused(ValueError):
    def __init__(self, code: str):
        self.code = code if code in BUDGET_REFUSAL_CODES else 'budget_policy_refused'
        super().__init__(self.code)


def money(value: Decimal) -> Decimal:
    if (not isinstance(value, Decimal) or not value.is_finite() or value < 0
            or value.adjusted() > 17 or value.as_tuple().exponent < -18
            or value.as_tuple().exponent > 18 or len(value.as_tuple().digits) > 54):
        raise ValueError("finite_supported_nonnegative_decimal_required")
    return Decimal(0) if value == 0 else value


def money_text(value: Decimal) -> str:
    return format(money(value), 'f')


def safe_usage(usage: dict) -> dict:
    """Only bounded numeric counters enter accounting events, never provider text."""
    count, nodes = 0, 0
    def clean(value, depth):
        nonlocal count, nodes
        nodes += 1
        if nodes > 512:
            raise ValueError('usage_counter_shape_bound')
        if not isinstance(value, dict) or depth > 8 or len(value) > 64:
            raise ValueError('usage_counters_required')
        output = {}
        for key, item in value.items():
            nodes += 1
            if nodes > 512:
                raise ValueError('usage_counter_shape_bound')
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', key):
                raise ValueError('usage_counter_name_invalid')
            if isinstance(item, dict):
                output[key] = clean(item, depth + 1)
            else:
                count += 1
                if count > 128 or type(item) not in (int, str):
                    raise ValueError('usage_counter_value_invalid')
                text = str(item)
                if not re.fullmatch(r'\d{1,18}(?:\.\d{1,18})?', text):
                    raise ValueError('usage_counter_value_invalid')
                output[key] = text
        return output
    return clean(usage, 0)


class AttemptLedger:
    """Each mutation takes a write lock before reading exposure; never uses floats.

    Unknown liability is terminal. Late usage is evidence only, never a refund.
    Over-bound authoritative evidence is retained and closes subsequent admission.
    """

    def __init__(self, sqlite_path: str | Path, budget: Decimal, *, owner_id: str | None = None,
                 recover_owner: bool = False):
        self.budget = money(budget)
        self.owner_id = TypeAdapter(Identifier).validate_python(owner_id) if owner_id is not None else None
        self.path = str(sqlite_path)
        if self.path == ':memory:':
            raise ValueError('durable_ledger_path_required')
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY, logical_turn_id TEXT NOT NULL,
                    arm TEXT NOT NULL, component TEXT NOT NULL, reservation TEXT NOT NULL,
                    price_version TEXT NOT NULL, basis TEXT NOT NULL,
                    state TEXT NOT NULL, settled TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS attempt_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ledger_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempt_context (
                    attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
                    terms_json TEXT NOT NULL, owner_id TEXT,
                    authorized_ns INTEGER, sent_ns INTEGER, provider_request_id TEXT,
                    trial_id TEXT, sent_source TEXT);
            """)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(attempt_context)')}
            if 'trial_id' not in columns:
                db.execute('ALTER TABLE attempt_context ADD COLUMN trial_id TEXT')
            if 'sent_source' not in columns:
                db.execute('ALTER TABLE attempt_context ADD COLUMN sent_source TEXT')
            db.execute('BEGIN IMMEDIATE')
            old = db.execute("SELECT value FROM ledger_settings WHERE key='budget'").fetchone()
            if old and Decimal(old[0]) != self.budget:
                raise BudgetRefused('ledger_budget_mismatch')
            db.execute("INSERT OR IGNORE INTO ledger_settings VALUES ('budget',?)", (money_text(self.budget),))
            if self.owner_id is not None:
                prior_owner = db.execute("SELECT value FROM ledger_settings WHERE key='owner_id'").fetchone()
                if prior_owner is not None and prior_owner[0] != self.owner_id and not recover_owner:
                    raise BudgetRefused('ledger_owner_mismatch')
                db.execute("INSERT INTO ledger_settings VALUES ('owner_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.owner_id,))
        if self.owner_id is not None:
            self.recover_unfinished()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db, attempt_id, kind, payload):
        db.execute("INSERT INTO attempt_events(attempt_id,kind,payload) VALUES (?,?,?)",
                   (attempt_id, kind, json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)))

    @staticmethod
    def _exposure(db):
        with localcontext() as context:
            context.prec = 96
            return sum((Decimal(row['settled'] if row['state'] == 'settled' else row['reservation'])
                        for row in db.execute('SELECT * FROM attempts')), Decimal(0))

    def _guard(self, db):
        stored = db.execute("SELECT value FROM ledger_settings WHERE key='budget'").fetchone()
        if stored is None or Decimal(stored[0]) != self.budget:
            raise BudgetRefused('ledger_budget_mismatch')
        owner = db.execute("SELECT value FROM ledger_settings WHERE key='owner_id'").fetchone()
        if self.owner_id is not None and (owner is None or owner[0] != self.owner_id):
            raise BudgetRefused('ledger_owner_mismatch')
        if db.execute("SELECT value FROM ledger_settings WHERE key='halted'").fetchone():
            raise BudgetRefused('ledger_admission_halted')

    def recover_unfinished(self) -> None:
        """Single artifact-root owner: prior boots cannot leave reusable reservations."""
        if self.owner_id is None:
            raise BudgetRefused('ledger_owner_required_for_recovery')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute("SELECT value FROM ledger_settings WHERE key='owner_id'").fetchone()
            if current is None or current[0] != self.owner_id:
                raise BudgetRefused('ledger_owner_mismatch')
            rows = db.execute("""SELECT a.attempt_id,c.owner_id,c.authorized_ns,c.sent_ns
                FROM attempts a LEFT JOIN attempt_context c USING(attempt_id)
                WHERE a.state='reserved' AND (c.owner_id IS NULL OR c.owner_id!=?)""", (self.owner_id,)).fetchall()
            for row in rows:
                db.execute("UPDATE attempts SET state='unknown',reason='service_restart' WHERE attempt_id=? AND state='reserved'", (row['attempt_id'],))
                self._event(db, row['attempt_id'], 'unknown_notice', {'reason': 'service_restart',
                    'owner_id': self.owner_id, 'prior_owner_id': row['owner_id'],
                    'dispatch_authorized': row['authorized_ns'] is not None,
                    'transport_send_returned': row['sent_ns'] is not None})

    @staticmethod
    def _mock_terms(component: str, basis: str) -> ReservationTerms:
        return ReservationTerms(mode='mock', component=component, model='keyless-mock', currency='USD',
            price_version='mock-zero-v1', pricing_reference='mock-zero-v1',
            capability_reference='no-provider-egress', enforcement_reference='keyless-mock-source',
            bound_usd='0', basis=basis, rates={'provider_requests': '0'},
            usage_bounds={'provider_requests': '0'}, usage_units={'provider_requests': 'requests'})

    def reserve(self, attempt_id: str, *, amount: Decimal, logical_turn_id: str,
                arm: str, component: str, price_version: str, basis: str,
                terms: ReservationTerms | dict | None = None, trial_id: str | None = None) -> dict:
        amount = money(amount)
        TypeAdapter(Identifier).validate_python(attempt_id)
        TypeAdapter(Identifier).validate_python(logical_turn_id)
        if arm not in {'V', 'C', 'K'}:
            raise BudgetRefused('reservation_arm_invalid')
        if terms is None:
            if amount != 0 or price_version != 'mock-zero-v1':
                raise BudgetRefused('known_price_and_enforceable_terms_required')
            terms = self._mock_terms(component, basis)
        else:
            terms = ReservationTerms.model_validate(terms)
        if (terms.component != component or terms.price_version != price_version or terms.basis != basis
                or Decimal(terms.bound_usd) != amount):
            raise BudgetRefused('reservation_terms_mismatch')
        encoded_terms = json.dumps(terms.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False)
        if trial_id is not None:
            TypeAdapter(Identifier).validate_python(trial_id)
        if terms.mode == 'live' and (trial_id is None or self.owner_id is None):
            raise BudgetRefused('live_reservation_owner_and_trial_required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._guard(db)
            if terms.mode == 'live':
                fingerprint = hashlib.sha256(encoded_terms.encode('utf-8')).hexdigest()
                key = 'terms.' + component
                prior = db.execute('SELECT value FROM ledger_settings WHERE key=?', (key,)).fetchone()
                version = db.execute("SELECT value FROM ledger_settings WHERE key='price_version'").fetchone()
                if (prior is not None and prior[0] != fingerprint) or (version is not None and version[0] != price_version):
                    raise BudgetRefused('run_price_terms_mismatch')
                db.execute('INSERT OR IGNORE INTO ledger_settings VALUES (?,?)', (key, fingerprint))
                db.execute("INSERT OR IGNORE INTO ledger_settings VALUES ('price_version',?)", (price_version,))
            old = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if old:
                raise BudgetRefused('fresh_attempt_id_required')
            if trial_id is not None:
                count = db.execute('''SELECT COUNT(*) FROM attempts a JOIN attempt_context c USING(attempt_id)
                    WHERE c.trial_id=? AND a.logical_turn_id=? AND a.component=? AND a.arm=?''',
                    (trial_id, logical_turn_id, component, arm)).fetchone()[0]
                if count >= 3:
                    raise BudgetRefused('component_attempt_limit')
            with localcontext() as context:
                context.prec = 96
                over_budget = self._exposure(db) + amount > self.budget
            if over_budget:
                raise BudgetRefused('budget_exhausted')
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (attempt_id, logical_turn_id, arm, component, money_text(amount), price_version,
                        basis, 'reserved', None, None))
            db.execute('''INSERT INTO attempt_context
                (attempt_id,terms_json,owner_id,authorized_ns,sent_ns,provider_request_id,trial_id)
                VALUES (?,?,?,?,?,?,?)''', (attempt_id, encoded_terms, self.owner_id, None, None, None, trial_id))
            self._event(db, attempt_id, 'reserved', {'amount': money_text(amount), 'basis': basis,
                'price_version': price_version, 'owner_id': self.owner_id})
            return dict(db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone())

    def authorize_dispatch(self, attempt_id: str) -> None:
        """Atomic linearization before dispatch; not proof of remote receipt or charge."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._guard(db)
            row = db.execute('SELECT state FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            entry = db.execute('SELECT * FROM attempt_context WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None or row['state'] != 'reserved' or entry is None:
                raise BudgetRefused('dispatch_requires_reserved_known_terms')
            terms = ReservationTerms.model_validate_json(entry['terms_json'])
            if terms.mode == 'live' and entry['owner_id'] != self.owner_id:
                raise BudgetRefused('dispatch_owner_mismatch')
            if entry['authorized_ns'] is None:
                stamp = time.monotonic_ns()
                db.execute('UPDATE attempt_context SET authorized_ns=? WHERE attempt_id=?', (stamp, attempt_id))
                self._event(db, attempt_id, 'dispatch_authorized', {'server_ns': stamp,
                    'clock_domain': 'server_monotonic_ns', 'owner_id': self.owner_id,
                    'boundary': 'before_transport_call' if terms.mode == 'live' else 'keyless_mock_component_start',
                    'remote_charge': 'unknown' if terms.mode == 'live' else 'none_mock'})

    def ensure_dispatch_allowed(self, attempt_id: str) -> None:
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._guard(db)
            row = db.execute('''SELECT a.state,c.authorized_ns,c.owner_id,c.terms_json FROM attempts a
                LEFT JOIN attempt_context c USING(attempt_id) WHERE attempt_id=?''', (attempt_id,)).fetchone()
            if row is None or row['state'] != 'reserved' or row['authorized_ns'] is None:
                raise BudgetRefused('dispatch_requires_active_authorization')
            if ReservationTerms.model_validate_json(row['terms_json']).mode == 'live' and row['owner_id'] != self.owner_id:
                raise BudgetRefused('dispatch_owner_mismatch')

    def record_sent(self, attempt_id: str, *, boundary: str = 'websocket_send_returned',
                    transport_source: str = 'provider_network') -> None:
        # Evidence after a transport call returns must survive a concurrent halt/unknown.
        if boundary not in {'websocket_send_returned', 'first_tts_pcm_received'}:
            raise ValueError('dispatch_observation_boundary_invalid')
        if transport_source not in {'provider_network', 'injected_unverified'}:
            raise ValueError('dispatch_observation_source_invalid')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempt_context WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None or row['authorized_ns'] is None:
                raise BudgetRefused('sent_without_dispatch_authorization')
            if row['sent_ns'] is None:
                stamp = time.monotonic_ns()
                db.execute('UPDATE attempt_context SET sent_ns=?,sent_source=? WHERE attempt_id=?', (stamp, transport_source, attempt_id))
                self._event(db, attempt_id, 'transport_send_returned', {'server_ns': stamp,
                    'clock_domain': 'server_monotonic_ns', 'owner_id': self.owner_id,
                    'boundary': boundary,
                    'transport_source': transport_source,
                    'remote_charge': 'unknown'})

    def settle(self, attempt_id: str, *, amount: Decimal, usage: dict, source: str,
               violation_dimensions: tuple[str, ...] = ()) -> dict:
        try:
            amount = money(amount)
        except ValueError:
            if isinstance(amount, Decimal):
                return self._unsupported_usage_amount(attempt_id, amount)
            raise
        if len(violation_dimensions) > 32 or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*){0,7}', path) or len(path) > 160 for path in violation_dimensions):
            raise ValueError('invalid_usage_dimension_reference')
        if not isinstance(source, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', source):
            raise ValueError('authoritative_usage_source_code_required')
        try:
            counters = safe_usage(usage)
            usage_valid = True
        except ValueError:
            counters, usage_valid = {}, False
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise ValueError('unreserved_attempt')
            kind = 'settled' if row['state'] == 'reserved' else 'late_or_duplicate_usage'
            self._event(db, attempt_id, kind if usage_valid else 'invalid_usage_evidence',
                {'amount': money_text(amount), 'usage': counters, 'source': source,
                 'usage_observation': 'observed' if usage_valid else 'unknown', 'prior_state': row['state']})
            # Enforce a broken bound in EVERY state. Never reconcile unknown exposure
            # or charge a duplicate settlement while recording authoritative late evidence.
            if amount > Decimal(row['reservation']) or violation_dimensions:
                self._event(db, attempt_id, 'bound_exceeded', {'amount': money_text(amount),
                    'reservation': row['reservation'], 'prior_state': row['state'],
                    'dimensions': list(violation_dimensions), 'terminal_row_preserved': row['state'] != 'reserved'})
                db.execute("INSERT OR IGNORE INTO ledger_settings VALUES ('halted','bound_exceeded')")
            if row['state'] == 'reserved' and usage_valid:
                db.execute("UPDATE attempts SET state='settled',settled=? WHERE attempt_id=?",
                           (money_text(amount), attempt_id))
            elif row['state'] == 'reserved':
                db.execute("UPDATE attempts SET state='unknown',reason='invalid_usage_dimension' WHERE attempt_id=?", (attempt_id,))
            return dict(db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone())

    def _unsupported_usage_amount(self, attempt_id: str, amount: Decimal) -> dict:
        # Do not format an unbounded exponent or turn invalid metering into a refund.
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise ValueError('unreserved_attempt')
            breached = amount.is_finite() and amount >= 0 and amount > Decimal(row['reservation'])
            self._event(db, attempt_id, 'bound_exceeded' if breached else 'invalid_usage_evidence',
                {'reason': 'usage_amount_outside_supported_range', 'prior_state': row['state'],
                 'amount': 'unsupported', 'terminal_row_preserved': row['state'] != 'reserved'})
            db.execute("INSERT OR IGNORE INTO ledger_settings VALUES ('halted',?)",
                ('bound_exceeded' if breached else 'usage_amount_invalid',))
            if row['state'] == 'reserved':
                db.execute("UPDATE attempts SET state='unknown',reason='invalid_usage_dimension' WHERE attempt_id=?", (attempt_id,))
            return dict(db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone())

    def settle_usage(self, attempt_id: str, *, usage: dict, source: str) -> dict:
        with self._connect() as db:
            entry = db.execute('SELECT terms_json FROM attempt_context WHERE attempt_id=?', (attempt_id,)).fetchone()
        if entry is None:
            return self.mark_unknown(attempt_id, reason='missing_price_terms')
        terms = ReservationTerms.model_validate_json(entry['terms_json'])
        if not isinstance(usage, dict):
            return self.mark_unknown(attempt_id, reason='missing_authoritative_usage')
        selected, violations, missing, invalid = {}, [], False, False
        with localcontext() as context:
            context.prec = 96
            amount = Decimal(0)
            for path, rate in terms.rates.items():
                value = usage
                for segment in path.split('.'):
                    value = value.get(segment) if isinstance(value, dict) else None
                if value is None:
                    missing = True
                    continue
                if type(value) not in (int, str) or not re.fullmatch(r'\d{1,18}(?:\.\d{1,18})?', str(value)):
                    invalid = True
                    continue
                units = Decimal(str(value))
                if terms.usage_units[path] != 'seconds' and units != units.to_integral_value():
                    invalid = True
                    continue
                if units > Decimal(terms.usage_bounds[path]):
                    violations.append(path)
                amount += units * Decimal(rate)
                cursor = selected
                segments = path.split('.')
                for segment in segments[:-1]:
                    cursor = cursor.setdefault(segment, {})
                cursor[segments[-1]] = str(value)
        if missing or invalid:
            if violations:
                with self._connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    self._event(db, attempt_id, 'bound_exceeded', {'dimensions': violations,
                        'reason': 'observed_bound_breach_with_incomplete_usage', 'amount': 'unknown'})
                    db.execute("INSERT OR IGNORE INTO ledger_settings VALUES ('halted','bound_exceeded')")
            return self.mark_unknown(attempt_id, reason='invalid_usage_dimension' if invalid else 'missing_usage_dimension')
        try:
            money(amount)
        except ValueError:
            return self._unsupported_usage_amount(attempt_id, amount)
        return self.settle(attempt_id, amount=amount, usage=selected, source=source,
                           violation_dimensions=tuple(violations))

    def mark_unknown(self, attempt_id: str, *, reason: str) -> dict:
        if not reason:
            raise ValueError('unknown_reason_required')
        if not isinstance(reason, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,95}', reason):
            reason = 'usage_unresolved'
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise ValueError('unreserved_attempt')
            self._event(db, attempt_id, 'unknown_notice', {'reason': reason})
            if row['state'] == 'reserved':
                db.execute("UPDATE attempts SET state='unknown',reason=? WHERE attempt_id=?", (reason, attempt_id))
            return dict(db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone())

    def summary(self) -> dict:
        with self._connect() as db:
            # Bind attempts, usage evidence and admission status to one read snapshot.
            db.execute('BEGIN')
            rows = [dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY attempt_id')]
            events = [dict(r) for r in db.execute('SELECT * FROM attempt_events ORDER BY sequence')]
            halted = db.execute("SELECT value FROM ledger_settings WHERE key='halted'").fetchone()
            owner = db.execute("SELECT value FROM ledger_settings WHERE key='owner_id'").fetchone()
            policies = {row['key'][6:]: row['value'] for row in db.execute("SELECT key,value FROM ledger_settings WHERE key LIKE 'terms.%'")}
            contexts = {row['attempt_id']: row for row in db.execute('SELECT * FROM attempt_context')}
            for row in rows:
                details = contexts.get(row['attempt_id'])
                row.update(dispatch_authorized=details is not None and details['authorized_ns'] is not None,
                    dispatched=True if details is not None and details['sent_ns'] is not None and details['sent_source'] == 'provider_network' else None,
                    started=True if details is not None and details['sent_ns'] is not None else None,
                    owner_id=details['owner_id'] if details is not None else None,
                    trial_id=details['trial_id'] if details is not None else None,
                    provider_request_id=details['provider_request_id'] if details is not None else None,
                    reservation_terms=json.loads(details['terms_json']) if details is not None else None)
                row['transport_source'] = details['sent_source'] if details is not None else None
            with localcontext() as context:
                context.prec = 96
                known = sum((Decimal(r['settled']) for r in rows if r['state'] == 'settled'), Decimal(0))
                exposure = sum((Decimal(r['reservation']) for r in rows if r['state'] != 'settled'), Decimal(0))
                total_exposure = known + exposure
            unresolved = [r for r in rows if r['state'] != 'settled']
            violations = sum(event['kind'] == 'bound_exceeded' for event in events)
            return {'budget': money_text(self.budget), 'known_cost': format(known, 'f'),
                    'unknown_count': len(unresolved), 'unknown_exposure': format(exposure, 'f'),
                    'complete': not unresolved, 'total_exposure': format(total_exposure, 'f'), 'attempts': rows,
                    'events': events, 'admission_halted': halted[0] if halted else None, 'currency': 'USD',
                    'bound_violation_count': violations, 'bound_integrity': 'invalid' if violations else 'not_disproved',
                    'bound_violation_attempt_count': len({event['attempt_id'] for event in events if event['kind'] == 'bound_exceeded'}),
                    'dispatch_halt_active': halted is not None,
                    'dispatch_allowed': False if halted else None,
                    'owner_id': owner[0] if owner else None, 'snapshot_owner_id': self.owner_id,
                    'price_policy_fingerprints': policies,
                    'total_exposure_definition': 'immutable known settlements plus reserved/unknown exposure; late usage evidence is not a reconciliation'}
