"""Canonical saved JSON snapshots, referenced and verified separately from totals."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from voicelab.security import safe_artifact_path

MAX_SNAPSHOT_BYTES = 32_000_000


def save_snapshot(root: Path, session_id: str, kind: str, data: dict) -> dict[str, object]:
    if kind not in {"ledger_snapshot", "fault_controls", "calibration_phases"}:
        raise ValueError("unsupported_snapshot_kind")
    raw = (json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot_export_bound")
    digest = hashlib.sha256(raw).hexdigest()
    reference = f"evidence-snapshots/{session_id}-{kind}-{digest}.json"
    path = safe_artifact_path(root, reference)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        with path.open("rb") as handle:
            previous = handle.read(MAX_SNAPSHOT_BYTES + 1)
        if previous != raw:
            raise ValueError("snapshot_hash_conflict")
    return {"kind": kind, "artifact_hash": digest, "artifact_reference": reference,
        "bytes": len(raw), "encoding": "canonical_json_utf8_sorted_compact_newline",
        "json_ensure_ascii": True,
        "observation": "observed"}


def read_snapshot(root: Path, metadata: dict) -> dict:
    path = safe_artifact_path(root, metadata["artifact_reference"])
    with path.open("rb") as handle:
        raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES or hashlib.sha256(raw).hexdigest() != metadata["artifact_hash"]:
        raise ValueError("snapshot_hash_or_bound_invalid")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("snapshot_object_required")
    return data
