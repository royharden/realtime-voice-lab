"""Convert raw driver evidence, keeping action attempts separate from server facts."""
from __future__ import annotations

from voicelab.contracts import ServerMessage, VoiceEvent


def _redacted(value: object) -> bool:
    blocked = {'nonce', 'headers', 'authorization', 'api_key', 'pcm16', 'audio', 'samples',
               'transcript', 'transcripts', 'raw_transcript', 'text'}
    if isinstance(value, dict):
        return not blocked.intersection(str(key).lower() for key in value) and all(_redacted(item) for item in value.values())
    if isinstance(value, list):
        return all(_redacted(item) for item in value)
    return True


def convert_driver_events(events: list[dict], plan: dict) -> dict:
    if not _redacted(events):
        raise ValueError('raw driver evidence is not metadata-only/redacted; source repair required')
    assignments = {row['logical_turn_id']: row for row in plan['turn_assignments']}
    tasks = {task['task_id']: task for task in plan['tasks']}
    observations, mappings, attempt_mappings, ready, issues = {}, {}, {}, {}, []
    last_sequence, last_timestamp = -1, -1
    for index, event in enumerate(events):
        if event.get('run_id') != plan['run_id'] or event.get('mode') != 'mock' or event.get('split') != 'dev':
            raise ValueError('driver evidence must bind this mock/dev plan')
        sequence, timestamp = event.get('sequence'), event.get('timestamp')
        if (type(sequence) is not int or type(timestamp) is not int or sequence <= last_sequence
                or timestamp < last_timestamp or event.get('clock_domain') != 'driver_monotonic_ns'):
            issues.append(f'driver record {index}: sequence/clock integrity unknown')
        else:
            last_sequence, last_timestamp = sequence, timestamp
        task_id, turn = event.get('task_id'), event.get('logical_turn_id')
        if task_id is not None and task_id not in tasks:
            raise ValueError('driver task outside prespecified schedule')
        if turn is not None and (turn not in assignments or assignments[turn]['task_id'] != task_id):
            raise ValueError('driver logical turn/task mismatch')
        if turn is not None:
            observations.setdefault(turn, {'run_id': plan['run_id'], 'trial_id': assignments[turn]['trial_id'],
                'logical_turn_id': turn, 'admission': 'unknown', 'outcome': None, 'timing_domain': 'unknown'})
        kind = event.get('kind')
        if kind == 'wire_received':
            message = event.get('message', {})
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
                for attempt in payload.get('ledger', {}).get('attempts', []):
                    candidate = {**ready[task_id], 'attempt_id': attempt['attempt_id'],
                                 'observed_logical_turn_id': attempt['logical_turn_id']}
                    old = attempt_mappings.get(attempt['attempt_id'])
                    if old is not None and old != candidate:
                        raise ValueError('attempt crosses observed driver sessions')
                    attempt_mappings[attempt['attempt_id']] = candidate
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
            message = event.get('message', {})
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
    resolved_attempts = []
    for attempt in attempt_mappings.values():
        key = (attempt['observed_run_id'], attempt['observed_trial_id'], attempt['session_id'], attempt['observed_logical_turn_id'])
        if key in mappings:
            resolved_attempts.append({**attempt, 'logical_turn_id': mappings[key]['logical_turn_id']})
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
            'issues': sorted(set(issues)), 'raw_events_preserved': True,
            'limitation': 'Action attempts, session admission and driver completion do not establish logical-turn or spoken-task success'}
