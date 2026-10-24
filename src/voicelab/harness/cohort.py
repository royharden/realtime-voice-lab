"""Frozen ordered cohort orchestration source; no verifier, provider or default tariff."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re

from .activation import load_activation, bind_task_fixture
from .browser import BrowserDriver, own_diagnostic, server_evidence, collect_relay_trace, collect_ledger_snapshot
from .calibration import calibrate_page
from .cohort_admission import AdmissionGrant, AdmissionRefused, prepare_cohort, admit_live_arm, utc
from .evidence import EvidenceSink
from .faults import MockControls
from .journey import wait_state, perform_fault
from .observations import app_snapshot, wait_cleanup, cleanup_count, DiagnosticCursor


class LiveLease:
    """Local exclusivity only; never authorization, budget availability or rights proof."""
    def __init__(self, spec: dict, scope: dict):
        from voicelab.security import safe_artifact_path
        from voicelab.evaluation.artifacts import canonical_bytes, digest
        owner = spec.get('live_budget_owner', {})
        if not owner.get('reference') or not owner.get('root'):
            raise AdmissionRefused('serialized_live_budget_owner_missing')
        if owner['reference'] != spec['live_config']['manifests']['authorization'].get('owner_lease_reference'):
            raise AdmissionRefused('local_owner_reference_differs_from_D4_authorized_scope')
        root = Path(owner['root']).resolve(strict=True)
        name = 'cohort-owner-' + hashlib.sha256(owner['reference'].encode()).hexdigest() + '.lock'
        self.path = safe_artifact_path(root, name)
        self.raw = canonical_bytes({'scope_hash': digest(scope), 'pid': os.getpid(),
                                    'owner_reference_hash': hashlib.sha256(owner['reference'].encode()).hexdigest()})
        self.created = False
        self.release_allowed = False

    def __enter__(self):
        try:
            with self.path.open('xb') as handle:
                handle.write(self.raw)
                handle.flush()
                os.fsync(handle.fileno())
            self.created = True
        except FileExistsError as exc:
            raise AdmissionRefused('live_budget_audio_owner_already_held') from exc
        return self

    def __exit__(self, *args):
        if self.created and self.release_allowed:
            if self.path.is_symlink() or self.path.read_bytes() != self.raw:
                raise AdmissionRefused('live_owner_release_identity_changed')
            self.path.unlink()  # only this invocation's exclusively-created lock


class Progress:
    def __init__(self, plan: dict, sink):
        self.plan, self.sink = plan, sink
        self.rows = {}
        self.attempts: set[str] = set()
        self.attributed_attempts: set[str] = set()
        self.unresolved_attempts: set[str] = set()
        self.obligations: list[dict] = []
        for task in plan['tasks']:
            self.rows[task['task_id']] = {'admission': 'scheduled', 'started': set(), 'terminal': set(),
                                         'task_terminal': False, 'code': None}
            sink.emit('task_scheduled', **{k: task[k] for k in ('task_id', 'trial_id', 'arm', 'cohort',
                'family', 'speaker', 'pair_id', 'fixture_id', 'logical_turn_ids', 'fault_schedule',
                'network_profile', 'model_version', 'repeat', 'control')})

    def refuse(self, task: dict, code: str):
        row = self.rows[task['task_id']]
        if row['admission'] == 'scheduled':
            row.update(admission='not_started', code=code)
            self.sink.emit('task_admission', task_id=task['task_id'], arm=task['arm'],
                           admission='not_started', code=code, source='driver_admission')
        for logical in task['logical_turn_ids']:
            if logical not in row['started']:
                self.sink.emit('turn_not_started', task_id=task['task_id'], logical_turn_id=logical,
                               arm=task['arm'], admission='not_started', code=code)

    def obligation(self, task: dict | None, code: str):
        item = {'task_id': task['task_id'] if task else None, 'code': code, 'status': 'unresolved'}
        self.obligations.append(item)
        self.sink.emit('cohort_obligation', observation='unknown', **item)

    def audit(self, task: dict, trace: list[dict], evidence: dict):
        row = self.rows[task['task_id']]
        observed_turns = set()
        if task.get('_identity_conflict'):
            self.obligation(task, 'conflicting_observed_transport_plan_mapping')
        for event in trace:
            if event['kind'] not in {'turn_started', 'turn_ended'}:
                continue
            if (event.get('session_id') != task.get('_session_id')
                    or event.get('run_id') != task.get('_server_run_id')
                    or event.get('trial_id') != task.get('_trial_namespace')
                    or event.get('boot_id') != task.get('_boot_id')):
                self.obligation(task, 'saved_trace_identity_differs_from_observed_ready_session')
                continue
            planned = task.get('_wire_turns', {}).get(event.get('transport_turn_id'))
            if planned not in task['logical_turn_ids']:
                self.obligation(task, 'actual_server_turn_outside_evidenced_plan_mapping')
                continue
            self.sink.emit('plan_turn_mapping', task_id=task['task_id'], logical_turn_id=planned,
                session_id=event['session_id'], observed_boot_id=event.get('boot_id'),
                observed_run_id=event['run_id'], observed_trial_id=event['trial_id'],
                observed_logical_turn_id=event['logical_turn_id'],
                observed_transport_turn_id=event['transport_turn_id'])
            observed_turns.add((event['session_id'], event['logical_turn_id'],
                event['transport_turn_id'], event['trial_id'], event.get('connection_epoch'),
                event.get('response_epoch'), event.get('boot_id')))
            if event['kind'] == 'turn_started':
                row['started'].add(planned)
            elif event.get('terminal_outcome') is not None:
                row['terminal'].add(planned)
        row['task_terminal'] = evidence['session'].get('terminal_outcome') is not None
        raw_attempts = (evidence.get('ledger') or {}).get('attempts', [])
        session_ids = {a['attempt_id'] for a in evidence.get('session_attempts', [])}
        for attempt in raw_attempts:
            self.attempts.add(attempt['attempt_id'])
            # A saved ledger is run-wide. Trial/transport strings alone are not a join.
            lineage = (attempt.get('session_id'), attempt.get('logical_turn_id'),
                       attempt.get('transport_turn_id'))
            exact = (*lineage, attempt.get('trial_id'), attempt.get('connection_epoch'),
                     attempt.get('response_epoch'), attempt.get('owner_id'))
            if (attempt.get('lineage_observation') == 'recorded_at_reservation'
                    and attempt['attempt_id'] in session_ids and exact in observed_turns):
                self.attributed_attempts.add(attempt['attempt_id'])
                self.unresolved_attempts.discard(attempt['attempt_id'])
                self.sink.emit('attempt_lineage_observed', task_id=task['task_id'],
                    attempt_id=attempt['attempt_id'], session_id=lineage[0],
                    observed_logical_turn_id=lineage[1], observed_transport_turn_id=lineage[2],
                    observed_trial_id=attempt['trial_id'], connection_epoch=attempt['connection_epoch'],
                    response_epoch=attempt['response_epoch'], observed_boot_id=attempt['owner_id'])
            elif (attempt.get('lineage_observation') != 'recorded_at_reservation'
                    or attempt.get('session_id') == task.get('_session_id')) and attempt['attempt_id'] not in self.attributed_attempts:
                self.unresolved_attempts.add(attempt['attempt_id'])

    def finish(self) -> dict:
        if self.unresolved_attempts:
            self.obligation(None, 'run_snapshot_attempt_lineage_unresolved')
        for task in self.plan['tasks']:
            row = self.rows[task['task_id']]
            if row['admission'] == 'scheduled':
                self.refuse(task, 'cohort_stopped_before_admission')
            if row['admission'] == 'admitted' and (not row['task_terminal'] or row['started'] - row['terminal']):
                self.obligation(task, 'admitted_task_or_started_turn_terminal_missing')
            missing = set(task['logical_turn_ids']) - row['started']
            if missing and row['admission'] == 'admitted':
                self.obligation(task, 'admitted_task_scheduled_turns_unstarted')
                for logical in sorted(missing):
                    self.sink.emit('turn_not_started', observation='unknown', task_id=task['task_id'],
                                   logical_turn_id=logical, code='cohort_aborted_or_task_failed')
        counts = []
        for arm in ('V', 'C', 'K'):
            for cohort in ('clean', 'fault'):
                selected = [t for t in self.plan['tasks'] if t['arm'] == arm and t['cohort'] == cohort]
                rows = [self.rows[t['task_id']] for t in selected]
                counts.append({'arm': arm, 'cohort': cohort, 'planned_tasks': len(rows),
                    'planned_turns': sum(len(t['logical_turn_ids']) for t in selected),
                    'admitted_tasks': sum(r['admission'] == 'admitted' for r in rows),
                    'refused_tasks': sum(r['admission'] == 'not_started' for r in rows),
                    'started_turns': sum(len(r['started']) for r in rows),
                    'terminal_turns': sum(len(r['terminal']) for r in rows),
                    'terminal_tasks': sum(r['task_terminal'] for r in rows)})
        return {'achieved_counts': counts, 'unique_attempt_count': len(self.attempts),
                'attempt_count_scope': 'deduplicated_observed_run_snapshots',
                'attributed_attempt_count': len(self.attributed_attempts),
                'unresolved_attempt_count': len(self.unresolved_attempts),
                'unassigned_run_attempt_count': len(self.attempts - self.attributed_attempts),
                'obligations': self.obligations, 'all_planned_tasks_retained': len(self.rows) == 144,
                'full_cohort_complete': not self.obligations and all(r['admission'] == 'admitted'
                    and r['task_terminal'] for r in self.rows.values()), 'product_acceptance': False}


async def external_calibration(provider, phase: str, page, task: dict, spec: dict, grant: AdmissionGrant) -> dict:
    if provider is None:
        raise AdmissionRefused('live_calibration_provider_unavailable')
    from voicelab.evaluation.trust import verify_gate_record
    from voicelab.evaluation.artifacts import digest
    from voicelab.reporting.diagnostics import calibration_report
    scope = {**grant.scope, 'task_id': task['task_id'], 'phase': phase}
    if page is not None:
        scope.update(session_id=task.get('_session_id'), graph_id=(await app_snapshot(page)).get('graph_id'))
    value = provider(phase, page, task, scope)
    if inspect.isawaitable(value):
        value = await value
    data, record = value['data'], value['record']
    scope['calibration_hash'] = digest(data)
    if data.get('phase') != phase or record.get('calibration_hash') != digest(data):
        raise AdmissionRefused('external_calibration_phase_hash_mismatch')
    if not verify_gate_record('calibration_evidence', record, scope,
            expected_record_hash=value.get('expected_record_hash', ''),
            authority=grant.authority, excluded_actor_refs=grant.excluded_actor_refs):
        raise AdmissionRefused('external_calibration_authority_unverified')
    analysis = calibration_report(data.get('rows', []))
    required = ('app_worklet_anchor_valid', 'injected_offset_rejected', 'past_scheduling_anchor_valid',
                'fence_ack_observed', 'app_fence_cutoff_within_quantum', 'stale_epoch_frame_refused')
    if (len(data.get('rows', [])) != 30 or not analysis['internal_gate_pass']
            or not all(data.get('controls', {}).get(key) is True for key in required)
            or data.get('cleanup_observed') is not True):
        raise AdmissionRefused('external_calibration_observations_incomplete')
    return data


async def play_turn(page, bound: dict, position: int, mode: str, *, cancel_after_commit: bool = False):
    state = (await app_snapshot(page))['state']
    if state == 'awaiting_response':
        await page.locator('#retry').click()
        await wait_state(page, ['listening'])
    elif state != 'listening':
        raise AdmissionRefused('terminal_or_disconnected_state_requires_explicit_task_disposition')
    before = await page.evaluate('()=>window.__voiceLabDriver.snapshot()')
    window = bound['_fixture']['utterances'][position]
    played = await page.evaluate('(window)=>window.__voiceLabFixtureSource.play(window)',
                                {'start_frame': window['start_frame'], 'end_frame': window['end_frame']})
    if mode == 'mock':
        # Explicit interpretation side channel: diagnostic only, never speech recognition evidence.
        await page.wait_for_timeout(200)
        if (await app_snapshot(page))['state'] != 'listening':
            raise AdmissionRefused('fixture_endpoint_changed_before_mock_diagnostic_commit')
        await page.locator('#text').fill(bound['_fixture']['turns'][position])
        await page.locator('#stimulus').evaluate('(form)=>form.requestSubmit()')
    await page.wait_for_function('''(args)=>{
      const app=window.__voiceLabDiagnostics.snapshot(),probe=window.__voiceLabDriver.snapshot();
      const canceled=probe.cancelTriggered>args.prior.cancelTriggered&&probe.fenceCounter>args.prior.fenceCounter
        &&probe.cancelAcks>args.prior.cancelAcks;
      const commit=probe.lastCommit,done=probe.lastCompletion;
      const completed=probe.commitCount>args.prior.commitCount&&probe.responseDone>args.prior.responseDone
        &&commit&&done&&commit.session_id===done.session_id&&commit.turn_id===done.turn_id
        &&commit.connection_epoch===done.connection_epoch&&commit.response_epoch===done.response_epoch;
      return (args.cancel?canceled:completed)
        ||['failed','recovery_unknown','closed','device_lost','device_muted'].includes(app.state);
    }''', arg={'prior': before, 'cancel': cancel_after_commit}, timeout=15000)
    await page.wait_for_function('''()=>{
      const app=window.__voiceLabDiagnostics.snapshot();
      return ['awaiting_response','listening','failed','recovery_unknown','closed'].includes(app.state);
    }''', timeout=5000)
    if (await app_snapshot(page))['state'] in {'failed', 'recovery_unknown', 'closed'}:
        raise AdmissionRefused('trial_response_failed_or_unknown')
    return played


async def verify_live_relay(spec: dict, task: dict, config: dict, nonce: str):
    from voicelab.core.admission import admission_evidence
    grant = spec['_cohort_grant']
    grant.recheck(spec)
    # Declared protected producer seam. Missing/unknown metadata refuses before fixture audio.
    admission = await asyncio.to_thread(own_diagnostic, spec, nonce, '/admission')
    row = admission.get('arms', {}).get(task['arm'])
    if admission.get('schema_version') != 1 or admission.get('mode') != 'live' or not isinstance(row, dict):
        raise AdmissionRefused('relay_live_admission_fingerprints_unverified')
    owner = row.get('budget_owner', {})
    if not owner.get('owner_id'):
        raise AdmissionRefused('relay_live_owner_identity_unknown')
    expected = admission_evidence(mode='live', run_id=spec['run_id'],
        run_manifest_hash=grant.scope['run_manifest_hash'], owner_id=owner['owner_id'], config=config)
    keys = ('mode', 'run_id', 'run_manifest_hash', 'manifest_hashes', 'requested_models', 'requested_voice',
            'expected_returned_models', 'budget_usd', 'budget_owner', 'licensed_control')
    if any(row.get(key) != expected[key] for key in keys):
        raise AdmissionRefused('relay_live_admission_fingerprints_unverified')
    task['_configured_admission'] = row
    return row


MOCK_FAULTS = frozenset({'late_pcm', 'duplicate_pcm', 'reordered_pcm', 'lost_ack', 'drop_before_commit',
    'drop_after_commit', 'fresh_tool_duplicate', 'service_restart', 'provider_error',
    'missing_audio', 'missing_transcript', 'queue_overrun'})
BROWSER_FAULTS = frozenset({'cancel_before_commit', 'cancel_after_commit', 'lost_rendered_ack',
    'device_lost', 'device_muted', 'malformed_protocol', 'duplicate_sequence'})


async def execute_trial(bound: dict, task: dict, nonce: str, sink, progress: Progress,
                        references: dict, grant: AdmissionGrant | None) -> bool:
    """One serial trial; return observed owned cleanup, never a correctness verdict."""
    from voicelab.evaluation.artifacts import digest
    task['_browser_opened'] = False
    task['_cleanup_observed'] = True
    if grant:
        bound = {**bound, '_cohort_grant': grant}
    if bound['mode'] == 'live':
        config = admit_live_arm(bound, task['arm'])
        await verify_live_relay(bound, task, config, nonce)
    else:
        config = None
    if any(action['kind'] not in MOCK_FAULTS | BROWSER_FAULTS for action in task['fault_schedule']):
        raise AdmissionRefused('unsupported_frozen_fault_schedule')
    if bound['mode'] == 'live' and any(action['kind'] in MOCK_FAULTS for action in task['fault_schedule']):
        raise AdmissionRefused('mock_control_not_a_live_fault_capability')
    task['_logical_turn_id'] = task['logical_turn_ids'][0]
    task['role'] = 'trial'
    bound = {**bound, '_fixture_stream': True, '_network': bound['cohort']['network']}
    if grant:
        bound['_cohort_grant'] = grant
    page = None
    cleanup_ok = False
    controls = MockControls(bound, nonce, sink)
    armed = []
    cursor = DiagnosticCursor()
    drain_stop = asyncio.Event()
    draining = None
    async with BrowserDriver(bound, nonce, sink) as driver:
        task['_browser_opened'] = True
        task['_cleanup_observed'] = False
        try:
            page, _ = await driver.page(task)
            await page.locator('#start').click()
            await wait_state(page, ['listening'], timeout=9000)
            if not task.get('_session_id'):
                raise AdmissionRefused('ready_identity_unobserved')
            if task.get('_server_run_id') != bound['run_id']:
                raise AdmissionRefused('actual_ready_run_differs_from_frozen_cohort_run')
            progress.rows[task['task_id']]['admission'] = 'admitted'
            sink.emit('task_admission', task_id=task['task_id'], arm=task['arm'], admission='admitted',
                session_id=task['_session_id'], server_trial_namespace=task['_trial_namespace'])
            cursor.sample_rate = (await page.evaluate('()=>window.__voiceLabDriver.graphStamp()'))['sample_rate']
            async def drain_loop():
                while not drain_stop.is_set():
                    await cursor.drain(page, sink, task['task_id'])
                    try:
                        await asyncio.wait_for(drain_stop.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        pass
            draining = asyncio.create_task(drain_loop())
            if grant:
                ready = task.get('_ready_metadata', {}).get('payload', {}).get('admission', {})
                configured = task['_configured_admission']
                if task.get('_boot_id') != configured.get('budget_owner', {}).get('owner_id'):
                    raise AdmissionRefused('provider_owner_boot_changed_after_preflight')
                if any(ready.get(key) != configured.get(key) for key in ('mode', 'run_id', 'run_manifest_hash',
                        'manifest_hashes', 'requested_models', 'requested_voice', 'expected_returned_models', 'budget_usd')):
                    raise AdmissionRefused('ready_configuration_changed_after_preflight')
                if task['arm'] == 'K':
                    actual = ready.get('licensed_control', {})
                    expected_control = config['licensed_control']
                    if (any(actual.get(key) != expected_control[key] for key in
                            ('kind', 'config_hash', 'catalog_hash', 'asset_hashes', 'task_applicability',
                             'spoken_correctness', 'provider_cost_scope', 'model_stages', 'translation'))
                            or actual.get('approval_observation') != 'external_verifier_accepted'
                            or actual.get('asset_observation') != 'local_wav_and_pcm_hashes_verified_at_open'
                            or ready.get('requested_models') != {} or ready.get('actually_returned_models') != {}):
                        raise AdmissionRefused('opened_licensed_control_hashes_or_external_approval_unverified')
                else:
                    component = 'native' if task['arm'] == 'V' else 'stt'
                    actual = ready.get('actually_returned_models', {}).get(component, {})
                    expected_returned = config['manifests']['capabilities']['returned_models'][component]
                    if (actual.get('observation') != 'observed' or actual.get('value') != expected_returned
                            or actual.get('source') != 'provider_session_updated'):
                        raise AdmissionRefused('opened_provider_returned_identity_unverified')
                sink.emit('opened_provider_identity', task_id=task['task_id'],
                          admission=ready, observation='observed', scope='returned_session_identity_only')
            for position, logical in enumerate(task['logical_turn_ids']):
                bound['_common_gate_check']()
                if sink.capacity_exhausted or draining.done() and draining.exception() is not None:
                    raise AdmissionRefused('bounded_observation_capture_unavailable')
                if grant:
                    grant.recheck(bound)
                    admit_live_arm(bound, task['arm'])
                if datetime.now(timezone.utc) >= utc(bound['cohort']['window']['expires_at']):
                    raise AdmissionRefused('cohort_window_expired')
                task['_logical_turn_id'] = logical
                actions = [a for a in task['fault_schedule'] if a['turn_position'] == position + 1]
                for action in actions:
                    sink.emit('action_attempted', task_id=task['task_id'], logical_turn_id=logical,
                              fault=action['kind'], action_id=f'{task["task_id"]}-{position + 1}-{action["kind"]}')
                    if action['kind'] in MOCK_FAULTS:
                        receipt = await controls.arm(task, action['kind'])
                        armed.append(receipt['control_id'])
                    elif action['kind'] == 'cancel_after_commit':
                        await page.evaluate('()=>window.__voiceLabDriver.configure({cancelAfterCommit:true})')
                    elif action['kind'] == 'lost_rendered_ack':
                        await page.evaluate('()=>window.__voiceLabDriver.configure({dropRenderedAck:true})')
                    else:
                        await perform_fault(page, action['kind'])
                sink.emit('turn_start_attempted', task_id=task['task_id'], logical_turn_id=logical,
                          fixture_id=task['fixture_id'], utterance_id=bound['_fixture']['utterances'][position]['utterance_id'],
                          audio_sha256=bound['_fixture']['audio']['sha256'])
                played = await play_turn(page, bound, position, bound['mode'],
                    cancel_after_commit=any(a['kind'] == 'cancel_after_commit' for a in actions))
                if any(a['kind'] == 'lost_rendered_ack' for a in actions):
                    await perform_fault(page, 'lost_rendered_ack')
                    await page.evaluate('()=>window.__voiceLabDriver.configure({dropRenderedAck:false})')
                    recovered = await cursor.drain(page, sink, task['task_id'])
                    if any(event['kind'] == 'repeat_requested' for event in recovered):
                        sink.emit('repeat_attempted', task_id=task['task_id'], logical_turn_id=logical,
                                  code='explicit_same_utterance_recovery_repeat')
                        await play_turn(page, bound, position, bound['mode'])
                sink.emit('fixture_window_observed', task_id=task['task_id'], logical_turn_id=logical,
                    snapshot=played, audio_sha256=bound['_fixture']['audio']['sha256'],
                    text_sha256=bound['_fixture']['utterances'][position]['text_sha256'],
                    boundary='synthetic_media_stream', spoken_correctness='unknown')
                await cursor.drain(page, sink, task['task_id'])
        finally:
            drain_stop.set()
            if draining is not None:
                try:
                    await draining
                except Exception:
                    progress.obligation(task, 'trial_diagnostic_capture_failed')
            if page:
                try:
                    await page.locator('#end').click()
                    app_cleanup = await wait_cleanup(page)
                    fixture_cleanup = await page.evaluate('()=>window.__voiceLabFixtureSource.close()')
                    cleanup_ok = (cleanup_count(app_cleanup) == 0
                        and app_cleanup['observation_statuses'].get('cleanup') == 'observed_released'
                        and fixture_cleanup.get('closed') is True
                        and all(v == 0 for v in fixture_cleanup['owned_handles'].values()))
                    task['_cleanup_observed'] = cleanup_ok
                    sink.emit('cohort_cleanup_observed', task_id=task['task_id'],
                        snapshot=app_cleanup, fixture_source=fixture_cleanup,
                        observation='observed' if cleanup_ok else 'unknown')
                    if not cleanup_ok:
                        progress.obligation(task, 'owned_trial_resources_not_proven_released')
                    await cursor.drain(page, sink, task['task_id'])
                    evidence = await server_evidence(bound, nonce, task)
                    raw_trace = collect_relay_trace(bound, evidence)
                    references[task['task_id'] + '-trace'] = sink.bytes_artifact(
                        task['task_id'] + '.jsonl', raw_trace, 'trace')
                    references[task['task_id'] + '-server'] = sink.artifact(
                        task['task_id'] + '.json', evidence, 'server_evidence')
                    ledger = sink.authoritative_artifact(collect_ledger_snapshot(bound, evidence), 'ledger_snapshot')
                    references[task['task_id'] + '-ledger'] = ledger
                    sink.emit('ledger_snapshot_reference', task_id=task['task_id'], scope='run_snapshot',
                              references={'ledger': ledger})
                    trace = [json.loads(line) for line in raw_trace.decode().splitlines() if line.strip()]
                    progress.audit(task, trace, evidence)
                    current = {r['control_id']: r for r in (evidence.get('faults') or {}).get('controls', [])}
                    for identifier in armed:
                        receipt = current.get(identifier, {})
                        applied = (receipt.get('status') == 'applied' and receipt.get('injected_count', 0) > 0
                            and any(e.get('control_id') == identifier and e.get('fault_status') == 'applied' for e in trace))
                        sink.emit('action_observed', task_id=task['task_id'], control_id=identifier,
                            receipt=receipt, observation='observed' if applied else 'unknown',
                            scope='injection_boundary_only; task_and_browser_outcome_not_inferred')
                        if not applied:
                            progress.obligation(task, 'fault_boundary_effect_unobserved')
                    if cursor.lost:
                        progress.obligation(task, 'trial_diagnostic_window_lost')
                except Exception as exc:
                    progress.obligation(task, 'trial_cleanup_or_evidence_' + type(exc).__name__)
                    cleanup_ok = False
                    task['_cleanup_observed'] = False
            else:
                # BrowserDriver owns closing any context created before page setup failed.
                progress.obligation(task, 'trial_page_setup_failed_before_cleanup_observation')
    return cleanup_ok


async def run_cohort(spec: dict, plan: dict, bundle: dict, artifact_root: str | Path,
                     freeze_hash: str, nonce: str, sink, *, authority=None,
                     calibration_provider=None, attempt_bound_verifier=None, k_approval_verifier=None,
                     excluded_actor_refs: frozenset[str] = frozenset()) -> dict:
    from voicelab.evaluation.artifacts import digest
    spec = {**spec, '_evidence_root': sink.root}
    progress = None
    references = {}
    grant = lease = None
    cleanup_known = True
    prepared = False
    aborted = None
    try:
        manifest, frozen, gates, pins, grant = prepare_cohort(spec, plan, bundle, artifact_root,
            freeze_hash, authority=authority, excluded_actor_refs=excluded_actor_refs)
        plan = frozen
        progress = Progress(plan, sink)
        spec = {**spec, 'trial_plan_hash': frozen['plan_hash'],
                '_artifact_reader': pins['reader'], '_public_access': pins['public_access'],
                '_attempt_bound_verifier': attempt_bound_verifier, '_common_gate_check': pins['common_gate_check']}
        spec['_k_approval_verifier'] = k_approval_verifier
        scope = pins['scope']
        async def calibration_within_window(operation):
            remaining = (utc(scope['window']['expires_at']) - datetime.now(timezone.utc)).total_seconds()
            # Control sessions do not renew a task or the frozen cohort window.
            return await asyncio.wait_for(operation, timeout=max(.01, min(120, remaining)))
        display_scope = {k: v for k, v in scope.items() if k != 'authorization_reference'}
        display_scope.update(scope_hash=digest(scope), authorization_reference_hash=hashlib.sha256(
            str(scope.get('authorization_reference') or '').encode()).hexdigest())
        references['cohort'] = sink.artifact('cohort-freeze.json', display_scope, 'cohort_freeze')
        bound = {}
        for task in frozen['tasks']:
            selected = bind_task_fixture(spec, task, planned=True)
            if selected['_fixture']['audio']['sha256'] != pins['fixtures'][task['fixture_id']]['sha256']:
                raise AdmissionRefused('actual_fixture_differs_from_frozen_D5_manifest')
            bound[task['task_id']] = selected
        prepared = True
        if spec['mode'] == 'live' and calibration_provider is None:
            raise AdmissionRefused('live_calibration_provider_unavailable')
        if spec['mode'] == 'live' and not callable(attempt_bound_verifier):
            raise AdmissionRefused('real_attempt_bound_verifier_unavailable')
        lease = LiveLease(spec, scope) if grant else None
        with lease if lease else nullcontext():
            first = frozen['tasks'][0]
            if grant:
                # External observation provider is trusted code injected programmatically,
                # never loaded from a manifest or replaced with a local fake verifier.
                pre = await calibration_within_window(external_calibration(calibration_provider, 'pre', None,
                    {'task_id': 'cohort-calibration-pre'}, spec, grant))
                references['calibration-pre'] = sink.artifact('cohort-calibration-pre.json', pre, 'calibration_observations')
            else:
                from .calibration import run_calibration
                control = {**bound[first['task_id']], '_fixture_stream': True}
                pre_result = await calibration_within_window(run_calibration(control, nonce, sink, 'pre'))
                references.update(pre_result['references'])
                if pre_result['incomplete']:
                    raise AdmissionRefused('cohort_pre_calibration_incomplete')
            for frozen_task in frozen['tasks']:
                pins['common_gate_check']()
                task = {key: value for key, value in frozen_task.items() if not key.startswith('_')}
                if datetime.now(timezone.utc) >= utc(scope['window']['expires_at']):
                    raise AdmissionRefused('cohort_window_expired')
                if grant and task['arm'] == 'K' and (not spec.get('k_control_config') or not callable(k_approval_verifier)):
                    progress.refuse(task, 'live_K_configuration_or_external_approval_verifier_absent')
                    progress.obligation(task, 'separate_K_control_not_executed')
                    continue
                try:
                    remaining = (utc(scope['window']['expires_at']) - datetime.now(timezone.utc)).total_seconds()
                    closed = await asyncio.wait_for(execute_trial(bound[task['task_id']], task, nonce, sink,
                        progress, references, grant), timeout=max(.01, remaining))
                    cleanup_known = cleanup_known and closed
                except AdmissionRefused as exc:
                    cleanup_known = cleanup_known and task.get('_cleanup_observed', True)
                    code = str(exc) if re.fullmatch(r'[A-Za-z0-9_]{1,160}', str(exc)) else 'trial_admission_refused'
                    progress.refuse(task, code)
                    progress.obligation(task, code)
                except Exception as exc:
                    cleanup_known = cleanup_known and task.get('_cleanup_observed', True)
                    progress.obligation(task, 'trial_aborted_' + type(exc).__name__)
                if not cleanup_known:
                    raise AdmissionRefused('serialized_owned_cleanup_unknown_stop')
            if grant:
                post = await calibration_within_window(external_calibration(calibration_provider, 'post', None,
                    {'task_id': 'cohort-calibration-post'}, spec, grant))
                references['calibration-post'] = sink.artifact('cohort-calibration-post.json', post, 'calibration_observations')
            else:
                post_result = await calibration_within_window(run_calibration(control, nonce, sink, 'post'))
                references.update(post_result['references'])
                if post_result['incomplete']:
                    progress.obligation(None, 'cohort_post_calibration_incomplete')
            if lease:
                lease.release_allowed = cleanup_known
    except BaseException as exc:
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            aborted = exc
        code = str(exc) if isinstance(exc, AdmissionRefused) and re.fullmatch(r'[A-Za-z0-9_]{1,160}', str(exc)) else 'cohort_gate_or_abort_' + type(exc).__name__
        if progress is not None:
            progress.obligation(None, code)
            for task in plan['tasks']:
                if progress.rows[task['task_id']]['admission'] == 'scheduled':
                    progress.refuse(task, code)
            if prepared:
                progress.obligation(None, 'post_calibration_or_remaining_cohort_observations_outstanding')
        else:
            sink.emit('cohort_obligation', code=code, observation='unknown', status='not_started')
    finally:
        if lease and lease.created and not lease.release_allowed:
            progress.obligation(None, 'local_live_owner_retained_pending_operator_recovery')
    summary = progress.finish() if progress else {'full_cohort_complete': False,
        'achieved_counts': None, 'all_planned_tasks_retained': None,
        'code': 'membership_not_exposed_without_authenticated_read_access', 'product_acceptance': False}
    references['summary'] = sink.artifact('cohort-summary.json', summary, 'cohort_summary')
    result = {'references': references, 'summary': summary,
            'incomplete': [] if summary['full_cohort_complete'] else ['frozen_cohort_obligations_or_refusals'],
            'evidence_label': 'ordered development observations; no independent spoken/live acceptance'}
    sink.latest_cohort_result = result
    if aborted is not None:
        raise aborted
    return result


def execute_cohort(args, *, authority=None, calibration_provider=None, attempt_bound_verifier=None,
                    public_access=None, k_approval_verifier=None,
                    excluded_actor_refs: frozenset[str] = frozenset()) -> None:
    """CLI/programmatic host adapter; JSON can never select a verifier implementation."""
    raw_manifest = args.manifest.read_bytes()
    bundle = json.loads(args.gate_bundle.read_text(encoding='utf-8'))  # public DTOs/descriptors only
    from voicelab.evaluation.exposure import PublicReadAccess
    if public_access is None and authority is not None:
        metadata = bundle['public_access']
        public_access = PublicReadAccess(authority, metadata['records'], metadata['hashes'], metadata['scope'],
                                        excluded_actor_refs=excluded_actor_refs)
    output_reader = (public_access.reader(args.artifact_root, bundle['artifacts'])
                     if isinstance(public_access, PublicReadAccess) else None)
    # Activation/public DTO metadata are not the private trial plan or fixture corpus.
    sink = EvidenceSink(args.out, {'run_id': json.loads(raw_manifest)['run_id'],
        'mode': json.loads(raw_manifest).get('mode', 'unknown'),
        '_output_reader': output_reader,
        '_manifest_hash': hashlib.sha256(raw_manifest).hexdigest()})
    result = None
    entered = False
    aborted = None
    try:
        spec = load_activation(args.manifest, args.activation_reference,
                               allow_live=True, load_default_fixture=False)
        spec.update(_evidence_root=sink.root, _requested_plan_path=args.trial_plan)
        if public_access is not None:
            spec['_public_access'] = public_access
        nonce = os.environ.get('VOICELAB_NONCE', '')
        if not 16 <= len(nonce) <= 128:
            raise AdmissionRefused('operator_launch_boundary_absent')
        entered = True
        result = asyncio.run(run_cohort(spec, None, bundle, args.artifact_root, args.freeze_hash,
            nonce, sink, authority=authority, calibration_provider=calibration_provider,
            attempt_bound_verifier=attempt_bound_verifier,
            k_approval_verifier=k_approval_verifier,
            excluded_actor_refs=excluded_actor_refs))
    except BaseException as exc:
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            aborted = exc
        code = str(exc) if isinstance(exc, AdmissionRefused) and re.fullmatch(r'[A-Za-z0-9_]{1,160}', str(exc)) else 'cohort_setup_' + type(exc).__name__
        if not entered:
            sink.emit('cohort_obligation', code=code, observation='unknown', status='not_started')
            summary = {'full_cohort_complete': False, 'achieved_counts': None,
                       'code': 'membership_not_exposed_without_authenticated_read_access',
                       'product_acceptance': False}
        elif getattr(sink, 'latest_cohort_result', None) is not None:
            result = sink.latest_cohort_result
            summary = result['summary']
        else:
            # A writer/host failure must not fabricate zero achieved counts or replay membership.
            sink.emit('cohort_obligation', observation='unknown', code=code, status='unresolved')
            summary = {'status': 'unresolved', 'full_cohort_complete': False,
                       'achieved_counts': None, 'product_acceptance': False}
        if result is None:
            result = {'references': {}, 'summary': summary, 'incomplete': [code]}
    finally:
        references = result.get('references', {}) if result else {}
        if result is not None:
            references['driver-result'] = sink.artifact('cohort-driver-result.json', result, 'cohort_driver_result')
        sink.close(completed=bool(result is not None and not result.get('incomplete')),
                   reason='cohort_observations_closed' if result and not result.get('incomplete') else 'cohort_incomplete',
                   references=references)
    if aborted is not None:
        raise aborted
