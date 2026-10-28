"""Conservative documented wire subset, isolated from disabled live dispatch.

Source: official OpenAI Realtime conversations/transcription and TTS guides,
reviewed 2026-10-23. Account/model/voice/prices/rights are NOT validated.
"""
from __future__ import annotations

import base64
import json


def _object(raw: str) -> dict:
    if len(raw.encode('utf-8')) > 131072:
        raise ValueError('provider frame too large')
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get('type'), str):
        raise ValueError('malformed provider frame')
    return value


def pcm_chunk(data: bytes, *, sample_rate: int = 24000) -> dict:
    if sample_rate != 24000 or not data or len(data) % 2 or len(data) > 9600:
        raise ValueError('unsupported PCM16 chunk')
    return {'type': 'audio.delta', 'pcm16': base64.b64encode(data).decode('ascii'), 'sample_rate': 24000}


def parse_realtime(raw: str) -> dict:
    event = _object(raw)
    kind = event['type']
    if kind == 'response.output_audio.delta':
        normalized = pcm_chunk(base64.b64decode(event['delta'], validate=True))
        normalized['response_id'] = event['response_id']
        return normalized
    if kind == 'conversation.item.input_audio_transcription.completed':
        return parse_transcription(raw)
    if kind == 'response.function_call_arguments.done':
        arguments = json.loads(event['arguments'])
        if not isinstance(arguments, dict):
            raise ValueError('tool arguments must be object')
        return {'type': 'tool.call', 'operation_id': event['call_id'],
                'name': event['name'], 'arguments': arguments}
    if kind == 'response.done':
        response = event['response']
        if response.get('status') not in {'completed', 'cancelled', 'failed', 'incomplete'}:
            raise ValueError('unsupported response status')
        return {'type': 'response.done', 'response_id': response['id'],
                'status': response['status'], 'usage': response.get('usage')}
    if kind == 'error':
        error = event.get('error', {})
        return {'type': 'error', 'code': str(error.get('code') or 'provider_error'),
                'message': 'Provider error; raw content omitted'}
    if kind == 'conversation.item.truncated':
        return {'type': 'cancel.ack', 'item_id': event['item_id'], 'audio_end_ms': event['audio_end_ms']}
    raise ValueError('unsupported provider event: explicit mapping required')


def parse_transcription(raw: str) -> dict:
    event = _object(raw)
    if event['type'] not in {'conversation.item.input_audio_transcription.completed',
                              'conversation.item.input_audio_transcription.delta'}:
        raise ValueError('unsupported transcription event')
    final = event['type'].endswith('.completed')
    text = event.get('transcript' if final else 'delta')
    if not isinstance(text, str) or len(text) > 8000:
        raise ValueError('invalid transcript')
    return {'type': 'transcript.final' if final else 'transcript.delta',
            'text': text, 'item_id': event['item_id']}


def serialize_command(kind: str, **values) -> str:
    if kind == 'append':
        pcm = base64.b64decode(values['audio'], validate=True)
        pcm_chunk(pcm)
        message = {'type': 'input_audio_buffer.append', 'audio': values['audio']}
    elif kind == 'commit':
        message = {'type': 'input_audio_buffer.commit'}
    elif kind == 'cancel':
        message = {'type': 'response.cancel', 'response_id': values['response_id']}
    elif kind == 'truncate':
        cutoff = values['rendered_cutoff']
        if not isinstance(cutoff, int) or cutoff < 0:
            raise ValueError('invalid rendered cutoff')
        message = {'type': 'conversation.item.truncate', 'item_id': values['item_id'],
                   'content_index': 0, 'audio_end_ms': cutoff * 1000 // 24000}
    else:
        raise ValueError('unsupported outbound command')
    return json.dumps(message, sort_keys=True, separators=(',', ':'), allow_nan=False)


def mock_transcript(arm: str, text: str, item_id: str) -> dict:
    if arm == 'K':
        return {'type': 'transcript.final', 'text': text, 'item_id': item_id}
    raw = json.dumps({'type': 'conversation.item.input_audio_transcription.completed',
                      'item_id': item_id, 'content_index': 0, 'transcript': text})
    return parse_realtime(raw) if arm == 'V' else parse_transcription(raw)


def mock_audio(arm: str, data: bytes, response_id: str) -> dict:
    if arm == 'V':
        return parse_realtime(json.dumps({'type': 'response.output_audio.delta', 'response_id': response_id,
            'item_id': f'item-{response_id}', 'output_index': 0, 'content_index': 0,
            'delta': base64.b64encode(data).decode('ascii')}))
    # C uses the same raw signed little-endian PCM decoder as a future HTTP TTS stream.
    return pcm_chunk(data)
