"""Bounded synthetic wire dialect. Not a provider compatibility certificate.

Both mock generation and inbound replay use these same serializer/parser seams.
Provider-specific live wire negotiation remains gated and unimplemented.
"""
from __future__ import annotations

import base64
import json

KINDS = {'transcript.final', 'audio.delta', 'response.done', 'usage', 'error', 'cancel.ack', 'tool.call'}


def serialize_wire(message: dict) -> str:
    raw = json.dumps(message, sort_keys=True, separators=(',', ':'), allow_nan=False)
    parse_wire(raw)
    return raw


def parse_wire(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 131072:
        raise ValueError('wire frame bound exceeded')
    message = json.loads(raw)
    if not isinstance(message, dict) or message.get('type') not in KINDS:
        raise ValueError('unknown wire event')
    if message.get('wire_version') != 1:
        raise ValueError('wire version mismatch')
    if message['type'] == 'audio.delta':
        if message.get('sample_rate') != 24000:
            raise ValueError('unsupported audio format')
        pcm = base64.b64decode(message.get('pcm16', ''), validate=True)
        if not pcm or len(pcm) % 2 or len(pcm) > 96000:
            raise ValueError('invalid PCM16 payload')
    if message['type'] == 'transcript.final' and not isinstance(message.get('text'), str):
        raise ValueError('missing transcript')
    return message
