"""INT addition001: protected metadata aliases share the bounded evidence read policy."""
import pytest
from fastapi.testclient import TestClient

from voicelab.contracts import validate_http_response
from voicelab.server.app import Settings, create_app

NONCE = 'metadata-public-nonce-0123456789abcdef'
ORIGIN = 'http://127.0.0.1:22301'


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(nonce=NONCE, allowed_origin=ORIGIN,
                                      artifact_root=tmp_path / 'runtime')),
                    base_url=ORIGIN) as value:
        yield value


def headers(**overrides):
    result = {'origin': ORIGIN, 'x-voicelab-nonce': NONCE}
    result.update(overrides)
    return {k: v for k, v in result.items() if v is not None}


@pytest.mark.parametrize('route', ['admission', 'diagnostics'])
def test_metadata_alias_matches_canonical_get(client, route):
    """what_bug_this_catches: browser POST aliases drifting from the canonical GET registry."""
    response = client.post('/' + route, content=b'{}', headers=headers())
    assert response.status_code == 200
    canonical = client.get('/' + route, headers=headers()).json()
    validate_http_response(route, canonical)
    if route == 'diagnostics':
        # Two requests observe different clocks/resources; compare their static contract.
        assert {k: v for k, v in response.json().items() if k not in {'metrics', 'timestamp_ns'}} == {
            k: v for k, v in canonical.items() if k not in {'metrics', 'timestamp_ns'}}
        assert response.json()['metrics'].keys() == canonical['metrics'].keys()
    else:
        assert response.json() == canonical
    validate_http_response(route, response.json())
    assert nonce_absent(response)


def nonce_absent(response):
    return NONCE not in response.text


@pytest.mark.parametrize('route', ['admission', 'diagnostics'])
@pytest.mark.parametrize('override', [
    {'origin': None}, {'origin': 'http://localhost:22301'},
    {'origin': 'https://foreign.example'}, {'x-voicelab-nonce': None},
    {'x-voicelab-nonce': 'wrong-public-nonce-0123456789'},
])
def test_metadata_alias_rejects_wrong_boundary(client, route, override):
    """what_bug_this_catches: alias metadata leaking through a weaker Origin/nonce boundary."""
    response = client.post('/' + route, content=b'{}', headers=headers(**override))
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'boundary_refused'


@pytest.mark.parametrize('route', ['admission', 'diagnostics'])
@pytest.mark.parametrize('body,status', [(b'', 400), (b'{ }', 400), (b'[]', 400),
    (b'{"path":"arbitrary"}', 400), (b' ' * 33, 413)])
def test_metadata_alias_body_is_exact_and_bounded(client, route, body, status):
    """what_bug_this_catches: arbitrary metadata filters or unbounded bodies on the aliases."""
    response = client.post('/' + route, content=body, headers=headers())
    assert response.status_code == status
    assert nonce_absent(response)


@pytest.mark.parametrize('route', ['admission', 'diagnostics'])
def test_metadata_alias_rejects_foreign_host(client, route):
    """what_bug_this_catches: DNS rebinding bypassing an otherwise correct metadata POST."""
    response = client.post('http://foreign.example/' + route, content=b'{}', headers=headers())
    assert response.status_code == 403


@pytest.mark.parametrize('route', ['admission', 'diagnostics'])
def test_metadata_alias_bounds_slow_body(client, route, monkeypatch):
    """what_bug_this_catches: a slow POST body holding the metadata reader indefinitely."""
    import asyncio
    from starlette.requests import Request

    async def stalled_stream(self):
        await asyncio.sleep(3)
        yield b'{}'

    monkeypatch.setattr(Request, 'stream', stalled_stream)
    response = client.post('/' + route, content=b'{}', headers=headers())
    assert response.status_code == 408


@pytest.mark.parametrize('route', ['admission', 'diagnostics'])
def test_metadata_alias_rejects_ambiguous_headers(client, route):
    """what_bug_this_catches: duplicate boundary headers interpreted differently by layers."""
    response = client.post('/' + route, content=b'{}', headers=[('origin', ORIGIN),
        ('origin', ORIGIN), ('x-voicelab-nonce', NONCE)])
    assert response.status_code == 403
