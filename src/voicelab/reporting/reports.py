"""Deterministic, denominator-preserving descriptive reports; no fabricated data."""
from __future__ import annotations

import hashlib
import json
import random
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from voicelab.contracts import RunManifest, TurnResult
from .timing import stage_rows


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


def _latency(row: dict, key: str, *, qualified: bool, raw: bool = False) -> Decimal | None:
    if qualified and (row.get('admission') != 'started' or row.get('outcome') != 'success' or row.get('spoken_correct') is not True
                      or row.get('task_correct') is not True):
        return None
    if row.get('timing_domain') != 'browser_performance_ms':
        return None
    if key == 'render_latency_ms' and not raw and (row.get('calibration_valid') is not True
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


def _withheld_distribution(values, reasons):
    """Keep the known/potential-start count, never publish a reduced headline."""
    return {name: {'n': len(values), 'rank': None, 'value_ms': None,
                   'label': 'withheld: cohort membership or admission evidence incomplete',
                   'complete': False, 'reasons': sorted(set(reasons))}
            for name in ('p50', 'p95')}


def _retain_scheduled(manifest, rows, source_fields, ledger):
    scheduled = set(manifest['scheduled_turns'])
    assignments = manifest['turn_assignments']
    assignment_ids = [row['logical_turn_id'] for row in assignments]
    if len(assignment_ids) != len(set(assignment_ids)) or set(assignment_ids) - scheduled:
        raise ValueError('duplicate or unscheduled turn assignment')
    by_assignment = {row['logical_turn_id']: row for row in assignments}
    by_row = {row['logical_turn_id']: row for row in rows}
    by_attempt = {}
    for attempt in ledger.get('attempts', []):
        by_attempt.setdefault(attempt['logical_turn_id'], []).append(attempt)
    global_reasons = []
    if scheduled - set(assignment_ids):
        global_reasons.append('prespecified cohort/task assignments missing for scheduled turns')
    retained = []
    metadata = ('trial_id', 'arm', 'cohort', 'network_profile', 'model_version', 'task_id', 'family', 'pair_id')
    for turn in sorted(scheduled):
        assignment = by_assignment.get(turn)
        source = by_row.get(turn)
        attempts = by_attempt.get(turn, [])
        row = dict(source) if source else {'run_id': manifest['run_id'], 'logical_turn_id': turn,
            'outcome': 'missing_terminal', 'task_correct': None, 'spoken_correct': None,
            'unknown_reason': 'missing source row', 'missing_source_row': True}
        reasons = []
        if assignment:
            for field in metadata:
                if (source and field in source_fields[turn] and source.get(field) is not None
                        and source[field] != assignment[field]):
                    raise ValueError('terminal metadata conflicts with prespecified turn assignment: ' + field)
                row[field] = assignment[field]
            if any(attempt.get('arm') != assignment['arm'] for attempt in attempts):
                raise ValueError('ledger arm conflicts with prespecified assignment')
        else:
            reasons.append('prespecified turn assignment missing')
        independent = assignment['admission'] if assignment and assignment['admission_evidence_reference'] else 'unknown'
        terminal_admission = source.get('admission', 'unknown') if source else 'unknown'
        terminal_started = terminal_admission in {'started', 'missing_terminal'}
        ledger_started = any(attempt.get('started') is True or attempt.get('dispatched') is True
                             or attempt.get('state') in {'settled', 'unknown'} for attempt in attempts)
        possible_start = bool(attempts)
        if independent == 'started' or terminal_started or possible_start:
            row['admission'] = 'started'
            if independent in {'refused', 'not_started'} or terminal_admission == 'refused':
                reasons.append('conflicting start/refusal evidence')
            if possible_start and not (independent == 'started' or terminal_started or ledger_started):
                reasons.append('reservation exists but dispatch/start remains ambiguous')
            admission_source = 'independent_record' if independent == 'started' else (
                'terminal_row' if terminal_started else 'ledger_attempt')
        elif independent in {'refused', 'not_started'}:
            row['admission'] = independent
            admission_source = 'independent_record'
        elif terminal_admission == 'refused':
            row['admission'] = 'refused'
            admission_source = 'terminal_row'
        else:
            row['admission'] = 'unknown'
            admission_source = 'unknown'
            reasons.append('start/admission not established independently of missing terminal')
        row['admission_source'] = admission_source
        row['admission_evidence_reference'] = assignment['admission_evidence_reference'] if assignment else None
        row['denominator_uncertainty'] = reasons
        retained.append(row)
    return retained, global_reasons


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
        bucket = cohorts.setdefault(key[:3] + pipeline, {'pairs': [], 'family_values': {}, 'scheduled_families': set()})
        value_v = _latency(left, 'render_latency_ms', qualified=True) if left else None
        value_c = _latency(right, 'render_latency_ms', qualified=True) if right else None
        family = left.get('family') if left else None
        if family:
            bucket['scheduled_families'].add(family)
        denominator_complete = all(not item.get('denominator_uncertainty') for item in pair.values())
        valid = (value_v is not None and value_c is not None and family and right.get('family') == family
                 and denominator_complete)
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
                       'scheduled_families': len(bucket['scheduled_families']),
                       'scheduled_pairs': len(bucket['pairs']),
                       'unattained_or_unmatched_pairs': sum(p['difference_C_minus_V_ms'] is None for p in bucket['pairs']),
                       'attained_pairs': sum(len(v) for v in bucket['family_values'].values()),
                       'exploratory_family_clustered_bootstrap_95_ms':
                       [str(samples[24]), str(samples[974])] if samples else None,
                       'bootstrap_repeats': 1000 if samples else 0, 'seed': seed,
                       'estimand': 'mean C-minus-V render difference conditional on both qualified responses attained',
                       'interval_method': 'percentile family-cluster resampling, 1000 replicates; exploratory',
                       'missing_pairs_are_not_zero': True,
                       'claim': 'conditional descriptive pinned-pipeline difference; no causal architecture winner'})
    return {'cohorts': output, 'unpaired_rows': sum(not r.get('pair_id') for r in rows)}


