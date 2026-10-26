"""Focused independent controls for the exact open_files projection-key restoration.

Shared public helpers are pinned to test commit80dd4dfd05a741f1df25d0ef7f17585dd1c71919.
That file is unchanged. Malformed count DTOs must stop at the existing canonical
HTTP validator; the global metadata projector is not a replacement DTO parser.
All paths/content below are invented sentinels and are never opened or resolved.
"""
from __future__ import annotations

import _thread
import asyncio
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from test_nextwave_no_probe_diagnostics import (
    DISABLED, NONCE, ORIGIN, UNITS, assert_disabled, headers, make_app, no_resources,
)


SENTINEL = 'PUBLIC-TEST-PRIVATE-CONTENT-SENTINEL'
FILE_SENTINEL = '/never-read/' + SENTINEL + '.pcm'


@pytest.fixture
def zero_resources(no_resources, monkeypatch):
    """Extend the pinned pre-effect traps to direct low-level thread APIs."""
    def forbidden(*args, **kwargs):
        no_resources.calls.append('direct_thread_start')
        pytest.fail('no direct low-level thread may start')

    for module, name in ((_thread, 'start_new_thread'), (_thread, 'start_joinable_thread'),
                         (threading, '_start_new_thread')):
        if hasattr(module, name):
            monkeypatch.setattr(module, name, forbidden)
    return no_resources


def persist(root, envelope):
    """Exercise both actual EvidenceSink write paths, then inspect persisted bytes."""
    from voicelab.harness.evidence import EvidenceSink
    sink = EvidenceSink(root, {'run_id': 'no-probe-projection', 'mode': 'mock',
                               '_manifest_hash': '0' * 64})
    try:
        sink.emit('server_observation', **envelope)
        sink.artifact('projection.json', envelope, 'server_diagnostics')
    finally:
        sink.close(completed=False, reason='incomplete_observation_coverage')
    artifact_bytes = (root / 'projection.json').read_bytes()
    row_bytes = (root / 'driver.jsonl').read_bytes().splitlines()[0]
    return json.loads(artifact_bytes), json.loads(row_bytes), artifact_bytes + row_bytes


@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_actual_three_rows_survive_http_projection_sink_and_unknown_consumer(
        zero_resources, monkeypatch, tmp_path, method):
    """what_bug_this_catches: required count row/reason dropped or unknown resources pass."""
    from voicelab.contracts import validate_http_response
    from voicelab.harness import observations
    from voicelab.harness.privacy import observed_metadata
    from voicelab.reporting.diagnostics import soak_report

    async def run():
        app = make_app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=ORIGIN) as client:
                response = await client.request(method, '/diagnostics', headers=headers(),
                    content=b'{}' if method == 'POST' else None)
                assert response.status_code == 200
                assert NONCE not in response.text
                raw = response.json()
                validate_http_response('diagnostics', raw)
                assert_disabled({name: raw['metrics'][name] for name in UNITS})
                projected = observed_metadata(raw)
                assert_disabled({name: projected['metrics'][name] for name in UNITS})
                artifact, event, saved = persist(tmp_path / 'sink', {'server_observations': raw})
                for envelope in (artifact, event):
                    assert_disabled({name: envelope['server_observations']['metrics'][name]
                                     for name in UNITS})
                assert NONCE.encode() not in saved

        snapshot = {'schema_version': 1, 'owned_handles': {'socket': 0, 'graph': 0},
            'observation_statuses': {'owned_handles': 'observed_api_state',
                'cleanup': 'observed_released', 'playback_queue': 'observed_empty',
                'upload_queue': 'observed_empty'},
            'queue_seconds': {'playback': 0, 'upload': 0}}

        async def evaluate(*args):
            return deepcopy(snapshot)

        async def send(*args):
            return {'metrics': [{'name': 'JSHeapUsedSize', 'value': 1000}]}

        async def inline(function, *args, **kwargs):
            assert function is observations.own_diagnostic
            return function(*args, **kwargs)

        monkeypatch.setattr(observations, 'own_diagnostic', lambda *args, **kwargs: deepcopy(projected))
        monkeypatch.setattr(asyncio, 'to_thread', inline)
        row = await observations.resource_sample(
            [SimpleNamespace(evaluate=evaluate) for _ in range(3)],
            [SimpleNamespace(send=send) for _ in range(3)], {}, NONCE,
            after_warmup=True, sample_rates=[48000] * 3)
        assert all(row[name] is None for name in ('rss_bytes', 'sockets', 'files'))
        assert row['browser_heap_bytes'] == 1000 and row['owned_handles'] == 0
        # Supplied development counts isolate missing resources; they are not
        # observations of executed workload or a product acceptance claim.
        report = soak_report([row], {'aggregate_turns': 200, 'interruptions': 40,
            'reconnects': 10, 'contexts': 3, 'fourth_session_refused': True,
            'coverage_actions_are_attempts': False})
        assert report['coverage_complete'] is True
        assert report['resource_gate_pass'] is False and report['passed'] is False
        assert 'resource series incomplete; missing instrumentation remains unknown' in report['failures']
    asyncio.run(run())


