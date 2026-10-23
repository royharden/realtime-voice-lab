"""Generate explicitly non-speech development stimulus only on operator request."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct
import wave


def generate_development(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    path = root / 'silence.wav'
    # Silence prevents fake microphone capture from silently creating extra turns.
    # The journey deliberately uses labeled typed interpretation plus real capture/playback.
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\0\0' * 24000)
    tone = root / 'diagnostic.wav'
    with wave.open(str(tone), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b''.join(struct.pack('<h', round(1400 * math.sin(2 * math.pi * 440 * i / 24000)))
                                   for i in range(2400)))
    manifest = {'schema_version': 1, 'split': 'dev',
        'kind': 'diagnostic_signal_and_typed_stimulus', 'speech': False,
        'generator': 'voicelab.harness.fixtures', 'generator_version': '1',
        'speaker': None, 'voice': None, 'heldout': False,
        'license': {'reviewed': False, 'reference': None,
                    'note': 'Locally generated silence/440Hz signal; operator must review provenance. No speech rights asserted.'},
        'audio': {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                  'sample_rate': 24000, 'channels': 1, 'format': 'PCM16LE', 'duration_seconds': 1},
        'diagnostic_signal': {'path': tone.name, 'sha256': hashlib.sha256(tone.read_bytes()).hexdigest()},
        'turns': ['hours', 'list slots', 'select slot 1', 'confirm', 'status'],
        'limitations': ['No speech recognition evidence', 'No spoken-task correctness',
                        'No independent fixture custody or acoustic calibration']}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
