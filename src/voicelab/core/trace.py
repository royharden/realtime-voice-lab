"""Bounded metadata-only JSONL trace. Raw audio never enters telemetry."""
from __future__ import annotations

import hashlib
from pathlib import Path
import time

from voicelab.contracts import VoiceEvent


class TraceWriter:
    def __init__(self, root: Path, session_id: str, trial_namespace: str, arm: str, *, run_id: str = "mock-local"):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"{session_id}.jsonl"
        self.handle = self.path.open("x", encoding="utf-8", newline="\n")
        self.digest = hashlib.sha256()
        self.sequence = 0
        self.session_id, self.trial_namespace, self.arm = session_id, trial_namespace, arm
        self.run_id = run_id
        self.closed = False

    def emit(self, kind: str, connection_epoch: int, response_epoch: int, **fields: object) -> dict[str, object]:
        if self.closed or self.sequence >= 20_000:
            raise ValueError("trace_closed_or_overflow")
        event = VoiceEvent(run_id=self.run_id, trial_id=self.trial_namespace, arm=self.arm, session_id=self.session_id, connection_epoch=connection_epoch, response_epoch=response_epoch, sequence=self.sequence, kind=kind, timestamp=time.monotonic_ns(), clock_domain="server_monotonic_ns", **fields)
        data = event.model_dump_json() + "\n"
        self.handle.write(data)
        self.handle.flush()
        self.digest.update(data.encode())
        self.sequence += 1
        return event.model_dump()

    def close(self) -> str:
        if not self.closed:
            self.handle.close()
            self.closed = True
        return self.digest.hexdigest()


def store_pcm(root: Path, raw: bytes, *, maximum_bytes: int = 9600) -> str:
    """Content-addressed bounded PCM; provenance is recorded separately, never inferred."""
    if not 1 <= maximum_bytes <= 5_760_000 or not raw or len(raw) % 2 or len(raw) > maximum_bytes:
        raise ValueError("response_artifact_bound")
    digest = hashlib.sha256(raw).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}.pcm16"
    if path.is_symlink() or path.is_junction():
        raise ValueError("artifact_link_refused")
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("artifact_hash_conflict")
    return digest
