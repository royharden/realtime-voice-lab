"""Convert raw driver evidence, keeping action attempts separate from server facts."""
from __future__ import annotations

import re

from voicelab.contracts import AdmissionEvidence, LicensedControlEvidence, ServerMessage, VoiceEvent

AUXILIARY_ROLES = frozenset({'calibration_control'})
_SHA256 = re.compile(r'^[a-f0-9]{64}$')
LINEAGE_FIELDS = ('attempt_id', 'session_id', 'observed_logical_turn_id', 'observed_transport_turn_id',
                  'observed_trial_id', 'connection_epoch', 'response_epoch', 'observed_boot_id')


def _rollup(event: dict) -> dict | None:
    """Metadata count/hash only; never acoustic, correctness, schedule or control-pass evidence."""
    count, digest = event.get('observed_message_count'), event.get('metadata_stream_sha256')
    if (type(count) is not int or count < 0 or not isinstance(digest, str) or not _SHA256.match(digest)
            or event.get('direction') not in {'sent', 'received'} or event.get('scope') != 'browser_socket_metadata_only'):
        return None
    return {'task_id': event.get('task_id'), 'role': event.get('role', 'trial'), 'session_id': event.get('session_id'),
            'direction': event['direction'], 'observed_message_count': count, 'metadata_stream_sha256': digest,
            'audio_observation_stride': event.get('audio_observation_stride'), 'sequence': event.get('sequence'),
            'scope': 'browser_socket_metadata_only',
            'qualification': 'count/hash of redacted wire metadata; not raw audio, correctness, a scheduled task or a measured/control pass'}


def _admission(event: dict, task: dict) -> dict:
    """Opened identity fingerprint as observed by the driver; independent verification stays unknown."""
    raw = event.get('admission')
    issues, notes = [], []
    try:
        raw = dict(raw)
        control = raw.pop('licensed_control', None)
        admission = AdmissionEvidence.model_validate(raw).model_dump(mode='json')
        if control is not None:
            # The driver privacy projection removes every *auth* key, so the required
            # authorization_reference never survives. Validate structure with a local
            # placeholder, then publish it as unknown; it is never reported as a value.
            control = dict(control)
            projected = 'authorization_reference' not in control
            control.setdefault('authorization_reference', 'privacy-projected-unknown')
            control = LicensedControlEvidence.model_validate(control).model_dump(mode='json')
            if projected:
                control['authorization_reference'] = None
                notes.append('K authorization reference removed by privacy projection; reference unknown in saved driver evidence')
        admission['licensed_control'] = control
    except (TypeError, ValueError):
        return {'task_id': task['task_id'], 'arm': task['arm'], 'observation': 'unknown',
                'reason': 'opened admission evidence malformed or privacy-projected incomplete'}
    if task['arm'] == 'K':
        if control is None:
            issues.append('K admission lacks licensed control metadata')
        elif control['approval_observation'] != 'external_verifier_accepted':
            issues.append('K approval observed as configuration only; external verifier acceptance absent')
        if admission['requested_models'] or admission['actually_returned_models']:
            issues.append('K admission names provider models; no-provider control identity conflicted')
    else:
        if control is not None:
            issues.append('licensed control metadata on non-K arm')
        for component, expected in sorted(admission['expected_returned_models'].items()):
            actual = admission['actually_returned_models'].get(component) or {}
            if actual.get('observation') != 'observed' or actual.get('value') != expected:
                issues.append('returned ' + component + ' identity not observed equal to expected')
    return {'task_id': task['task_id'], 'arm': task['arm'], 'observation': 'observed',
            'source': 'driver_opened_provider_identity', 'admission': admission,
            'requested_models': admission['requested_models'],
            'expected_returned_models': admission['expected_returned_models'],
            'actually_returned_models': admission['actually_returned_models'],
            'fingerprints': {'run_manifest_hash': admission['run_manifest_hash'], **admission['manifest_hashes'],
                             **({'k_config_hash': control['config_hash'], 'k_catalog_hash': control['catalog_hash']}
                                if control else {})},
            'licensed_control': control, 'independent_verification': 'unknown', 'issues': issues, 'notes': notes}