def task_analysis(manifest: dict, rows: list[dict], task_admissions: list[dict]) -> dict:
    scheduled = manifest.get('scheduled_tasks')
    if scheduled is None:
        return {'status': 'unknown', 'reason': 'scheduled task denominator not provided'}
    if not isinstance(scheduled, list) or len(scheduled) != len(set(scheduled)):
        raise ValueError('invalid scheduled_tasks')
    if any(row.get('task_id') and row['task_id'] not in scheduled for row in rows):
        raise ValueError('task row outside scheduled task denominator')
    results = []
    admission_by_task = {item['task_id']: item for item in task_admissions}
    if len(admission_by_task) != len(task_admissions) or set(admission_by_task) - set(scheduled):
        raise ValueError('duplicate/unscheduled task admission evidence')
    for task in sorted(scheduled):
        children = [r for r in rows if r.get('task_id') == task]
        terminals = {r['task_terminal'] for r in children if r.get('task_terminal')}
        terminal = next(iter(terminals)) if len(terminals) == 1 else 'unknown'
        terminal_rows = [r for r in children if r.get('task_terminal') == terminal]
        if terminal == 'success' and not any(r.get('task_correct') is True and r.get('spoken_correct') is True for r in terminal_rows):
            terminal = 'unknown'
        memberships = {(r.get('arm'), r.get('cohort'), r.get('network_profile'), r.get('model_version')) for r in children}
        membership = next(iter(memberships)) if len(memberships) == 1 else ('unknown',) * 4
        admission_record = admission_by_task.get(task)
        admission = admission_record.get('admission') if admission_record else 'unknown'
        if admission not in {'admitted', 'refused', 'unknown'}:
            raise ValueError('invalid task admission evidence')
        started = any(r.get('admission') == 'started' for r in children)
        conflict = admission == 'refused' and started
        results.append({'task_id': task, 'started': started,
                        'admission': admission, 'admission_evidence': admission_record,
                        'admitted': admission == 'admitted' or started,
                        'admission_unknown': admission == 'unknown' and not started,
                        'admission_conflict': conflict,
                        'refused': not started and (admission == 'refused' or bool(children) and all(r.get('admission') == 'refused' for r in children)),
                        'identity': dict(zip(('arm', 'cohort', 'network_profile', 'model_version'), membership)),
                        'outcome': terminal, 'reason': None if terminal != 'unknown' else 'missing/conflicting/unqualified task terminal'})
    cohorts = {}
    for row in results:
        key = tuple(str(row['identity'][k]) for k in ('arm', 'cohort', 'network_profile', 'model_version'))
        bucket = cohorts.setdefault(key, {'identity': row['identity'], 'scheduled': 0, 'started': 0, 'successful': 0,
                                          'admitted': 0, 'admission_unknown': 0, 'refused': 0, 'unknown': 0})
        bucket['scheduled'] += 1
        bucket['started'] += int(row['started'])
        bucket['admitted'] += int(row['admitted'])
        bucket['admission_unknown'] += int(row['admission_unknown'])
        bucket['successful'] += int(row['outcome'] == 'success')
        bucket['refused'] += int(row['refused'])
        bucket['unknown'] += int(row['outcome'] == 'unknown')
    return {'scheduled': len(results), 'started': sum(r['started'] for r in results),
            'admitted': sum(r['admitted'] for r in results),
            'admission_unknown': sum(r['admission_unknown'] for r in results),
            'successful': sum(r['outcome'] == 'success' for r in results), 'rows': results,
            'cohorts': [cohorts[key] for key in sorted(cohorts)]}


