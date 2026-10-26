"""Actual installed CLI → loopback websocket → durable evidence, public synthetic text only."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest
from websockets.asyncio.client import connect


@pytest.fixture
def relay(tmp_path):
    raw = os.environ.get('VOICELAB_TEST_PORT', '')
    receipt = os.environ.get('VOICELAB_TEST_PORT_CLAIM', '')
    if not raw or not receipt or not os.environ.get('VOICELAB_PORT_CHECKED'):
        pytest.skip('operator agent-ports claim/check required before CLI listener')
    assert raw.isdigit() and 22300 <= int(raw) <= 22399
    assert Path(receipt).is_file()
    origin = f'http://127.0.0.1:{raw}'
    nonce = 'public-synthetic-cli-nonce-0123456789'
    command = [sys.executable, '-m', 'voicelab.cli', 'serve', '--mode', 'mock',
               '--port', raw, '--port-claim', receipt, '--allowed-origin', origin,
               '--run-id', 'rv1-public-cli', '--artifact-root', str(tmp_path / 'runtime')]
    # An explicit minimal environment prevents inherited provider keys entering the child.
    env = {k: v for k, v in os.environ.items() if k.upper() in
           {'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'USERPROFILE', 'LOCALAPPDATA', 'PYTHONPATH'}}
    env['VOICELAB_NONCE'] = nonce
    with (tmp_path / 'relay.log').open('wb') as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, 'CLI exited before health became ready'
                try:
                    response = httpx.get(origin + '/health', timeout=1, trust_env=False)
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, 'CLI health startup deadline'
                time.sleep(0.1)
            yield origin, nonce
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


@pytest.mark.parametrize('arm', ['V', 'C', 'K'])
def test_cli_keyless_five_turn_task_and_evidence(relay, arm):
    """what_bug_this_catches: CLI composition works in-process but fails over real sockets,
    drops an arm, loses durable evidence, or treats mock transport as independent speech truth.
    This exercises debug-text mocks, not microphone speech or provider parsers.
    """
    origin, nonce = relay
    messages = []

    async def journey():
        async with connect(origin.replace('http:', 'ws:') + '/ws?nonce=' + nonce,
                           origin=origin) as ws:
            async def until(predicate):
                for _ in range(96):
                    message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    messages.append(message)
                    assert message['type'] != 'error', message.get('payload', {}).get('code')
                    if predicate(message):
                        return message
                raise AssertionError('relay message bound exceeded')

            await ws.send(json.dumps({'type': 'hello', 'protocol_version': 1,
                                      'mode': 'mock', 'arm': arm, 'nonce': nonce}))
            ready = await until(lambda m: m['type'] == 'ready')
            await until(lambda m: m['type'] == 'state' and m['payload']['state'] == 'listening')
            tasks = []
            for index, text in enumerate(('hours', 'list slots', 'select slot 1', 'yes confirm', 'status')):
                await ws.send(json.dumps({'type': 'commit', 'turn_id': f'cli-{index}',
                                          'connection_epoch': 0, 'response_epoch': 0, 'text': text}))
                samples, response_id = 0, None
                for _ in range(96):
                    message = json.loads(await asyncio.wait_for(ws.recv(), 5))
                    messages.append(message)
                    payload = message['payload']
                    assert message['type'] != 'error', payload.get('code')
                    if message['type'] == 'audio':
                        samples += len(base64.b64decode(payload['pcm16'])) // 2
                        response_id = payload['response_id']
                    elif message['type'] == 'task':
                        tasks.append(payload)
                    elif message['type'] == 'event' and payload.get('kind') == 'response_done':
                        break
                else:
                    raise AssertionError('turn message bound exceeded')
                await until(lambda m: m['type'] == 'state' and m['payload']['state'] == 'playing')
                await ws.send(json.dumps({'type': 'rendered', 'response_id': response_id,
                                          'rendered_through_sample': samples}))
                await until(lambda m: m['type'] == 'state' and m['payload']['state'] == 'listening')
            await ws.send(json.dumps({'type': 'end'}))
            terminal = await until(lambda m: m['type'] == 'ended')
            return ready['session_id'], tasks, terminal['payload']

    session_id, tasks, terminal = asyncio.run(journey())
    assert len(tasks) == 5 and all(t['ok'] for t in tasks)
    assert tasks[3]['effect_committed'] is True
    assert terminal['outcome'] == 'success'
    response = httpx.post(origin + f'/sessions/{session_id}/evidence', content=b'{}',
                          headers={'origin': origin, 'x-voicelab-nonce': nonce},
                          timeout=5, trust_env=False)
    assert response.status_code == 200
    evidence = response.json()
    assert evidence['task_journal']['task']['booking']['slot_id'] == 'slot-a'
    assert len(evidence['session_attempts']) == (10 if arm == 'C' else 5)
    if arm == 'C':
        assert {row['component'] for row in evidence['session_attempts']} == {'stt', 'tts'}
    assert evidence['ledger']['known_cost'] == '0'
    assert evidence['runtime_verified'] is False
    assert terminal['spoken_correctness'] == 'unknown'
    assert nonce not in json.dumps(messages) + response.text


def test_cli_plan_preserves_schedule_and_refuses_overwrite(tmp_path):
    """what_bug_this_catches: CLI dropping K/fault denominators or overwriting a frozen plan."""
    families, speakers = [f'public-family-{i}' for i in range(8)], ['synthetic-a', 'synthetic-b']
    request = {'run_id': 'rv1-cli-plan', 'seed': 12, 'families': families, 'speakers': speakers,
               'network_profile': 'loopback', 'models': {a: f'mock-{a}' for a in 'VCK'},
               'fixture_cells': [{'family': f, 'speaker': s, 'fixture_id': f'{f}-{s}'}
                                 for f in families for s in speakers],
               'fault_schedules': {f: [{'kind': 'disconnect', 'turn_position': 2}] for f in families}}
    source, target = tmp_path / 'request.json', tmp_path / 'plan.json'
    source.write_text(json.dumps(request), encoding='utf-8')
    command = [sys.executable, '-m', 'voicelab.cli', 'trial-plan', '--request', str(source), '--out', str(target)]
    completed = subprocess.run(command, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode()
    original = target.read_bytes()
    plan = json.loads(original)
    assert len(plan['pairs']) == 48 and len(plan['tasks']) == 144
    assert len(plan['scheduled_turns']) == 1152
    assert sum(task['arm'] == 'K' for task in plan['tasks']) == 48
    assert subprocess.run(command, capture_output=True, timeout=20).returncode != 0
    assert target.read_bytes() == original
