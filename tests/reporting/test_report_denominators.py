"""Denominator-preserving report contracts (plan M6-04; F2 repair 7efa85e). NOT YET RUN."""
from __future__ import annotations

from decimal import Decimal

import pytest

from voicelab.reporting.reports import build_report, quantile


def _clean_cohort(report: dict) -> dict:
    cohorts = [c for c in report['cohorts'] if c['identity']['cohort'] == 'clean']
    assert len(cohorts) == 1
    return cohorts[0]


def test_missing_known_started_turn_is_p95_nonattainment(report_builders):
    """what_bug_this_catches: F2 - 18 clean successes plus 1 independently-started turn with
    no terminal row reported n=18 and a finite p95 (survivorship). The scheduled, started
    turn must stay in the denominator as nonattainment: n=19, nearest rank 19 is '>10 s'."""
    b = report_builders
    turns = [f'turn-{i:02d}' for i in range(19)]
    manifest = b.run_manifest(turns, [b.scheduled_turn(t) for t in turns])
    rows = [b.clean_success_row(t, 400.0 + i) for i, t in enumerate(turns[:18])]
    report = build_report(manifest, rows, b.empty_ledger())
    cohort = _clean_cohort(report)
    assert cohort['started'] == 19
    assert cohort['all_started_denominator_complete'] is True
    p95 = cohort['all_started_clean_acceptable_render']['p95']
    assert p95['n'] == 19 and p95['rank'] == 19
    assert p95['value_ms'] is None
    assert p95['label'] == '>10 s / not attained'
    missing = [r for r in report['rows'] if r['logical_turn_id'] == 'turn-18'][0]
    assert missing['outcome'] == 'missing_terminal' and missing['admission'] == 'started'


def test_quantile_orders_nonattainment_after_attained_values():
    """what_bug_this_catches: dropping None before ranking, which shrinks the denominator.
    The canonical nearest-rank helper must keep n and sort nonattainment last."""
    values = [Decimal(400 + i) for i in range(18)] + [None]
    assert quantile(values, Decimal('.95'))['value_ms'] is None
    assert quantile(values, Decimal('.50')) == {'n': 19, 'rank': 10, 'value_ms': '409',
                                                'label': '409 ms'}


@pytest.mark.parametrize('variant', ['unknown_start', 'missing_assignment'])
def test_unknown_cohort_or_start_withholds_headline(report_builders, variant):
    """what_bug_this_catches: publishing a finite reduced-denominator headline when a
    scheduled turn's cohort assignment or start evidence is unknown (plan: unknowns cannot
    become zero or pass). reports.py must withhold p50/p95 and mark the cohort incomplete."""
    b = report_builders
    turns = [f'turn-{i:02d}' for i in range(19)]
    assignments = [b.scheduled_turn(t) for t in turns[:18]]
    if variant == 'unknown_start':
        assignments.append(b.scheduled_turn(turns[18], admission='unknown',
                                            admission_evidence_reference=None))
    manifest = b.run_manifest(turns, assignments)
    rows = [b.clean_success_row(t, 400.0 + i) for i, t in enumerate(turns[:18])]
    report = build_report(manifest, rows, b.empty_ledger())
    assert report['all_started_denominator_complete'] is False
    assert report['denominator_incompleteness_reasons']
    cohort = _clean_cohort(report)
    assert cohort['all_started_denominator_complete'] is False
    for name in ('p50', 'p95'):
        withheld = cohort['all_started_clean_acceptable_render'][name]
        assert withheld['value_ms'] is None and withheld['rank'] is None
        assert withheld['complete'] is False
        assert withheld['label'].startswith('withheld')
    assert any(item['scope'] == 'cohort' and 'withheld' in item['reason']
               for item in report['failed_items'])
