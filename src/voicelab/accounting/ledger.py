from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from contextlib import contextmanager


class BudgetRefused(ValueError):
    pass


def money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("amount must be a finite nonnegative Decimal")
    return value


class AttemptLedger:
    """Each mutation takes a write lock before reading exposure; never uses floats.

    Unknown liability is terminal. Late usage is evidence only, never a refund.
    An over-bound bill is retained in full and closes subsequent admission.
    """

    def __init__(self, sqlite_path: str | Path, budget: Decimal):
        self.budget = money(budget)
        self.path = str(sqlite_path)
        if self.path == ':memory:':
            raise ValueError('durable ledger requires a file path')
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
            """)
            db.execute('BEGIN IMMEDIATE')
            old = db.execute("SELECT value FROM ledger_settings WHERE key='budget'").fetchone()
            if old and Decimal(old[0]) != self.budget:
                raise BudgetRefused("existing ledger budget differs; explicit reconciliation required")
            db.execute("INSERT OR IGNORE INTO ledger_settings VALUES ('budget',?)", (str(self.budget),))

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
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
        return sum((Decimal(row['settled'] if row['state'] == 'settled' else row['reservation'])
                    for row in db.execute('SELECT * FROM attempts')), Decimal(0))

    def reserve(self, attempt_id: str, *, amount: Decimal, logical_turn_id: str,
                arm: str, component: str, price_version: str, basis: str) -> dict:
        amount = money(amount)
        if not all((attempt_id, logical_turn_id, price_version, basis)) or arm not in {'V', 'C', 'K'}:
            raise BudgetRefused('identity, known price version and enforceable bound basis required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT value FROM ledger_settings WHERE key='halted'").fetchone():
                raise BudgetRefused('ledger halted after bound violation')
            old = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if old:
                raise BudgetRefused('attempt already reserved; retries require a fresh attempt ID')
            if self._exposure(db) + amount > self.budget:
                raise BudgetRefused('budget_exhausted')
            db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (attempt_id, logical_turn_id, arm, component, str(amount), price_version,
                        basis, 'reserved', None, None))
            self._event(db, attempt_id, 'reserved', {'amount': str(amount), 'basis': basis})
            return dict(db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone())

    def settle(self, attempt_id: str, *, amount: Decimal, usage: dict, source: str) -> dict:
        amount = money(amount)
        if not source:
            raise ValueError('authoritative usage source required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise ValueError('unreserved attempt')
            kind = 'settled' if row['state'] == 'reserved' else 'late_or_duplicate_usage'
            self._event(db, attempt_id, kind, {'amount': str(amount), 'usage': usage, 'source': source})
            if row['state'] == 'reserved':
                db.execute("UPDATE attempts SET state='settled',settled=? WHERE attempt_id=?",
                           (str(amount), attempt_id))
                if amount > Decimal(row['reservation']):
                    self._event(db, attempt_id, 'bound_exceeded', {'amount': str(amount)})
                    db.execute("INSERT OR REPLACE INTO ledger_settings VALUES ('halted','bound_exceeded')")
            return dict(db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone())

    def mark_unknown(self, attempt_id: str, *, reason: str) -> dict:
        if not reason:
            raise ValueError('unknown reason required')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row is None:
                raise ValueError('unreserved attempt')
            self._event(db, attempt_id, 'unknown_notice', {'reason': reason})
            if row['state'] == 'reserved':
                db.execute("UPDATE attempts SET state='unknown',reason=? WHERE attempt_id=?", (reason, attempt_id))
            return dict(db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone())

    def summary(self) -> dict:
        with self._connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY attempt_id')]
            events = [dict(r) for r in db.execute('SELECT * FROM attempt_events ORDER BY sequence')]
            halted = db.execute("SELECT value FROM ledger_settings WHERE key='halted'").fetchone()
            known = sum((Decimal(r['settled']) for r in rows if r['state'] == 'settled'), Decimal(0))
            unresolved = [r for r in rows if r['state'] != 'settled']
            exposure = sum((Decimal(r['reservation']) for r in unresolved), Decimal(0))
            return {'budget': str(self.budget), 'known_cost': str(known),
                    'unknown_count': len(unresolved), 'unknown_exposure': str(exposure),
                    'complete': not unresolved, 'total_exposure': str(known + exposure), 'attempts': rows,
                    'events': events, 'admission_halted': halted[0] if halted else None, 'currency': 'USD'}
