"""Bounded metadata-only JSONL trace. Raw audio never enters telemetry."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

from voicelab.contracts import VoiceEvent


class TraceWriter:
    def __init__(self, root: Path, session_id: str, trial_namespace: str, arm: str, *, run_id: str = "mock-local", boot_id: str | None = None):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"{session_id}.jsonl"
        self.handle = self.path.open("x", encoding="utf-8", newline="\n")
        self.digest = hashlib.sha256()
        self.sequence = 0
        self.session_id, self.trial_namespace, self.arm = session_id, trial_namespace, arm
        self.run_id = run_id
        self.closed = False
        self.boot_id = boot_id
        self.last_timestamp = 0
        self.terminal = False
        self.durable_terminal = False
        self.started = False
        self.hash_verified = False
        self.last_connection_epoch = 0
        self.last_response_epoch = 0

    def emit(self, kind: str, connection_epoch: int, response_epoch: int, **fields: object) -> dict[str, object]:
        # Reserve the last row for an explicit terminal even after event overflow.
        if self.closed or self.terminal or self.sequence >= (20_000 if kind == "session_ended" else 19_999):
            raise ValueError("trace_closed_or_overflow")
        if (not self.started and kind != "session_started") or (self.started and kind == "session_started"):
            raise ValueError("trace_session_start_integrity")
        stamp = time.monotonic_ns()
        if stamp < self.last_timestamp:
            raise ValueError("trace_clock_regressed")
        if connection_epoch < self.last_connection_epoch or response_epoch < self.last_response_epoch:
            raise ValueError("trace_epoch_regressed")
        if kind in {"session_ended", "turn_ended"} and fields.get("terminal_outcome") is None:
            raise ValueError("trace_terminal_outcome_required")
        event = VoiceEvent(run_id=self.run_id, trial_id=self.trial_namespace, arm=self.arm, session_id=self.session_id, connection_epoch=connection_epoch, response_epoch=response_epoch, sequence=self.sequence, kind=kind, timestamp=stamp, clock_domain="server_monotonic_ns", boot_id=self.boot_id, **fields)
        data = event.model_dump_json() + "\n"
        self.handle.write(data)
        self.handle.flush()
        self.digest.update(data.encode())
        self.sequence += 1
        self.last_timestamp = stamp
        self.last_connection_epoch = connection_epoch
        self.last_response_epoch = response_epoch
        self.started = True
        self.terminal = kind == "session_ended"
        if self.terminal:
            os.fsync(self.handle.fileno())
            self.durable_terminal = True
        return event.model_dump()

    def metadata(self) -> dict[str, object]:
        return {"trace_hash": self.digest.hexdigest() if self.hash_verified else None, "event_count": self.sequence,
            "terminal_present": self.terminal, "clock_domain": "server_monotonic_ns",
            "terminal_fsynced": self.durable_terminal,
            "boot_id": self.boot_id, "run_id": self.run_id, "trial_id": self.trial_namespace,
            "session_id": self.session_id, "arm": self.arm,
            "trace_reference": f"traces/{self.session_id}.jsonl"}

    def close(self) -> str:
        if not self.closed:
            self.handle.close()
            self.closed = True
        actual = hashlib.sha256()
        with self.path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65_536), b""):
                actual.update(chunk)
        if actual.hexdigest() != self.digest.hexdigest():
            raise ValueError("trace_hash_integrity")
        self.hash_verified = True
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
