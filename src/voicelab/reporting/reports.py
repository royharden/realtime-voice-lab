"""Deterministic, denominator-preserving descriptive reports; no fabricated data."""
from __future__ import annotations

import hashlib
import json
import random
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from voicelab.contracts import RunManifest, TurnResult


def quantile(values: list[Decimal | None], q: Decimal, deadline: Decimal = Decimal('10000')) -> dict:
    """None is nonattainment, sorted after all attained values. Units ms."""
    if not q.is_finite() or not 0 < q <= 1:
        raise ValueError('quantile must be in (0,1]')
    if not values:
        return {'n': 0, 'rank': None, 'value_ms': None, 'label': 'undefined: empty denominator'}
    rank = int((q * len(values)).to_integral_value(rounding=ROUND_CEILING))
    ordered = sorted(values, key=lambda v: (v is None, v if v is not None else deadline))
    selected = ordered[rank - 1]
    return {'n': len(values), 'rank': rank, 'value_ms': str(selected) if selected is not None else None,
            'label': str(selected) + ' ms' if selected is not None else '>10 s / not attained'}


def _latency(row: dict, key: str, *, qualified: bool) -> Decimal | None:
    if qualified and (row.get('admission') != 'started' or row.get('outcome') != 'success' or row.get('spoken_correct') is not True
                      or row.get('task_correct') is not True):
        return None
    if row.get('timing_domain') != 'browser_performance_ms':
        return None
    if key == 'render_latency_ms' and (row.get('calibration_valid') is not True
                                     or row.get('timing_boundary') != 'browser_output_timestamp_estimate'):
        return None
    value = row.get(key)
    if value is None:
        return None
    latency = Decimal(str(value))
    if not latency.is_finite() or latency < 0 or (qualified and latency > 10000):
        return None
    return latency


def _distribution(values):
    return {name: quantile(values, q) for name, q in [('p50', Decimal('.50')), ('p95', Decimal('.95'))]}


def paired_analysis(rows: list[dict], seed: int, mode: str) -> dict:
    pairs = {}
    for row in rows:
        if row.get('arm') not in {'V', 'C'} or not row.get('pair_id'):
            continue
        key = (mode,) + tuple(str(row.get(k, 'unknown')) for k in ('network_profile', 'cohort', 'pair_id'))
        arm_rows = pairs.setdefault(key, {})
        if row['arm'] in arm_rows:
            raise ValueError('multiple rows per paired arm; select a prespecified turn position')
        arm_rows[row['arm']] = row
    cohorts = {}
    for key, pair in sorted(pairs.items()):
        left, right = pair.get('V'), pair.get('C')
        pipeline = tuple(str(pair.get(arm, {}).get('model_version', 'unknown')) for arm in ('V', 'C'))
        bucket = cohorts.setdefault(key[:3] + pipeline, {'pairs': [], 'family_values': {}})
        value_v = _latency(left, 'render_latency_ms', qualified=True) if left else None
        value_c = _latency(right, 'render_latency_ms', qualified=True) if right else None
        family = left.get('family') if left else None
        valid = value_v is not None and value_c is not None and family and right.get('family') == family
        difference = value_c - value_v if valid else None
        bucket['pairs'].append({'pair_id': key[3], 'difference_C_minus_V_ms': str(difference) if valid else None,
                                 'reason': None if valid else 'missing/nonattained/unmatched family or arm'})
        if valid:
            bucket['family_values'].setdefault(family, []).append(difference)
    output = []
    for key, bucket in sorted(cohorts.items()):
        families = sorted(bucket['family_values'])
        samples = []
        if len(families) >= 2:
            rng = random.Random(seed)
            for _ in range(1000):
                sampled = [value for family in rng.choices(families, k=len(families))
                           for value in bucket['family_values'][family]]
                samples.append(sum(sampled) / len(sampled))
            samples.sort()
        output.append({'cohort': list(key), 'pairs': bucket['pairs'], 'families': len(families),
                       'attained_pairs': sum(len(v) for v in bucket['family_values'].values()),
                       'exploratory_family_clustered_bootstrap_95_ms':
                       [str(samples[24]), str(samples[974])] if samples else None,
                       'bootstrap_repeats': 1000 if samples else 0, 'seed': seed,
                       'claim': 'descriptive pinned-pipeline difference; no causal architecture winner'})
    return {'cohorts': output, 'unpaired_rows': sum(not r.get('pair_id') for r in rows)}


