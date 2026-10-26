"""Retained-evidence regressions from independent D10 Medium source review.

Reuse the synthetic build/claim/process boundaries; no real process or listener.
The evidence assertions below exercise the actual factory and EvidenceSink.
"""
from __future__ import annotations

import json

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
    assert isinstance(terminal.get('reason'), str) and terminal['reason'].strip()
