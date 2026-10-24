"""One keyless V-mock journey through the real relay, plus a stale-epoch fault row. NOT YET RUN.

Plan W5/M6-05: mock is the default, full-path and keyless. TestClient drives the ASGI app
in-process (no listener, no port, no provider). Requires httpx for fastapi.testclient.
"""
from __future__ import annotations

import asyncio
import asyncio.base_events
import base64
from decimal import Decimal
import json
import socket
import urllib.request

import pytest
from fastapi.testclient import TestClient

from voicelab.core.task import TaskAuthority
from voicelab.server.app import Settings, create_app

NONCE = 'regression-nonce-0123456789abcdef'
ORIGIN = 'http://127.0.0.1:5173'
BASE = 'http://127.0.0.1:8000'
WS = 'ws://127.0.0.1:8000/ws?nonce=' + NONCE
CREDENTIAL_SENTINEL = 'sk-REGRESSION-SENTINEL-never-sent'
DIALOG = ('what are your hours', 'list slots', 'select slot 1', 'yes confirm', 'status')


@pytest.fixture
def egress_guard(monkeypatch):
    """Record (and refuse) any network or provider egress attempt during the journey."""
    attempts: list[str] = []

    def refuse(name):
        def blocked(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError('egress attempted in mock mode: ' + name)
        return blocked

    monkeypatch.setenv('OPENAI_API_KEY', CREDENTIAL_SENTINEL)
    # Outbound TCP entry points only: socket.socket.connect stays intact because the Windows
    # asyncio self-pipe (socket.socketpair) connects over loopback internally.
    monkeypatch.setattr(socket, 'create_connection', refuse('socket.create_connection'))
    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, 'create_connection',
                        refuse('loop.create_connection'))
    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', refuse('urllib.open'))
    from voicelab.adapters import transport
    monkeypatch.setattr(transport.ProviderTransport, 'connect', refuse('ProviderTransport.connect'))
    monkeypatch.setattr(transport.ProviderTransport, 'speech', refuse('ProviderTransport.speech'))
    from voicelab.adapters import live
    monkeypatch.setattr(live.LiveArmSession, '__init__', refuse('LiveArmSession'))
    return attempts


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(nonce=NONCE, allowed_origin=ORIGIN, artifact_root=tmp_path / 'runtime'))
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


def _until(ws, predicate, log: list[dict], limit: int = 64) -> dict:
    for _ in range(limit):
        message = ws.receive_json()
        log.append(message)
        if predicate(message):
            return message
    raise AssertionError('expected relay message not received within bound')


def _hello(ws, log):
    ws.send_text(json.dumps({'type': 'hello', 'protocol_version': 1, 'mode': 'mock', 'arm': 'V',
                             'nonce': NONCE}))
    ready = _until(ws, lambda m: m['type'] == 'ready', log)
    _until(ws, lambda m: m['type'] == 'state' and m['payload'].get('state') == 'listening', log)
    return ready


def _turn(ws, turn_id: str, text: str, log: list[dict]) -> dict:
    """Commit debug text, collect the response, acknowledge full render, await listening."""
    ws.send_text(json.dumps({'type': 'commit', 'turn_id': turn_id, 'connection_epoch': 0,
                             'response_epoch': 0, 'text': text}))
    samples, response_id, task, done = 0, None, None, False
    for _ in range(64):
        message = ws.receive_json()
        log.append(message)
        payload = message['payload']
        if message['type'] == 'audio':
            response_id = payload['response_id']
            samples += len(base64.b64decode(payload['pcm16'])) // 2
        elif message['type'] == 'task':
            task = payload
        elif message['type'] == 'event' and payload.get('kind') == 'response_done':
            done = True
        elif message['type'] == 'error':
            raise AssertionError('relay failed: ' + str(payload.get('code')))
        elif done and message['type'] == 'state' and payload.get('state') == 'playing':
            break
    else:
        raise AssertionError('response not completed within bound')
    ws.send_text(json.dumps({'type': 'rendered', 'response_id': response_id,
                             'rendered_through_sample': samples}))
    _until(ws, lambda m: m['type'] == 'state' and m['payload'].get('state') == 'listening', log)
    return task


def _evidence(client, session_id: str) -> dict:
    response = client.post(f'/sessions/{session_id}/evidence', content=b'{}',
                           headers={'origin': ORIGIN, 'x-voicelab-nonce': NONCE})
    assert response.status_code == 200
    return response.json()


