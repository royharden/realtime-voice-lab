"""Later-phase browser fixtures. NOT YET RUN.

A listener starts only with an operator agent-ports claim (as root `serve` requires): set
VOICELAB_TEST_PORT (20000-29999) and VOICELAB_TEST_PORT_CLAIM (existing receipt file). Run
the agent-ports check immediately before. No port is hardcoded; without a claim these
tests skip. The UI is the real built web/ bundle (VOICELAB_WEB_DIST, from `npm run build`).
The relay and UI share one exact loopback origin, as the evidence inspector requires.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
import wave

import pytest

NONCE = 'browser-nonce-0123456789abcdef'


@pytest.fixture
def port_claim() -> int:
    raw, receipt = os.environ.get('VOICELAB_TEST_PORT'), os.environ.get('VOICELAB_TEST_PORT_CLAIM')
    if not raw or not receipt:
        pytest.skip('agent-ports claim required: claim/check a port, then set VOICELAB_TEST_PORT '
                    'and VOICELAB_TEST_PORT_CLAIM (receipt path); no listener without a claim')
    if not raw.isdigit() or not 20000 <= int(raw) <= 29999:
        pytest.skip('VOICELAB_TEST_PORT must be a claimed port in the 20000-29999 band')
    if not Path(receipt).is_file():
        pytest.skip('VOICELAB_TEST_PORT_CLAIM must name an existing agent-ports claim receipt')
    return int(raw)


@pytest.fixture
def web_dist() -> Path:
    raw = os.environ.get('VOICELAB_WEB_DIST')
    if not raw or not (Path(raw) / 'index.html').is_file():
        pytest.skip('built web bundle required: set VOICELAB_WEB_DIST to web/dist after npm run build')
    return Path(raw)


@pytest.fixture
def serve(port_claim, web_dist, tmp_path):
    """Context factory: relay + built UI on the claimed port; page_host picks the page origin."""
    pytest.importorskip('uvicorn')
    from starlette.staticfiles import StaticFiles
    import uvicorn
    from voicelab.server.app import Settings, create_app

    @contextmanager
    def run(page_host: str = '127.0.0.1'):
        page_origin = f'http://{page_host}:{port_claim}'
        # Silent mono PCM16 24 kHz capture, written before any listener exists: Chromium's
        # default fake device beeps, which could trip the energy endpoint/barge-in.
        capture = tmp_path / f'silence-{page_host}.wav'
        with wave.open(str(capture), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\0\0' * 24000 * 30)
        app = create_app(Settings(nonce=NONCE, allowed_origin=page_origin,
                                  artifact_root=tmp_path / f'runtime-{page_host}'))
        # Test-only same-port composition; relay routes are registered first and win.
        app.mount('/', StaticFiles(directory=web_dist, html=True), name='web')
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port_claim,
                                               ws='websockets', ws_max_size=24_000,
                                               access_log=False, log_level='warning'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                server.should_exit = True
                raise RuntimeError('claimed-port relay did not start')
            time.sleep(0.05)
        try:
            yield {'page_origin': page_origin, 'relay_url': f'ws://127.0.0.1:{port_claim}/ws',
                   'relay_origin': f'http://127.0.0.1:{port_claim}', 'nonce': NONCE,
                   'capture_wav': str(capture)}
        finally:
            server.should_exit = True
            thread.join(timeout=10)

    return run
