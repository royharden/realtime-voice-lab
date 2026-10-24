"""AttemptLedger exposure contracts (plan section 7; R3 accounting boundary c27b770). NOT YET RUN.

Terms are synthetic 'live'-mode declarations because only that mode carries a nonzero bound.
Nothing here dispatches, contacts a provider or represents a real price.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading

import pytest

from voicelab.accounting.ledger import AttemptLedger, BudgetRefused

OWNER = 'regression-owner'
BOUND = '0.40'


def _terms() -> dict:
    return {'mode': 'live', 'component': 'native', 'model': 'synthetic-regression-model',
            'currency': 'USD', 'price_version': 'synthetic-v1',
            'pricing_reference': 'synthetic-pricing-fixture',
            'capability_reference': 'synthetic-capability-fixture',
            'enforcement_reference': 'synthetic-enforcement-fixture', 'bound_usd': BOUND,
            'basis': 'synthetic regression bound', 'rates': {'provider_requests': BOUND},
            'usage_bounds': {'provider_requests': '1'},
            'usage_units': {'provider_requests': 'requests'}}


def _reserve(ledger: AttemptLedger, attempt: str, turn: str) -> dict:
    return ledger.reserve(attempt, amount=Decimal(BOUND), logical_turn_id=turn, arm='V',
                          component='native', price_version='synthetic-v1',
                          basis='synthetic regression bound', terms=_terms(), trial_id='trial-1',
                          session_id='session-1', transport_turn_id='transport-' + turn,
                          connection_epoch=0, response_epoch=0)


def test_late_over_bound_usage_after_unknown_halts_without_release_or_double_settle(tmp_path):
    """what_bug_this_catches: late authoritative usage arriving after a terminal unknown was
    treated as a reconciliation - releasing the immutable reserved exposure (a refund) or
    settling a second time - and an over-bound amount did not close admission. ledger.py:
    unknown stays unknown, late usage is evidence only, bound breach halts all admission."""
    ledger = AttemptLedger(tmp_path / 'ledger.sqlite', Decimal('1.00'), owner_id=OWNER)
    _reserve(ledger, 'attempt-1', 'turn-1')
    assert ledger.mark_unknown('attempt-1', reason='transport_loss')['state'] == 'unknown'
    late = ledger.settle('attempt-1', amount=Decimal('0.55'), usage={'provider_requests': 1},
                         source='provider_terminal_usage')
    assert late['state'] == 'unknown' and late['settled'] is None
    again = ledger.settle('attempt-1', amount=Decimal('0.55'), usage={'provider_requests': 1},
                          source='provider_terminal_usage')
    assert again['state'] == 'unknown' and again['settled'] is None
    summary = ledger.summary()
    assert Decimal(summary['known_cost']) == 0
    assert Decimal(summary['unknown_exposure']) == Decimal(BOUND)
    assert Decimal(summary['total_exposure']) == Decimal(BOUND)
    assert summary['unknown_count'] == 1 and summary['complete'] is False
    assert summary['admission_halted'] == 'bound_exceeded'
    assert summary['bound_integrity'] == 'invalid'
    kinds = [event['kind'] for event in summary['events']]
    assert kinds.count('late_or_duplicate_usage') == 2 and 'settled' not in kinds
    with pytest.raises(BudgetRefused) as refused:
        _reserve(ledger, 'attempt-2', 'turn-2')
    assert refused.value.code == 'ledger_admission_halted'


def test_concurrent_reservations_cannot_exceed_envelope(tmp_path):
    """what_bug_this_catches: check-then-act admission where concurrent reservers each read
    exposure before any insert, so the sum of reservations overran the budget. ledger.py
    takes BEGIN IMMEDIATE before reading exposure; with 1.00 and 0.40 bounds exactly two of
    five racing reservations may succeed (reserve-before-call)."""
    path = tmp_path / 'ledger.sqlite'
    ledgers = [AttemptLedger(path, Decimal('1.00'), owner_id=OWNER) for _ in range(5)]
    barrier = threading.Barrier(len(ledgers))

    def attempt(index: int) -> str:
        barrier.wait()
        try:
            _reserve(ledgers[index], f'attempt-{index}', f'turn-{index}')
            return 'reserved'
        except BudgetRefused as error:
            return error.code

    with ThreadPoolExecutor(max_workers=len(ledgers)) as pool:
        outcomes = list(pool.map(attempt, range(len(ledgers))))
    assert outcomes.count('reserved') == 2
    assert outcomes.count('budget_exhausted') == 3
    summary = ledgers[0].summary()
    assert Decimal(summary['total_exposure']) == Decimal('0.80')
    assert Decimal(summary['total_exposure']) <= Decimal(summary['budget'])