def task_analysis(manifest: dict, rows: list[dict]) -> dict:
    scheduled = manifest.get('scheduled_tasks')
    if scheduled is None:
        return {'status': 'unknown', 'reason': 'scheduled task denominator not provided'}
    if not isinstance(scheduled, list) or len(scheduled) != len(set(scheduled)):
        raise ValueError('invalid scheduled_tasks')
    if any(row.get('task_id') and row['task_id'] not in scheduled for row in rows):
        raise ValueError('task row outside scheduled task denominator')
    results = []
    for task in sorted(scheduled):
        children = [r for r in rows if r.get('task_id') == task]
        terminals = {r['task_terminal'] for r in children if r.get('task_terminal')}
        terminal = next(iter(terminals)) if len(terminals) == 1 else 'unknown'
        results.append({'task_id': task, 'started': any(r.get('admission') == 'started' for r in children),
                        'outcome': terminal, 'reason': None if len(terminals) == 1 else 'missing or conflicting task terminal'})
    return {'scheduled': len(results), 'started': sum(r['started'] for r in results),
            'successful': sum(r['outcome'] == 'success' for r in results), 'rows': results}


def cost_analysis(ledger: dict, manifest: dict) -> dict:
    known = Decimal(ledger['known_cost'])
    exposure = Decimal(ledger['unknown_exposure'])
    if not known.is_finite() or not exposure.is_finite() or min(known, exposure) < 0:
        raise ValueError('invalid cost')
    complete = ledger.get('complete') is True and ledger.get('unknown_count') == 0 and exposure == 0
    rates = {}
    for metric in ('captured_input_seconds', 'rendered_output_seconds', 'elapsed_session_seconds'):
        seconds = manifest.get('audio', {}).get(metric)
        denominator = Decimal(str(seconds)) if seconds is not None else None
        if denominator is not None and (not denominator.is_finite() or denominator < 0):
            raise ValueError('invalid cost denominator')
        rates[metric] = {'denominator_seconds': str(denominator) if denominator is not None else None,
                         'known_cost_per_minute': str(known * 60 / denominator) if denominator else None,
                         'complete': complete, 'reason': None if denominator else 'undefined denominator'}
    return {'known_cost': str(known), 'unknown_count': ledger.get('unknown_count'),
            'unknown_exposure': str(exposure), 'complete': complete,
            'estimated_components': ledger.get('estimated_components', []), 'rates': rates,
            'affordability_winner': None}


