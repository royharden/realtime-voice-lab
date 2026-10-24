"""Local HTTP boundary on the protected evidence POST (plan W4; D4-R5 browser evidence seam).

NOT YET RUN. TestClient drives the ASGI app in-process (no listener, no port) in mock mode.
Requires httpx for fastapi.testclient (requested as a test extra).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from voicelab.server.app import Settings, create_app

NONCE = 'regression-nonce-0123456789abcdef'
ORIGIN = 'http://127.0.0.1:5173'
BASE = 'http://127.0.0.1:8000'
PATH = '/sessions/unknown-session/evidence'


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(nonce=NONCE, allowed_origin=ORIGIN, artifact_root=tmp_path / 'runtime'))
    with TestClient(app, base_url=BASE) as test_client:  # context runs lifespan (journal init)
        yield test_client


def _headers(**overrides) -> dict:
    headers = {'origin': ORIGIN, 'x-voicelab-nonce': NONCE}
    headers.update(overrides)
    return {key: value for key, value in headers.items() if value is not None}


def _refused(response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json() == {'detail': {'code': code}, 'error': {'code': code}}


def test_wrong_host_refused_before_routing(client):
    """what_bug_this_catches: DNS-rebinding - a non-loopback Host reaching the relay. The
    loopback_host_only middleware must refuse before any route or body handling."""
    _refused(client.post('http://attacker.example' + PATH, content=b'{}', headers=_headers()),
             403, 'boundary_refused')


@pytest.mark.parametrize('overrides', [
    {'origin': None}, {'origin': 'http://127.0.0.1:9999'}, {'origin': 'http://attacker.example'},
    {'x-voicelab-nonce': None}, {'x-voicelab-nonce': 'wrong-nonce-0123456789abcdef'},
    {'x-voicelab-nonce': 'short'},
], ids=['missing-origin', 'other-loopback-origin', 'foreign-origin', 'missing-nonce',
        'wrong-nonce', 'malformed-nonce'])
def test_origin_and_nonce_are_required_exactly(client, overrides):
    """what_bug_this_catches: cross-site or same-machine pages reading session evidence. Origin
    must equal the configured frontend origin and the nonce must match (security.validate_boundary)."""
    _refused(client.post(PATH, content=b'{}', headers=_headers(**overrides)), 403, 'boundary_refused')


def test_over_size_body_refused(client):
    """what_bug_this_catches: an unbounded request body read on the evidence POST. Bodies over
    32 bytes are refused with a fixed code before any evidence is read."""
    _refused(client.post(PATH, content=b'{' + b' ' * 40 + b'}', headers=_headers()),
             413, 'evidence_read_body_bound')


@pytest.mark.parametrize('body', [b'', b'{ }', b'{"a":1}', b'[]', b'null'])
def test_only_exact_empty_object_body_accepted(client, body):
    """what_bug_this_catches: the POST accepting parameters (filters, paths, counters) and
    becoming a second evidence API. Only the exact two bytes '{}' are allowed."""
    _refused(client.post(PATH, content=body, headers=_headers()), 400,
             'evidence_read_empty_object_required')


def test_valid_boundary_reaches_canonical_reader(client):
    """what_bug_this_catches: an over-broad boundary that refuses valid requests. With exact
    Host/Origin/nonce and body, the request reaches the canonical reader (unknown session ->
    404 with a fixed code; the client-supplied id is never echoed)."""
    response = client.post(PATH, content=b'{}', headers=_headers())
    _refused(response, 404, 'request_refused')
    assert 'unknown-session' not in response.text


def test_get_evidence_applies_the_same_boundary(client):
    """what_bug_this_catches: protecting POST while the GET reader stays open."""
    _refused(client.get(PATH, headers=_headers(origin=None)), 403, 'boundary_refused')
    _refused(client.get(PATH, headers=_headers()), 404, 'request_refused')