def test_nested_sibling_and_sensitive_payloads_do_not_persist_around_count_dto(
        zero_resources, tmp_path):
    """what_bug_this_catches: field-key repair turning nested unknown/content data into evidence."""
    from voicelab.harness.privacy import observed_metadata

    async def run():
        actual = await zero_resources.sampler().sample()
        assert_disabled(actual)
        dirty = deepcopy(actual)
        for row in dirty.values():
            row.update(filename=FILE_SENTINEL, content={'path': FILE_SENTINEL},
                opaque_metric_payload={'path': FILE_SENTINEL},
                headers={'authorization': SENTINEL}, text=SENTINEL,
                transcript=SENTINEL, pcm16=SENTINEL, cookie=SENTINEL)
        envelope = {'server_observations': {'metrics': dirty},
            'filename': FILE_SENTINEL, 'content': {'path': FILE_SENTINEL},
            'opaque_sibling': {'open_files': FILE_SENTINEL},
            'payload': {'metrics': deepcopy(dirty), 'filename': FILE_SENTINEL,
                'content': {'path': FILE_SENTINEL}, 'opaque_nested': {'path': FILE_SENTINEL},
                'reason': SENTINEL, 'authorization': SENTINEL,
                'detail': {'metrics': deepcopy(dirty), 'nonce': SENTINEL,
                    'headers': {'cookie': SENTINEL}, 'transcript': SENTINEL}},
            'reason': SENTINEL, 'nonce': SENTINEL, 'credentials': SENTINEL}
        projected = observed_metadata(envelope)
        artifact, event, saved = persist(tmp_path / 'nested-sink', envelope)
        for candidate in (projected, artifact, event):
            for container in (candidate['server_observations'], candidate['payload'],
                              candidate['payload']['detail']):
                assert_disabled(container['metrics'])
            assert 'reason' not in candidate and 'reason' not in candidate['payload']
        assert SENTINEL.encode() not in saved
        assert FILE_SENTINEL.encode() not in saved
        assert SENTINEL not in json.dumps(projected)
    asyncio.run(run())


BAD_METRICS = [
    ('filename-extra', {'filename': FILE_SENTINEL}),
    ('path-extra', {'path': FILE_SENTINEL}),
    ('content-extra', {'content': {'text': SENTINEL}}),
    ('unknown-extra', {'unregistered_file_data': {'path': FILE_SENTINEL}}),
    ('sensitive-extra', {'authorization': SENTINEL}),
    ('string-value', {'value': FILE_SENTINEL}),
    ('object-value', {'value': {'path': FILE_SENTINEL}}),
    ('list-value', {'value': [FILE_SENTINEL]}),
    ('numeric-string-value', {'value': '123'}),
    ('path-row', FILE_SENTINEL),
    ('filename-list-row', [FILE_SENTINEL]),
]


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('label,attack', BAD_METRICS, ids=[row[0] for row in BAD_METRICS])
def test_existing_canonical_http_validation_refuses_malicious_open_files_shape(
        zero_resources, monkeypatch, tmp_path, method, label, attack):
    """what_bug_this_catches: admitting arbitrary file data under the newly retained key.

    Path is legitimate metadata elsewhere. Inside a metric it is an extra field;
    string/object/list metric values are invalid at the canonical HTTP boundary.
    This tests that boundary rather than inventing a global path-redaction rule.
    """
    from voicelab.server import metrics
    original = metrics.OwnProcessSampler.sample

    async def malicious(owner):
        rows = await original(owner)
        assert_disabled(rows)
        if isinstance(attack, dict):
            rows['open_files'].update(deepcopy(attack))
        else:
            rows['open_files'] = deepcopy(attack)
        return rows

    monkeypatch.setattr(metrics.OwnProcessSampler, 'sample', malicious)

    async def run():
        app = make_app(tmp_path)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url=ORIGIN) as client:
                response = await client.request(method, '/diagnostics', headers=headers(),
                    content=b'{}' if method == 'POST' else None)
                assert response.status_code == 500, label
                assert response.json() == {'detail': {'code': 'http_response_contract_violation'},
                                           'error': {'code': 'http_response_contract_violation'}}
                assert SENTINEL not in response.text and NONCE not in response.text
                artifact, event, saved = persist(tmp_path / 'refusal-sink',
                    {'server_observations': response.json()})
                assert SENTINEL.encode() not in saved and FILE_SENTINEL.encode() not in saved
                assert 'metrics' not in artifact['server_observations']
                assert 'metrics' not in event['server_observations']
    asyncio.run(run())


def test_unallowlisted_reason_is_filtered_without_expanding_canonical_reason_enum(
        zero_resources, tmp_path):
    """what_bug_this_catches: broad reason allowlist bypass when adding one fixed literal."""
    from voicelab.harness.privacy import CONTROL_VALUES, observed_metadata
    assert DISABLED in CONTROL_VALUES['reason']
    assert SENTINEL not in CONTROL_VALUES['reason']

    async def run():
        rows = await zero_resources.sampler().sample()
        assert_disabled(rows)
        rows['open_files']['reason'] = SENTINEL
        envelope = {'server_observations': {'metrics': rows}}
        projected = observed_metadata(envelope)
        artifact, event, saved = persist(tmp_path / 'reason-sink', envelope)
        for candidate in (projected, artifact, event):
            metrics = candidate['server_observations']['metrics']
            assert 'open_files' in metrics
            assert metrics['open_files'] == {'value': None, 'observation': 'unknown', 'unit': 'count'}
            for name in ('rss_bytes', 'open_sockets'):
                assert metrics[name]['reason'] == DISABLED
        assert SENTINEL.encode() not in saved
    asyncio.run(run())
