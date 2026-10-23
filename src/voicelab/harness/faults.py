"""Consumer of D4's mock-only fault producer; a receipt alone is never effect proof."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

from .browser import server_evidence, wire_metadata


class MockControls:
    def __init__(self, spec: dict, nonce: str, sink):
        self.spec, self.nonce, self.sink = spec, nonce, sink
        self.receipts: dict[tuple[str, str, str], dict] = {}

    def _post(self, task: dict, kind: str) -> dict:
        policy = self.spec.get('mock_fault_policy', {})
        if type(policy.get('seed')) is not int or not policy.get('authorization_reference'):
            raise ValueError('operator mock fault policy pin required')
        session = task.get('_session_id')
        if not isinstance(session, str) or not re.fullmatch(r's-[a-f0-9]{24}', session):
            raise ValueError('observed owned mock session required')
        identifier = 'ctl-' + hashlib.sha256(
            f'{self.spec["run_id"]}:{task["task_id"]}:{task.get("_logical_turn_id")}:{kind}'.encode()).hexdigest()[:24]
        body = json.dumps({'control_id': identifier, 'kind': kind}).encode()
        relay, front = urlsplit(self.spec['relay_url']), urlsplit(self.spec['frontend_url'])
        url = urlunsplit(('http', relay.netloc, f'/sessions/{session}/faults', '', ''))
        request = Request(url, data=body, method='POST', headers={
            'Content-Type': 'application/json', 'X-Voicelab-Nonce': self.nonce,
            'Origin': urlunsplit((front.scheme, front.netloc, '', '', ''))})
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise ValueError('mock fault redirect refused')
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('fault receipt response bound')
        receipt = json.loads(raw)
        if (receipt.get('control_id') != identifier or receipt.get('kind') != kind
                or receipt.get('simulation') is not True or receipt.get('session_id') != session
                or receipt.get('run_id') != task.get('_server_run_id')
                or receipt.get('trial_id') != task.get('_trial_namespace')
                or receipt.get('seed') != policy['seed']
                or receipt.get('status') not in {'armed', 'applied', 'unresolved'}):
            raise ValueError('fault receipt identity/policy mismatch')
        return receipt

    async def arm(self, task: dict, kind: str) -> dict:
        key = (task['task_id'], task['_logical_turn_id'], kind)
        if key not in self.receipts:
            receipt = await asyncio.to_thread(self._post, task, kind)
            self.receipts[key] = receipt
            self.sink.emit('fault_control_receipt', task_id=task['task_id'],
                logical_turn_id=task.get('_logical_turn_id'),
                receipt=wire_metadata(receipt), receipt_is_not_effect_proof=True)
        return self.receipts[key]

    async def observe(self, task: dict, kind: str) -> dict:
        receipt = await self.arm(task, kind)
        evidence = await server_evidence(self.spec, self.nonce, task, terminal=kind == 'service_restart')
        return {'receipt': receipt, 'evidence': evidence, 'simulation': True}


def effect_observation(kind: str, proof: dict) -> tuple[str, str]:
    if proof.get('receipt', {}).get('status') == 'unresolved':
        return 'unknown', 'mock_fault_control_unresolved'
    evidence = proof.get('evidence', {})
    if kind == 'service_restart':
        if evidence.get('session', {}).get('terminal_outcome') == 'service_restart':
            return 'observed', 'simulated_session_service_restart; no process restarted'
    if kind == 'fresh_tool_duplicate':
        journal = evidence.get('task_journal', {})
        duplicates = [row for row in journal.get('attempts', []) if row.get('duplicate') == 1]
        if duplicates and journal.get('task', {}).get('booking') and journal.get('truncated') is False:
            return 'observed', 'duplicate_authority_attempt_and_single_booking_observed; parser boundary in receipt'
    return 'unknown', 'fault_receipt_present_but_actual_effect_unproven'
