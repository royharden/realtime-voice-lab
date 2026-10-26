"""Actual observer boundary controls with synthetic page/CDP/HTTP responses only."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from voicelab.harness import observations


def snapshot():
    return {'schema_version': 1, 'owned_handles': {'socket': 1, 'graph': 1},
            'observation_statuses': {'owned_handles': 'observed_api_state',
                'cleanup': 'not_released', 'playback_queue': 'estimated', 'upload_queue': 'estimated'},
            'queue_seconds': {'playback': .2, 'upload': .1},
            'worklet': {'status': 'observed', 'queued_frames': 480}}


def sample(monkeypatch, *, change_snapshot=None, metric=None, value=None, heap=1000,
           rate=48000, pages=1, cdps=1):
    state = snapshot()
    if change_snapshot:
        change_snapshot(state)

    async def evaluate(*args):
        return deepcopy(state)

    async def send(*args):
        return {'metrics': [{'name': 'JSHeapUsedSize', 'value': heap}]}

    metrics = {key: {'value': 1, 'observation': 'observed'} for key in
               ('rss_bytes', 'open_sockets', 'open_files', 'asyncio_tasks', 'event_loop_lag_ms')}
    if metric:
        metrics[metric]['value'] = value
    monkeypatch.setattr(observations, 'own_diagnostic', lambda *args: {
        'scope': 'own_server_process_only', 'boot_id': 'synthetic-boot', 'metrics': metrics})
    return asyncio.run(observations.resource_sample(
        [SimpleNamespace(evaluate=evaluate) for _ in range(pages)],
        [SimpleNamespace(send=send) for _ in range(cdps)], {}, 'test-only',
        after_warmup=True, sample_rates=[rate] * pages))


def test_resource_observer_positive_control_retains_actual_components(monkeypatch):
    """what_bug_this_catches: a vacuous unknown-only observer or swapped queue units."""
    row = sample(monkeypatch)
    assert row['rss_bytes'] == 1 and row['browser_heap_bytes'] == 1000
    assert row['owned_handles'] == 2 and row['queue_seconds'] == .2
    assert row['worklet_queue_seconds'] == .01
    assert row['server_observations']['boot_id'] == 'synthetic-boot'


@pytest.mark.parametrize('bad', [None, True, -1, float('nan'), float('inf'), '1'])
@pytest.mark.parametrize('metric, field', [('rss_bytes', 'rss_bytes'),
                                         ('event_loop_lag_ms', 'event_loop_lag_ms')])
def test_unknown_or_invalid_server_numeric_metric_is_not_observed(monkeypatch, bad, metric, field):
    """what_bug_this_catches: NaN/bool/negative/string instrumentation reaching a pass gate."""
    assert sample(monkeypatch, metric=metric, value=bad)[field] is None


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf'), '1'])
def test_invalid_heap_stays_unknown(monkeypatch, bad):
    """what_bug_this_catches: invalid browser heap data being retained as complete."""
    assert sample(monkeypatch, heap=bad)['browser_heap_bytes'] is None


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf')])
def test_invalid_sample_rate_never_converts_worklet_frames(monkeypatch, bad):
    """what_bug_this_catches: invalid sample rates producing false finite queue coverage."""
    assert sample(monkeypatch, rate=bad)['worklet_queue_seconds'] is None


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf')])
def test_invalid_app_queue_stays_unknown(monkeypatch, bad):
    """what_bug_this_catches: max() hiding an invalid queue behind a normal peer queue."""
    row = sample(monkeypatch, change_snapshot=lambda state: state['queue_seconds'].update(upload=bad))
    assert row['queue_seconds'] is None


@pytest.mark.parametrize('pages, cdps', [(2, 1), (1, 2)])
def test_resource_sampling_never_silently_zips_away_contexts(monkeypatch, pages, cdps):
    """what_bug_this_catches: zip truncation reporting complete metrics for unsampled contexts."""
    try:
        row = sample(monkeypatch, pages=pages, cdps=cdps)
    except ValueError:
        return  # explicit structural refusal also preserves incomplete status
    assert row['browser_heap_bytes'] is None or row['owned_handles'] is None


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf')])
def test_diagnostic_queue_event_nonfinite_or_boolean_remains_unknown(bad):
    """what_bug_this_catches: malformed event-level maxima silently collapsing to zero."""
    cursor = observations.DiagnosticCursor()
    cursor.sample_rate = 48000
    batch = {'schema_version': 1, 'cursor': 1, 'latest_seq': 1,
             'dropped_before_cursor': 0, 'events': [
                 {'kind': 'worklet', 'detail': {'queued_frames': bad}}]}

    async def evaluate(*args):
        return batch

    sink = SimpleNamespace(emit=lambda *args, **kwargs: None)
    asyncio.run(cursor.drain(SimpleNamespace(evaluate=evaluate), sink, 'synthetic-only'))
    assert cursor.queue_unknown is True
    assert cursor.queue_events == 0


def test_cleanup_requires_observed_nonboolean_handle_counts():
    """what_bug_this_catches: unknown/empty/bool handles being reported as released zero."""
    valid = snapshot()
    valid['owned_handles'] = {'socket': 0, 'graph': 0}
    assert observations.cleanup_count(valid) == 0
    for handles in ({}, {'socket': False}, {'socket': -1}):
        assert observations.cleanup_count({**valid, 'owned_handles': handles}) is None
    valid['observation_statuses']['owned_handles'] = 'unknown'
    assert observations.cleanup_count(valid) is None
