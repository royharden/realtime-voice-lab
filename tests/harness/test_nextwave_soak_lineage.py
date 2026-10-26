"""Retained-evidence regressions from independent D10 Medium source review.

Reuse the synthetic build/claim/process boundaries; no real process or listener.
The evidence assertions below exercise the actual factory and EvidenceSink.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from test_nextwave_soak_capability import boundaries, inputs, prepare
from test_nextwave_soak_capability import (
    test_local_producer_persists_exact_hash_lineage_and_cleanup_status as run_producer_control,
)


def test_retained_public_manifest_tamper_invalidates_capability(boundaries, inputs):
    """what_bug_this_catches: retained manifest identity changing while capability stays valid."""
    module, _ = boundaries
    spec = prepare(module, inputs)
    manifest = inputs['root'] / 'fresh-run' / 'local-synthetic-manifest.json'
    value = json.loads(manifest.read_bytes())
    value['run_id'] = 'synthetic-contradictory-run'
    manifest.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError):
        spec['_local_synthetic'].validate(spec)


def test_forced_shutdown_reason_remains_in_actual_driver_evidence(boundaries, inputs, monkeypatch):
    """what_bug_this_catches: a closed projection dropping the required incomplete reason."""
    run_producer_control(boundaries, inputs, monkeypatch, incomplete=False, forced=True)
    path = inputs['root'] / 'fresh-run' / 'observations' / 'driver.jsonl'
    events = [json.loads(raw) for raw in path.read_bytes().splitlines()]
    terminal = events[-1]
    assert terminal['kind'] == 'driver_ended' and terminal['completed'] is False
    assert terminal['reason'] == 'service_restart'
    summary = json.loads((path.parent / 'local-summary.json').read_bytes())
    assert 'owned_relay_shutdown_incomplete' in summary['incomplete']


def controlled_failure(boundaries, inputs, monkeypatch, stage):
    """Actual factory/sink with explicit failed-start/tamper boundaries, no OS process."""
    from voicelab.harness import browser, soak
    module, _ = boundaries
    state = SimpleNamespace(stopped=False, spec=None, workload_calls=0)

    class Process:
        pid = None
        returncode = exitcode = None

        def start(self):
            self.pid = 999999  # test metadata only, never an OS process target

        def is_alive(self):
            return self.pid is not None and self.exitcode is None

        def join(self, timeout):
            self.exitcode = self.returncode = 0

        def terminate(self):
            self.exitcode = self.returncode = -15

    process = Process()
    def stop():
        state.stopped = True
    context = SimpleNamespace(Event=lambda: SimpleNamespace(set=stop),
                              Process=lambda **kwargs: process)
    monkeypatch.setattr(module.multiprocessing, 'get_context', lambda kind: context)
    original = module.prepare_local_synthetic
    def prepare_recorded(*args):
        state.spec = original(*args)
        return state.spec
    monkeypatch.setattr(module, 'prepare_local_synthetic', prepare_recorded)

    def tamper():
        path = inputs['root'] / 'fresh-run' / 'local-synthetic-manifest.json'
        value = json.loads(path.read_bytes())
        value['run_id'] = 'synthetic-changed-after-preparation'
        path.write_text(json.dumps(value), encoding='utf-8')

    def diagnostic(spec, nonce, path):
        if stage == 'startup':
            raise ValueError('synthetic-startup-refusal')
        if path == '/health':
            return {'mode': 'mock', 'protocol_version': 1}
        if path == '/admission':
            if stage == 'before_workload':
                tamper()
            return {'arms': {arm: {'run_id': spec['run_id']} for arm in ('V', 'C')}}
        pytest.fail('unexpected diagnostic path')
    monkeypatch.setattr(browser, 'own_diagnostic', diagnostic)

    async def workload(*args):
        state.workload_calls += 1
        if stage == 'after_workload':
            tamper()
        return {'references': {}, 'summary': {'full_gate_pass': False, 'product_acceptance': False},
                'incomplete': []}
    monkeypatch.setattr(soak, 'run_soak', workload)
    args = SimpleNamespace(out=inputs['root'] / 'fresh-run', port=22341,
        port_claim=inputs['receipt'], web_dist=inputs['build'], web_manifest=inputs['build_record'],
        profile='short')
    result, error = None, None
    try:
        result = module.run_local_synthetic(args)
    except ValueError as exc:
        error = exc
    assert state.stopped and process.exitcode is not None
    return state, result, error


def test_startup_without_summary_keeps_canonical_failure_reason(boundaries, inputs, monkeypatch):
    """what_bug_this_catches: no-summary startup refusal losing its reason or claiming completion."""
    state, _, error = controlled_failure(boundaries, inputs, monkeypatch, 'startup')
    assert error is not None and str(error) == 'synthetic-startup-refusal'
    assert state.workload_calls == 0
    path = inputs['root'] / 'fresh-run' / 'observations' / 'driver.jsonl'
    terminal = json.loads(path.read_bytes().splitlines()[-1])
    assert terminal['kind'] == 'driver_ended' and terminal['completed'] is False
    assert terminal['reason'] == 'bounded_driver_step_failed'


@pytest.mark.parametrize('stage', ['before_workload', 'after_workload'])
def test_tamper_during_start_or_finalization_cannot_leave_completed_evidence(boundaries, inputs, monkeypatch, stage):
    """what_bug_this_catches: validating only once before process start, then saving a false pass."""
    state, result, error = controlled_failure(boundaries, inputs, monkeypatch, stage)
    assert state.workload_calls == (1 if stage == 'after_workload' else 0)
    assert error is not None or result is not None and result.get('incomplete')
    path = inputs['root'] / 'fresh-run' / 'observations' / 'handoff.json'
    handoff = json.loads(path.read_bytes())
    assert handoff['driver_completed'] is False and handoff['product_acceptance'] is False