def cost_analysis(ledger: dict, manifest: dict) -> dict:
    known = Decimal(ledger['known_cost'])
    exposure = Decimal(ledger['unknown_exposure'])
    if not known.is_finite() or not exposure.is_finite() or min(known, exposure) < 0:
        raise ValueError('invalid cost')
    if type(ledger.get('unknown_count')) is not int or ledger['unknown_count'] < 0:
        raise ValueError('explicit nonnegative unresolved attempt count required')
    complete = ledger.get('complete') is True and ledger.get('unknown_count') == 0 and exposure == 0
    attempts = ledger.get('attempts', [])
    if len({a['attempt_id'] for a in attempts}) != len(attempts):
        raise ValueError('duplicate attempt identities in report ledger')
    components = {}
    derived_known, derived_exposure, unresolved = Decimal(0), Decimal(0), 0
    for attempt in attempts:
        key = (str(attempt['arm']), str(attempt['component']), str(attempt['price_version']), str(attempt.get('currency', ledger.get('currency', 'USD'))))
        if key[-1] != 'USD':
            raise ValueError('mixed/unsupported currency; no implicit exchange rate')
        bucket = components.setdefault(key, {'attempts': 0, 'known': Decimal(0), 'exposure': Decimal(0), 'unknown': 0})
        bucket['attempts'] += 1
        if attempt['state'] == 'settled':
            amount = Decimal(str(attempt['settled']))
            derived_known += amount
            bucket['known'] += amount
        elif attempt['state'] in {'reserved', 'unknown'}:
            amount = Decimal(str(attempt['reservation']))
            derived_exposure += amount
            unresolved += 1
            bucket['exposure'] += amount
            bucket['unknown'] += 1
        else:
            raise ValueError('unknown ledger attempt state')
        if not amount.is_finite() or amount < 0:
            raise ValueError('invalid attempt amount')
    if 'attempts' not in ledger or known != derived_known or exposure != derived_exposure or ledger.get('unknown_count') != unresolved:
        raise ValueError('cost summary disagrees with immutable attempts')
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
            'attempt_count': len(attempts),
            'components': [{'arm': key[0], 'component': key[1], 'price_version': key[2], 'currency': key[3],
                            'attempt_count': value['attempts'], 'known_cost': str(value['known']),
                            'unknown_exposure': str(value['exposure']), 'unknown_count': value['unknown']}
                           for key, value in sorted(components.items())],
            'affordability_winner': None}


def _stage_analysis(rows):
    raw = [stage for row in rows for stage in stage_rows(row)]
    groups = {}
    for item in raw:
        key = (item['name'], str(item['clock_domain']), str(item['observation']))
        groups.setdefault(key, []).append(item)
    summary = []
    for key, items in sorted(groups.items()):
        values = [Decimal(item['value_ms']) for item in items if item['value_ms'] is not None]
        summary.append({'name': key[0], 'clock_domain': key[1], 'observation': key[2],
                        'conditional_ms': _distribution(values), 'unknown_count': len(items) - len(values),
                        'total_spans': len(items)})
    missing = []
    for arm, names in (('C', ('stt', 'controller', 'tts')), ('V', ('native_internal_stt', 'native_internal_reasoning', 'native_internal_tts'))):
        selected = [row for row in rows if row.get('arm') == arm]
        for name in names:
            count = sum(not any(stage.get('name') == name for stage in row.get('stages', [])) for row in selected)
            if count:
                missing.append({'name': name, 'turns_missing': count, 'observation': 'unknown',
                                'reason': 'stage anchors not exposed/supplied; no duration inferred'})
    return {'rows': raw, 'summary': summary, 'unobserved_expected_stages': missing,
            'limitation': 'Within-span clock arithmetic only; no cross-clock stage sum or percentile subtraction',
            'native_internal_stages': 'unknown unless explicitly exposed with valid anchors; no fabricated spans',
            'english_translation': 'identity no-op; no duration'}