def test_keyless_mock_journey_books_once_and_saves_evidence(client, egress_guard):
    """what_bug_this_catches: the default mock path silently requiring keys/egress, a booking
    without a distinct confirmation turn, or a session whose terminal, turns, task journal and
    zero-cost all-attempt ledger are not durably saved and readable through the canonical
    evidence reader (plan M6-05 full-path keyless mocks)."""
    log: list[dict] = []
    with client.websocket_connect(WS, headers={'origin': ORIGIN}) as ws:
        ready = _hello(ws, log)
        session_id = ready['session_id']
        assert ready['payload']['mode'] == 'mock'
        tasks = [_turn(ws, f'turn-{index}', text, log) for index, text in enumerate(DIALOG, start=1)]
        ws.send_text(json.dumps({'type': 'end'}))
        ended = _until(ws, lambda m: m['type'] == 'ended', log)['payload']

    assert [task['ok'] for task in tasks] == [True] * 5
    assert tasks[3]['effect_committed'] is True and tasks[3]['state'] == 'booked'
    assert ended['outcome'] == 'success' and ended['reason'] == 'ended_by_user'
    assert ended['task']['state'] == 'complete'
    assert ended['spoken_correctness'] == 'unknown'
    assert ended['trace_hash'] and ended['lifecycle_persisted'] is True

    evidence = _evidence(client, session_id)
    assert evidence['session']['terminal_outcome'] == 'success'
    assert evidence['session']['trace_hash'] == ended['trace_hash']
    assert [turn['terminal_outcome'] for turn in evidence['turns']] == ['success'] * 5
    journal = evidence['task_journal']
    assert journal['task']['booking']['slot_id'] == 'slot-a'
    assert sum(op['command'] == 'confirm_slot' for op in journal['original_operations']) == 1
    ledger = evidence['ledger']
    assert ledger['complete'] is True and Decimal(ledger['known_cost']) == 0
    assert all(row['reservation_terms']['mode'] == 'mock' for row in ledger['attempts'])
    assert len(evidence['session_attempts']) == 5
    assert evidence['runtime_verified'] is False

    assert egress_guard == []
    transcript = json.dumps(log) + json.dumps(evidence)
    assert CREDENTIAL_SENTINEL not in transcript
    assert NONCE not in transcript


def test_stale_epoch_commit_is_refused_and_terminal(client, egress_guard):
    """what_bug_this_catches (fault matrix: stale epoch callback): a frame carrying a response
    epoch the relay never issued being admitted, starting a turn or reaching the authority.
    SessionState.commit raises stale_epoch; the session ends as a visible protocol error with
    no task operation and the failure is durably journaled."""
    log: list[dict] = []
    with client.websocket_connect(WS, headers={'origin': ORIGIN}) as ws:
        session_id = _hello(ws, log)['session_id']
        ws.send_text(json.dumps({'type': 'commit', 'turn_id': 'turn-stale', 'connection_epoch': 0,
                                 'response_epoch': 1, 'text': 'list slots'}))
        error = _until(ws, lambda m: m['type'] == 'error', log)['payload']
        ended = _until(ws, lambda m: m['type'] == 'ended', log)['payload']
    assert error['code'] == 'stale_epoch' and error['terminal'] is True
    assert ended['outcome'] == 'protocol_error'
    evidence = _evidence(client, session_id)
    assert evidence['session']['terminal_outcome'] == 'protocol_error'
    assert evidence['turns'] == []
    assert evidence['task_journal']['original_operations'] == []
    assert egress_guard == []


def test_authority_refuses_tool_callback_from_superseded_epoch(tmp_path):
    """what_bug_this_catches (fault matrix: stale epoch callback at the effect boundary): a
    late tool result from a cancelled response (old epoch) mutating task state after the
    relay advanced the fence (TaskAuthority.set_epoch then execute with the old epoch)."""
    authority = TaskAuthority(tmp_path / 'task.sqlite')
    try:
        authority.execute('trial-fence', 'list_slots', {}, logical_turn_id='turn-1', response_epoch=0)
        authority.set_epoch('trial-fence', 1)
        stale = authority.execute('trial-fence', 'select_slot', {'slot_id': 'slot-a'},
                                  logical_turn_id='turn-2', response_epoch=0)
        assert stale['ok'] is False and stale['code'] == 'stale_epoch'
        assert authority.snapshot('trial-fence')['state'] == 'slots_offered'
        with pytest.raises(ValueError, match='stale_epoch'):
            authority.set_epoch('trial-fence', 0)
    finally:
        authority.close()