def build_report(manifest: dict, rows: list[dict], ledger: dict) -> dict:
    manifest = RunManifest.model_validate(manifest).model_dump(mode='json')
    rows = [TurnResult.model_validate(row).model_dump(mode='json') for row in rows]
    if any(row['run_id'] != manifest['run_id'] for row in rows):
        raise ValueError('result row from different run')
    identities = [r.get('logical_turn_id') for r in rows]
    if any(not identity for identity in identities) or len(identities) != len(set(identities)):
        raise ValueError('one unique row for each scheduled logical turn is required')
    scheduled = manifest.get('scheduled_turns')
    if not isinstance(scheduled, list) or len(scheduled) != len(set(scheduled)):
        raise ValueError('manifest scheduled_turns required; denominator cannot be inferred from successes')
    by_id = {r['logical_turn_id']: r for r in rows}
    if set(by_id) - set(scheduled):
        raise ValueError('row not present in scheduled denominator')
    if any(attempt.get('logical_turn_id') not in scheduled for attempt in ledger.get('attempts', [])):
        raise ValueError('ledger contains attempt outside this run scheduled denominator')
    retained = [dict(by_id.get(turn, {'logical_turn_id': turn, 'admission': 'unknown',
                                    'outcome': 'missing_terminal', 'unknown_reason': 'missing source row',
                                    'missing_source_row': True})) for turn in sorted(scheduled)]
    groups = {}
    for row in retained:
        # Never pool mode/arm/network/model/fixture cohorts.
        key = (manifest['mode'],) + tuple(str(row.get(k, 'unknown')) for k in ('arm', 'network_profile', 'model_version', 'cohort'))
        groups.setdefault(key, []).append(row)
    cohorts = []
    for key, group in sorted(groups.items()):
        started = [r for r in group if r.get('admission') in {'started', 'missing_terminal'}]
        clean = [r for r in started if r.get('cohort') == 'clean']
        conditional = {}
        for metric in ('receipt_latency_ms', 'render_latency_ms'):
            known = [_latency(r, metric, qualified=False) for r in started]
            conditional[metric] = _distribution([v for v in known if v is not None])
            conditional[metric]['excluded_or_unknown'] = sum(v is None for v in known)
        outcomes = {}
        for row in started:
            outcome = row.get('outcome') or 'unknown'
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        cohorts.append({'identity': dict(zip(('mode', 'arm', 'network_profile', 'model_version', 'cohort'), key)),
            'scheduled': len(group), 'started': len(started),
            'refused': sum(r.get('admission') == 'refused' for r in group),
            'admission_unknown': sum(r.get('admission') not in {'started', 'missing_terminal', 'refused'} for r in group),
            'terminal_counts': outcomes, 'conditional_latency': conditional,
            'all_started_clean_acceptable_render': _distribution([_latency(r, 'render_latency_ms', qualified=True) for r in clean]),
            'task_results': 'reported separately; turn success is not task success'})
    tasks = task_analysis(manifest, retained)
    costs = cost_analysis(ledger, manifest)
    successes = tasks.get('successful', 0)
    costs['known_cost_per_successful_task'] = str(Decimal(costs['known_cost']) / successes) if successes else None
    return {'schema_version': 1, 'run_id': manifest['run_id'], 'created_at': manifest['created_at'],
            'evidence_status': 'descriptive input report; independent acceptance not implied',
            'scheduled_turn_count': len(scheduled), 'rows': retained, 'cohorts': cohorts,
            'ledger': ledger, 'costs': costs, 'tasks': tasks,
            'paired': paired_analysis(retained, manifest.get('bootstrap_seed', 0), manifest['mode']), 'winner': None,
            'limitations': ['No architecture causal claim', 'Unknown spoken correctness is nonattainment',
                            'Calibration/audio custody and independent acceptance require separate evidence']}


def write_report(manifest_path: str | Path, rows_path: str | Path, ledger_path: str | Path,
                 out: str | Path) -> list[Path]:
    paths = [Path(p) for p in (manifest_path, rows_path, ledger_path)]
    raw = [p.read_bytes() for p in paths]
    manifest = json.loads(raw[0])
    rows = [json.loads(line) for line in raw[1].decode('utf-8').splitlines() if line.strip()]
    ledger = json.loads(raw[2])
    report = build_report(manifest, rows, ledger)
    report['input_sha256'] = {name: hashlib.sha256(data).hexdigest()
                              for name, data in zip(('manifest', 'rows', 'ledger'), raw)}
    destination = Path(out)
    destination.mkdir(parents=True, exist_ok=True)
    structured = destination / 'results.json'
    structured.write_text(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    table = ['# Realtime Voice Lab descriptive report', '', f"Run: {report['run_id']}", '',
             'All rows and unknowns retained. No unconditional winner.', '',
             '| Cohort | Started | Refused | All-started acceptable render p50 | p95 |',
             '|---|---:|---:|---|---|']
    for cohort in report['cohorts']:
        table.append('| ' + ' / '.join(cohort['identity'].values()) + f" | {cohort['started']} | {cohort['refused']} | "
            + cohort['all_started_clean_acceptable_render']['p50']['label'] + ' | '
            + cohort['all_started_clean_acceptable_render']['p95']['label'] + ' |')
    summary = destination / 'results.md'
    summary.write_text('\n'.join(table) + '\n', encoding='utf-8')
    raw_rows = destination / 'results.jsonl'
    raw_rows.write_text(''.join(json.dumps(r, sort_keys=True, allow_nan=False) + '\n' for r in report['rows']), encoding='utf-8')
    cost = destination / 'ledger-summary.md'
    cost.write_text('# All-attempt cost\n\nKnown cost: ' + report['costs']['known_cost']
                    + '\nUnknown exposure: ' + report['costs']['unknown_exposure']
                    + '\nComplete: ' + str(report['costs']['complete']).lower() + '\n', encoding='utf-8')
    return [structured, summary, raw_rows, cost]