def _failures(rows, tasks, costs, cohorts):
    failures = []
    for row in rows:
        reasons = list(row.get('denominator_uncertainty', []))
        if row.get('admission') == 'started' and row.get('outcome') != 'success':
            reasons.append('started turn terminal: ' + str(row.get('outcome', 'unknown')))
        if row.get('admission') == 'refused':
            reasons.append('admission refused; excluded from started response denominator')
        if row.get('unknown_reason') and row.get('admission') != 'not_started':
            reasons.append(row['unknown_reason'])
        if row.get('admission') == 'started' and (row.get('spoken_correct') is not True or row.get('task_correct') is not True):
            reasons.append('acceptable spoken/task correctness not established')
        for stage in stage_rows(row):
            if stage['reason'] and 'no-op' not in stage['reason']:
                reasons.append(stage['name'] + ': ' + stage['reason'])
        for reason in sorted(set(reasons)):
            failures.append({'scope': 'turn', 'id': row['logical_turn_id'], 'reason': reason})
    for task in tasks.get('rows', []):
        if task.get('admission_conflict'):
            failures.append({'scope': 'task', 'id': task['task_id'], 'reason': 'task refusal conflicts with observed logical start'})
        if task['outcome'] != 'success':
            failures.append({'scope': 'task', 'id': task['task_id'], 'reason': task['reason'] or task['outcome']})
    if not costs['complete']:
        failures.append({'scope': 'cost', 'id': 'all-attempts', 'reason': 'unknown liability; complete cost/affordability claim withheld'})
    for cohort in cohorts:
        if not cohort['all_started_denominator_complete']:
            failures.append({'scope': 'cohort', 'id': ' / '.join(cohort['identity'].values()),
                             'reason': 'all-started quantiles withheld: incomplete cohort/start evidence'})
    return sorted(failures, key=lambda item: (item['scope'], item['id'], item['reason']))


