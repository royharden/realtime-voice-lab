"""Later-activation transports. Importing this module opens no connections."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request
from pydantic import TypeAdapter
from voicelab.contracts import ProviderIdentityTag


class ProviderFailure(ValueError):
    def __init__(self, code: str):
        self.code = code if code in {'provider_timeout', 'provider_transport_error', 'provider_redirect_refused', 'provider_protocol_error'} else 'provider_transport_error'
        super().__init__(self.code)


def _identity(value: str) -> None:
    try:
        TypeAdapter(ProviderIdentityTag).validate_python(value)
    except ValueError:
        raise ProviderFailure('provider_protocol_error') from None


def allowed_endpoint(url: str, *, websocket: bool) -> str:
    try:
        if not isinstance(url, str) or len(url) > 512 or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        expected = '/v1/realtime' if websocket else '/v1/audio/speech'
        if (parsed.scheme != ('wss' if websocket else 'https') or parsed.hostname != 'api.openai.com'
                or parsed.netloc not in {'api.openai.com', 'api.openai.com:443'}
                or parsed.port not in (None, 443) or parsed.path != expected
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if websocket:
            if len(query) != 1:
                raise ValueError
            key, value = query[0]
            if key == 'model':
                _identity(value)
            elif (key, value) != ('intent', 'transcription'):
                raise ValueError
        elif query:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ProviderFailure('provider_protocol_error') from None
    return url


class ProviderTransport:
    def __init__(self):
        self.pending_openings = set()

    async def connect(self, component: str, model: str, credential: str):
        from websockets.asyncio.client import connect
        if component not in {'native', 'stt'}:
            raise ProviderFailure('provider_protocol_error')
        _identity(model)
        query = {'model': model} if component == 'native' else {'intent': 'transcription'}
        url = allowed_endpoint('wss://api.openai.com/v1/realtime?' + urlencode(query), websocket=True)
        class NoRedirectConnect(connect):
            # Pinned websockets14.1 source exposes this redirect decision seam.
            def process_redirect(self, exc):
                return ProviderFailure('provider_redirect_refused')

        logger = logging.Logger('voicelab.private_transport')
        logger.disabled = True
        logger.propagate = False
        try:
            return await NoRedirectConnect(url, additional_headers={'Authorization': 'Bearer ' + credential},
                                 open_timeout=5, close_timeout=2, max_size=131072, max_queue=8, logger=logger)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise ProviderFailure('provider_timeout') from None
        except Exception:
            raise ProviderFailure('provider_transport_error') from None

    async def speech(self, model: str, voice: str, text: str, credential: str):
        """Streaming raw PCM. Redirects are disabled to avoid credential forwarding."""
        from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise ProviderFailure('provider_redirect_refused')

        _identity(model)
        _identity(voice)
        if not isinstance(text, str) or not 1 <= len(text.encode('utf-8')) <= 8000:
            raise ProviderFailure('provider_protocol_error')
        url = allowed_endpoint('https://api.openai.com/v1/audio/speech', websocket=False)
        request = Request(url, data=json.dumps({'model': model, 'voice': voice, 'input': text,
            'response_format': 'pcm'}).encode(), headers={'Authorization': 'Bearer ' + credential,
            'Content-Type': 'application/json'}, method='POST')
        response = None
        opening = None
        try:
            opening = asyncio.create_task(asyncio.to_thread(
                build_opener(ProxyHandler({}), NoRedirect()).open, request, timeout=5))
            self.pending_openings.add(opening)
            response = await asyncio.shield(opening)
            self.pending_openings.discard(opening)
            pending = b''
            total = 0
            while True:
                chunk = await asyncio.to_thread(response.read, 4800)
                if not chunk:
                    break
                total += len(chunk)
                if total > 24000 * 2 * 10:
                    raise ProviderFailure('provider_protocol_error')
                pending += chunk
                aligned = len(pending) - len(pending) % 2
                if aligned:
                    yield pending[:aligned]
                    pending = pending[aligned:]
            if pending:
                raise ProviderFailure('provider_protocol_error')
        except asyncio.CancelledError:
            if opening is not None and response is None:
                # Cancellation cannot abandon a response that the owned thread later opens.
                def close_late(done):
                    self.pending_openings.discard(done)
                    if not done.cancelled():
                        try:
                            owned = done.result()
                            owned.close()
                        except Exception:
                            pass  # No raw exception/URL/headers enter logs or telemetry.
                opening.add_done_callback(close_late)
            raise
        except ProviderFailure:
            raise
        except TimeoutError:
            raise ProviderFailure('provider_timeout') from None
        except Exception:
            raise ProviderFailure('provider_transport_error') from None
        finally:
            if opening is not None and opening.done():
                self.pending_openings.discard(opening)
            if response is not None:
                try:
                    response.close()
                except Exception:
                    raise ProviderFailure('provider_transport_error') from None
