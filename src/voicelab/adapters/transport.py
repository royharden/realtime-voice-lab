"""Later-activation transports. Importing this module opens no connections."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def allowed_endpoint(url: str, *, websocket: bool) -> str:
    parsed = urlsplit(url)
    expected = '/v1/realtime' if websocket else '/v1/audio/speech'
    if (parsed.scheme != ('wss' if websocket else 'https') or parsed.hostname != 'api.openai.com'
            or parsed.port not in (None, 443) or parsed.path != expected
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('provider endpoint refused')
    return url


class ProviderTransport:
    async def connect(self, component: str, model: str, credential: str):
        from websockets.asyncio.client import connect
        query = {'model': model} if component == 'native' else {'intent': 'transcription'}
        url = allowed_endpoint('wss://api.openai.com/v1/realtime?' + urlencode(query), websocket=True)
        class NoRedirectConnect(connect):
            # Pinned websockets14.1 source exposes this redirect decision seam.
            def process_redirect(self, exc):
                return exc

        logger = logging.Logger('voicelab.private_transport')
        logger.disabled = True
        logger.propagate = False
        return await NoRedirectConnect(url, additional_headers={'Authorization': 'Bearer ' + credential},
                             open_timeout=5, close_timeout=2, max_size=131072, max_queue=8, logger=logger)

    async def speech(self, model: str, voice: str, text: str, credential: str):
        """Streaming raw PCM. Redirects are disabled to avoid credential forwarding."""
        from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise ValueError('provider redirect refused')

        url = allowed_endpoint('https://api.openai.com/v1/audio/speech', websocket=False)
        request = Request(url, data=json.dumps({'model': model, 'voice': voice, 'input': text,
            'response_format': 'pcm'}).encode(), headers={'Authorization': 'Bearer ' + credential,
            'Content-Type': 'application/json'}, method='POST')
        response = None
        try:
            response = await asyncio.to_thread(build_opener(ProxyHandler({}), NoRedirect()).open, request, timeout=5)
            pending = b''
            total = 0
            while True:
                chunk = await asyncio.to_thread(response.read, 4800)
                if not chunk:
                    break
                total += len(chunk)
                if total > 24000 * 2 * 10:
                    raise ValueError('TTS output duration bound exceeded')
                pending += chunk
                aligned = len(pending) - len(pending) % 2
                if aligned:
                    yield pending[:aligned]
                    pending = pending[aligned:]
            if pending:
                raise ValueError('truncated PCM sample')
        finally:
            if response is not None:
                response.close()