def build_report(manifest: dict, rows: list[dict], ledger: dict, *, task_admissions: list[dict] | None = None) -> dict:
    manifest = RunManifest.model_validate(manifest).model_dump(mode='json')
    source_fields = {row['logical_turn_id']: set(row) for row in rows}
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
    retained, global_reasons = _retain_scheduled(manifest, rows, source_fields, ledger)
    groups = {}
    for row in retained:
        # Never pool mode/arm/network/model/fixture cohorts.
        key = (manifest['mode'],) + tuple(str(row.get(k, 'unknown')) for k in ('arm', 'network_profile', 'model_version', 'cohort'))
        groups.setdefault(key, []).append(row)
    cohorts = []
    for key, group in sorted(groups.items()):
        started = [r for r in group if r.get('admission') in {'started', 'missing_terminal'}]
        clean = [r for r in started if r.get('cohort') == 'clean']
        conditional, raw_latency, qualified_conditional = {}, {}, {}
        for metric in ('receipt_latency_ms', 'render_latency_ms'):
            known = [_latency(r, metric, qualified=False) for r in started]
            conditional[metric] = _distribution([v for v in known if v is not None])
            conditional[metric]['excluded_or_unknown'] = sum(v is None for v in known)
            raw_values = [_latency(r, metric, qualified=False, raw=True) for r in started]
            qualified_values = [_latency(r, metric, qualified=True) for r in started]
            raw_latency[metric] = {**_distribution([v for v in raw_values if v is not None]),
                                   'excluded_or_unknown': sum(v is None for v in raw_values),
                                   'qualification': 'raw observed onset, including wrong/unadjudicated responses; not acceptable-response latency'}
            qualified_conditional[metric] = {**_distribution([v for v in qualified_values if v is not None]),
                                             'excluded_or_unknown': sum(v is None for v in qualified_values)}
        outcomes = {}
        for row in started:
            outcome = row.get('outcome') or 'unknown'
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        denominator_reasons = global_reasons + [reason for row in group for reason in row['denominator_uncertainty']]
        clean_values = [_latency(r, 'render_latency_ms', qualified=True) for r in clean]
        headline = (_withheld_distribution(clean_values, denominator_reasons) if denominator_reasons
                    else _distribution(clean_values))
        receipt_values = [_latency(r, 'receipt_latency_ms', qualified=True) for r in clean]
        receipt_headline = (_withheld_distribution(receipt_values, denominator_reasons) if denominator_reasons
                            else _distribution(receipt_values))
        cohorts.append({'identity': dict(zip(('mode', 'arm', 'network_profile', 'model_version', 'cohort'), key)),
            'scheduled': len(group), 'started': len(started),
            'refused': sum(r.get('admission') == 'refused' for r in group),
            'not_started': sum(r.get('admission') == 'not_started' for r in group),
            'admission_unknown': sum(r.get('admission') not in {'started', 'missing_terminal', 'refused', 'not_started'} for r in group),
            'terminal_counts': outcomes, 'conditional_latency': conditional,
            'recovery': {'started_denominator': len(started),
                         'logical_turns_with_retry': sum(r.get('retry_count', 0) > 0 for r in started),
                         'reported_retries': sum(r.get('retry_count', 0) for r in started),
                         'reported_duplicate_attempts': sum(r.get('duplicate_count', 0) for r in started),
                         'expected_interruption_terminals': outcomes.get('expected_interruption', 0),
                         'unknown_recovery_terminals': outcomes.get('recovery_unknown', 0),
                         'invariants': 'duplicate-attempt counts do not prove unique effects; stale-frame/cleanup invariants require separate observations'},
            'raw_latency': raw_latency, 'qualified_conditional_latency': qualified_conditional,
            'stages': _stage_analysis(started),
            'all_started_denominator_complete': not denominator_reasons,
            'denominator_incompleteness_reasons': sorted(set(denominator_reasons)),
            'all_started_clean_acceptable_render': headline,
            'all_started_clean_acceptable_receipt': receipt_headline,
            'task_results': 'reported separately; turn success is not task success'})
    tasks = task_analysis(manifest, retained, task_admissions or [])
    costs = cost_analysis(ledger, manifest)
    successes = tasks.get('successful', 0)
    costs['known_cost_per_successful_task'] = str(Decimal(costs['known_cost']) / successes) if successes else None
    return {'schema_version': 1, 'run_id': manifest['run_id'], 'created_at': manifest['created_at'],
            'evidence_status': 'descriptive input report; independent acceptance not implied',
            'scheduled_turn_count': len(scheduled), 'rows': retained, 'cohorts': cohorts,
            'all_started_denominator_complete': not global_reasons and all(not row['denominator_uncertainty'] for row in retained),
            'denominator_incompleteness_reasons': sorted(set(global_reasons + [
                reason for row in retained for reason in row['denominator_uncertainty']])),
            'ledger': ledger, 'costs': costs, 'tasks': tasks,
            'failed_items': _failures(retained, tasks, costs, cohorts),
            'paired': paired_analysis(retained, manifest.get('bootstrap_seed', 0), manifest['mode']), 'winner': None,
            'limitations': ['No architecture causal claim', 'Unknown spoken correctness is nonattainment',
                            'Calibration/audio custody and independent acceptance require separate evidence']}