def _redacted(value: object, container: str | None = None) -> bool:
    blocked = {'nonce', 'headers', 'authorization', 'api_key', 'pcm16', 'audio', 'samples',
               'transcript', 'transcripts', 'raw_transcript', 'text'}
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            # manifest_hashes.authorization is a 64-hex manifest digest slot, not a credential.
            hash_slot = (container == 'manifest_hashes' and name == 'authorization'
                         and (item is None or isinstance(item, str) and _SHA256.match(item) is not None))
            if name in blocked and not hash_slot or not _redacted(item, name):
                return False
        return True
    if isinstance(value, list):
        return all(_redacted(item, container) for item in value)
    return True


def convert_driver_events(events: list[dict], plan: dict, *, mode: str = 'mock') -> dict:
    """``mode`` is the run manifest mode; evidence from another mode never joins this plan."""
    if not _redacted(events):
        raise ValueError('raw driver evidence is not metadata-only/redacted; source repair required')
    if mode not in {'mock', 'live'}:
        raise ValueError('driver evidence conversion supports frozen mock/live runs only')
    assignments = {row['logical_turn_id']: row for row in plan['turn_assignments']}
    tasks = {task['task_id']: task for task in plan['tasks']}
    observations, mappings, attempt_mappings, ready, issues = {}, {}, {}, {}, []
    auxiliary, rollups, admissions, lineage_unresolved = {}, [], {}, []
    last_sequence, last_timestamp = -1, -1
    for index, event in enumerate(events):
        if event.get('run_id') != plan['run_id'] or event.get('mode') != mode or event.get('split') != 'dev':
            raise ValueError('driver evidence must bind this run mode/dev plan')
        sequence, timestamp = event.get('sequence'), event.get('timestamp')
        if (type(sequence) is not int or type(timestamp) is not int or sequence <= last_sequence
                or timestamp < last_timestamp or event.get('clock_domain') != 'driver_monotonic_ns'):
            issues.append(f'driver record {index}: sequence/clock integrity unknown')
        else:
            last_sequence, last_timestamp = sequence, timestamp
        task_id, turn = event.get('task_id'), event.get('logical_turn_id')
        kind = event.get('kind')
        # Auxiliary calibration-control sessions are never planned tasks, turns or denominators.
        if event.get('role') in AUXILIARY_ROLES or task_id in auxiliary:
            if task_id in tasks or turn is not None and turn in assignments:
                raise ValueError('auxiliary calibration evidence claims a planned task/turn')
            entry = auxiliary.setdefault(task_id, {'task_id': task_id, 'role': event.get('role', 'calibration_control'),
                'phase': None, 'scheduled': False, 'session_ids': [], 'evidence_unknown': [], 'wire_audio_rollups': [],
                'qualification': 'calibration phases/receipts alone do not prove probe effects or successful calibration'})
            if kind == 'auxiliary_session_scheduled':
                entry['scheduled'], entry['phase'] = True, event.get('phase')
            elif kind == 'auxiliary_session_evidence_unknown':
                entry['evidence_unknown'].append(str(event.get('error_type', 'unknown')))
            elif kind == 'wire_audio_rollup':
                checked = _rollup(event)
                if checked is None:
                    entry['evidence_unknown'].append('malformed wire_audio_rollup metadata')
                else:
                    entry['wire_audio_rollups'].append(checked)
            if isinstance(event.get('session_id'), str) and event['session_id'] not in entry['session_ids']:
                entry['session_ids'].append(event['session_id'])
            continue
        if task_id is not None and task_id not in tasks:
            raise ValueError('driver task outside prespecified schedule')
        if turn is not None and (turn not in assignments or assignments[turn]['task_id'] != task_id):
            raise ValueError('driver logical turn/task mismatch')
        if turn is not None:
            observations.setdefault(turn, {'run_id': plan['run_id'], 'trial_id': assignments[turn]['trial_id'],
                'logical_turn_id': turn, 'admission': 'unknown', 'outcome': None, 'timing_domain': 'unknown'})
        if kind == 'wire_audio_rollup':
            checked = _rollup(event)
            if checked is None:
                issues.append('malformed wire_audio_rollup metadata')
            else:
                rollups.append(checked)
            continue
        if kind == 'opened_provider_identity' and task_id in tasks:
            if task_id in admissions:
                issues.append('opened admission identity repeated: ' + task_id)
            admissions[task_id] = _admission(event, tasks[task_id])
            continue
        if kind == 'attempt_lineage_observed':
            # D7 emits this only for an exact reservation-time lineage present in the
            # session_attempts projection and observed server turns; never a total.
            candidate = {key: event.get(key) for key in LINEAGE_FIELDS}
            candidate['observed_run_id'] = plan['run_id'] if task_id not in ready else ready[task_id]['observed_run_id']
            candidate['task_id'] = task_id
            candidate['source'] = 'reservation_lineage'
            if any(candidate[key] is None for key in LINEAGE_FIELDS[:5]):
                lineage_unresolved.append(candidate['attempt_id'] or 'attempt_lineage_observed_incomplete')
                continue
            old = attempt_mappings.get(candidate['attempt_id'])
            if old is not None and any(old.get(key) != value for key, value in candidate.items()
                                       if key in old and key != 'source' and value is not None):
                raise ValueError('attempt crosses observed driver sessions')
            attempt_mappings[candidate['attempt_id']] = {**(old or {}), **candidate}
            continue
        if kind == 'cohort_obligation' and event.get('code') == 'run_snapshot_attempt_lineage_unresolved':
            lineage_unresolved.append('run_snapshot_attempt_lineage_unresolved')
            continue
        if kind == 'wire_received':
            message = event.get('message_metadata', {})
            try:
                ServerMessage.model_validate(message)
            except ValueError:
                issues.append('malformed received driver wire envelope')
                continue
            payload = message.get('payload', {})
            if message.get('type') == 'ready':
                if not all(isinstance(payload.get(key), str) and payload[key] for key in ('run_id', 'trial_id', 'boot_id')):
                    issues.append('ready lacks observed run/trial/boot identity')
                    continue
                if task_id not in tasks or payload.get('arm') != tasks[task_id]['arm']:
                    raise ValueError('ready arm differs from planned driver task')
                value = {'observed_run_id': payload.get('run_id'),
                         'observed_trial_id': payload.get('trial_id', payload.get('trial_namespace')),
                         'observed_boot_id': payload.get('boot_id'),
                         'session_id': message.get('session_id'), 'task_id': task_id}
                if task_id in ready and ready[task_id] != value:
                    issues.append('driver ready identity changed: ' + task_id)
                ready[task_id] = value
            # A transport's ended ledger can supply attempt-to-observed-task correlations.
            if message.get('type') == 'ended' and task_id in ready:
                scope = (payload.get('ledger_artifact') or {}).get('scope')
                ledger = payload.get('ledger')
                for attempt in ledger.get('attempts', []) if isinstance(ledger, dict) else []:
                    # D4 emits a whole-run snapshot. Capturing it for this session
                    # does not assign every historical attempt to this session.
                    if scope != 'session_ledger_snapshot' and attempt.get('session_id') != ready[task_id]['session_id']:
                        continue
                    candidate = {**ready[task_id], 'attempt_id': attempt['attempt_id'],
                                 'observed_logical_turn_id': attempt['logical_turn_id']}
                    if attempt.get('lineage_observation') == 'recorded_at_reservation':
                        candidate.update(observed_transport_turn_id=attempt.get('transport_turn_id'),
                                         connection_epoch=attempt.get('connection_epoch'),
                                         response_epoch=attempt.get('response_epoch'))
                    old = attempt_mappings.get(attempt['attempt_id'])
                    if old is not None and any(old.get(key) != candidate.get(key) for key in candidate
                                               if key in old and candidate.get(key) is not None):
                        raise ValueError('attempt crosses observed driver sessions')
                    attempt_mappings[attempt['attempt_id']] = {**(old or {}), **candidate}
            if message.get('type') == 'audio' and turn is not None and payload.get('response_id'):
                observations[turn]['response_id'] = payload['response_id']
            server_event = payload if message.get('type') == 'event' and 'run_id' in payload else None
            if server_event is not None and turn is not None:
                try:
                    checked = VoiceEvent.model_validate(server_event).model_dump(mode='json')
                except ValueError:
                    issues.append('malformed server event in driver evidence')
                    continue
                identity = (checked['run_id'], checked['trial_id'], checked['session_id'],
                            checked.get('transport_turn_id') or checked.get('logical_turn_id'))
                old = mappings.get(identity)
                if old is not None and old['logical_turn_id'] != turn:
                    issues.append('ambiguous driver event-to-plan correlation')
                else:
                    mappings[identity] = {**ready.get(task_id, {}), 'observed_run_id': checked['run_id'],
                        'observed_trial_id': checked['trial_id'], 'session_id': checked['session_id'],
                        'observed_logical_turn_id': checked['logical_turn_id'],
                        'observed_transport_turn_id': checked['transport_turn_id'], 'logical_turn_id': turn}
                if checked['kind'] == 'turn_started':
                    observations[turn]['admission'] = 'started'
                if checked['kind'] == 'turn_ended':
                    observations[turn]['outcome'] = checked.get('terminal_outcome')
        if kind == 'wire_sent' and turn is not None and task_id in ready:
            message = event.get('message_metadata', {})
            if message.get('type') in {'audio', 'commit'} and message.get('turn_id'):
                observed = ready[task_id]
                identity = (observed['observed_run_id'], observed['observed_trial_id'], observed['session_id'], message['turn_id'])
                value = {**observed, 'observed_transport_turn_id': message['turn_id'], 'logical_turn_id': turn}
                if identity in mappings and mappings[identity]['logical_turn_id'] != turn:
                    issues.append('observed transport turn mapped to multiple planned turns')
                else:
                    mappings[identity] = value
        # Only explicit measured browser anchors are admitted. Driver ns timestamps,
        # DOM states, response-done text and wait durations cannot supply browser latency.
        timing = event.get('timing')
        if turn is not None and isinstance(timing, dict) and timing.get('clock_domain') == 'browser_performance_ms':
            for key in ('speech_end_ms', 'first_received_pcm_ms', 'first_nonsilent_render_ms'):
                if timing.get(key) is not None:
                    value = timing[key]
                    if key == 'speech_end_ms' and observations[turn].get(key) is not None:
                        value = min(observations[turn][key], value)
                    observations[turn][key] = value
            observations[turn]['timing_domain'] = 'browser_performance_ms'
        if turn is not None:
            for key in ('input_audio_artifact', 'rendered_audio_artifact', 'calibration_artifact', 'confirmation_artifact'):
                if event.get(key):
                    observations[turn][key] = event[key]
            if kind in {'turn_not_started', 'scheduled_turn_unattempted'} and event.get('observation') == 'observed':
                observations[turn]['admission'] = 'not_started'
            if kind == 'turn_refused' and event.get('observation') == 'observed':
                observations[turn]['admission'] = 'refused'
    # Resolve ledger attempts by exact observed session + transport, never by turn ID
    # alone: browser turn IDs can repeat across several task sessions.
    # A reservation-time transport ID is preferred; the original logical ID is the fallback.
    resolved_attempts = []
    for attempt in attempt_mappings.values():
        prefix = (attempt['observed_run_id'], attempt['observed_trial_id'], attempt['session_id'])
        keys = [prefix + (value,) for value in (attempt.get('observed_transport_turn_id'),
                                                 attempt['observed_logical_turn_id']) if value is not None]
        targets = {mappings[key]['logical_turn_id'] for key in keys if key in mappings}
        if len(targets) == 1:
            resolved_attempts.append({**attempt, 'logical_turn_id': next(iter(targets))})
        else:
            issues.append('attempt lacks unambiguous session-to-plan mapping: ' + attempt['attempt_id'])
    if issues:
        for row in observations.values():
            row['timing_domain'], row['outcome'] = 'unknown', 'missing_terminal'
    return {'observations': [observations[key] for key in sorted(observations)],
            'correlations': [mappings[key] for key in sorted(mappings, key=str)],
            'task_admissions': [{'task_id': task, 'admission': 'admitted', 'source': 'observed_ready_envelope',
                                  **ready[task]} for task in sorted(ready)],
            'attempt_correlations': sorted(resolved_attempts, key=lambda row: row['attempt_id']),
            'admission_evidence': [admissions[key] for key in sorted(admissions)],
            'auxiliary_sessions': [auxiliary[key] for key in sorted(auxiliary, key=str)],
            'wire_audio_rollups': rollups,
            'attempt_lineage_unresolved': sorted(set(lineage_unresolved)),
            'issues': sorted(set(issues)), 'raw_events_preserved': True,
            'limitation': 'Action attempts, session admission and driver completion do not establish logical-turn or spoken-task success'}