def write_report(manifest_path: str | Path, rows_path: str | Path, ledger_path: str | Path,
                 out: str | Path, *, evidence_path: str | Path | None = None,
                 source_hashes_path: str | Path | None = None,
                 driver_correlations_path: str | Path | None = None) -> list[Path]:
    paths = [Path(p) for p in (manifest_path, rows_path, ledger_path)]
    raw = [p.read_bytes() for p in paths]
    manifest = json.loads(raw[0])
    rows = [json.loads(line) for line in raw[1].decode('utf-8').splitlines() if line.strip()]
    ledger = json.loads(raw[2])
    driver_raw = Path(driver_correlations_path).read_bytes() if driver_correlations_path else None
    driver_data = json.loads(driver_raw) if driver_raw is not None else {}
    report = build_report(manifest, rows, ledger, task_admissions=driver_data.get('task_admissions', []))
    report['input_sha256'] = {name: hashlib.sha256(data).hexdigest()
                              for name, data in zip(('manifest', 'rows', 'ledger'), raw)}
    if driver_raw is not None:
        report['input_sha256']['driver_correlations'] = hashlib.sha256(driver_raw).hexdigest()
        report['driver_correlations'] = driver_data
        observed_terminals = []
        for turn, records in sorted(driver_data.get('transport_attempts', {}).items()):
            for record in records:
                if record['kind'] == 'turn_ended':
                    observed_terminals.append({'logical_turn_id': turn, **record})
                    if record.get('terminal_outcome') != 'success':
                        report['failed_items'].append({'scope': 'transport_attempt',
                            'id': turn + ' / ' + str(record.get('observed_transport_turn_id')),
                            'reason': str(record.get('terminal_outcome', 'unknown')) + '; retained even if later recovery succeeds'})
        report['transport_attempt_terminals'] = observed_terminals
        report['failed_items'].sort(key=lambda item: (item['scope'], item['id'], item['reason']))
    if evidence_path is not None:
        evidence_raw = Path(evidence_path).read_bytes()
        from voicelab.contracts import EvidenceRecord
        evidence = [json.loads(line) for line in evidence_raw.decode('utf-8').splitlines() if line.strip()]
        by_turn = {row['logical_turn_id']: row for row in rows}
        seen = set()
        for item in evidence:
            turn = item['logical_turn_id']
            record = EvidenceRecord.model_validate(item['record']).model_dump(mode='json')
            if turn in seen or turn not in by_turn or record['run_id'] != report['run_id']:
                raise ValueError('duplicate/unscheduled/cross-run evidence record')
            seen.add(turn)
            original = by_turn[turn]
            encoded = (json.dumps(original, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
            if record['result_hash'] != hashlib.sha256(encoded).hexdigest() or record['trial_id'] != original['trial_id']:
                raise ValueError('evidence result binding mismatch')
        report['evidence_records'] = evidence
        report['evidence_record_coverage_complete'] = seen == set(manifest['scheduled_turns'])
        report['input_sha256']['evidence'] = hashlib.sha256(evidence_raw).hexdigest()
    else:
        report['evidence_record_coverage_complete'] = False
    if source_hashes_path is not None:
        source_raw = Path(source_hashes_path).read_bytes()
        source_hashes = json.loads(source_raw)
        if not isinstance(source_hashes, dict) or any(not isinstance(value, str) or len(value) != 64
                or any(c not in '0123456789abcdef' for c in value) for value in source_hashes.values()):
            raise ValueError('invalid original source hashes')
        report['original_source_sha256'] = source_hashes
        report['input_sha256']['source_hashes'] = hashlib.sha256(source_raw).hexdigest()
        for item in report.get('evidence_records', []):
            for key in ('trace_hash', 'audio_hash', 'ledger_hash', 'oracle_hash'):
                value = item['record'].get(key)
                if value is not None and value not in source_hashes.values():
                    raise ValueError('evidence record hash absent from original source references: ' + key)
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
                    + '\nUnknown count: ' + str(report['costs']['unknown_count'])
                    + '\nUnknown exposure: ' + report['costs']['unknown_exposure']
                    + '\nComplete: ' + str(report['costs']['complete']).lower()
                    + '\n\nSeparate denominators/rates and component attempts:\n\n```json\n'
                    + json.dumps(report['costs'], sort_keys=True, indent=2, allow_nan=False) + '\n```\n', encoding='utf-8')
    failures = destination / 'failures.json'
    failures.write_text(json.dumps({'input_sha256': report['input_sha256'], 'failed_items': report['failed_items']},
                                   sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    numeric = {'run_id': report['run_id'], 'created_at': report['created_at'], 'input_sha256': report['input_sha256'],
               'evidence_status': report['evidence_status'], 'cohorts': report['cohorts'], 'tasks': report['tasks'],
               'costs': report['costs'], 'paired': report['paired'], 'winner': None}
    fragments = destination / 'numeric-fragments.json'
    fragments.write_text(json.dumps(numeric, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    readme = destination / 'README-results.md'
    readme.write_text('\n'.join(table) + '\n\nInput hashes:\n\n```json\n'
                      + json.dumps(report['input_sha256'], sort_keys=True, indent=2) + '\n```\n'
                      + '\nDescriptive saved-evidence analysis; not independent product acceptance.\n', encoding='utf-8')
    limits = destination / 'LIMITATIONS-results.md'
    limits.write_text('# Generated failed/unknown items\n\n'
                      + ('\n'.join('- ' + item['scope'] + ' / ' + item['id'] + ': ' + item['reason'].replace('\n', ' ')
                                   for item in report['failed_items']) if report['failed_items'] else
                         'No failed items in the supplied analysis; this does not establish independent acceptance.')
                      + '\n\nExact inputs: `' + json.dumps(report['input_sha256'], sort_keys=True) + '`\n', encoding='utf-8')
    return [structured, summary, raw_rows, cost, failures, fragments, readme, limits]
