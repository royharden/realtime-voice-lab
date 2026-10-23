"""Dormant approved-synthetic retention; all manual capture is otherwise ephemeral."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from voicelab.core.trace import store_pcm
from voicelab.security import safe_artifact_path

MAX_INPUT_BYTES = 8 * 15 * 24_000 * 2
MAX_OUTPUT_BYTES = 8 * 10 * 24_000 * 2


@dataclass(frozen=True)
class RetentionManifest:
    arm: str
    run_id: str
    trial_id: str
    fixture_id: str
    synthetic: bool
    approved_input_sha256: tuple[str, ...]
    retain_input: bool
    retain_output: bool
    approval_reference: str
    input_rights_reference: str
    output_rights_reference: str | None
    manifest_reference: str
    retention_until_utc: str
    custody_reference: str
    policy: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> RetentionManifest:
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError("synthetic_retention_manifest_fields_required")
        for key in ("run_id", "trial_id", "fixture_id"):
            if not isinstance(value[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value[key]):
                raise ValueError("invalid_synthetic_fixture_identity")
        hashes = value["approved_input_sha256"]
        if not isinstance(hashes, (list, tuple)) or not 1 <= len(hashes) <= 32 or any(not isinstance(h, str) or not re.fullmatch(r"[a-f0-9]{64}", h) for h in hashes) or len(set(hashes)) != len(hashes):
            raise ValueError("finite_exact_synthetic_input_allowlist_required")
        if value["synthetic"] is not True or value["arm"] not in {"V", "C"} or value["policy"] != "operator_custody_expiry":
            raise ValueError("unsupported_retention_policy")
        if type(value["retain_input"]) is not bool or type(value["retain_output"]) is not bool:
            raise ValueError("explicit_retention_permissions_required")
        for key in ("approval_reference", "input_rights_reference", "manifest_reference", "custody_reference"):
            if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 500:
                raise ValueError("synthetic_retention_approval_and_rights_required")
        if value["retain_output"] and (not isinstance(value["output_rights_reference"], str) or not 1 <= len(value["output_rights_reference"]) <= 500):
            raise ValueError("synthetic_output_rights_required")
        if value["output_rights_reference"] is not None and (not isinstance(value["output_rights_reference"], str) or len(value["output_rights_reference"]) > 500):
            raise ValueError("invalid_output_rights_reference")
        if not isinstance(value["retention_until_utc"], str):
            raise ValueError("finite_retention_expiry_required")
        expiry = datetime.fromisoformat(value["retention_until_utc"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if expiry.tzinfo is None or not now < expiry <= now + timedelta(days=30):
            raise ValueError("retention_expiry_must_be_future_and_within_30_days")
        return cls(**{**value, "approved_input_sha256": tuple(hashes)})


class TrialRetention:
    def __init__(self, manifest: RetentionManifest | None):
        self.manifest = manifest
        self.inputs: list[bytes] = []
        self.outputs: dict[str, bytearray] = {}
        self.rendered: dict[str, int] = {}
        self.input_bytes = 0
        self.output_bytes = 0
        self.finished = False

    def append_input(self, raw: bytes) -> None:
        if self.manifest is None:
            return
        if self.finished or self.input_bytes + len(raw) > MAX_INPUT_BYTES:
            raise ValueError("retention_input_buffer_bound")
        self.inputs.append(raw)
        self.input_bytes += len(raw)

    def append_output(self, response_id: str, raw: bytes) -> None:
        if self.manifest is None or not self.manifest.retain_output:
            return
        if self.finished or self.output_bytes + len(raw) > MAX_OUTPUT_BYTES:
            raise ValueError("retention_output_buffer_bound")
        self.outputs.setdefault(response_id, bytearray()).extend(raw)
        self.output_bytes += len(raw)

    def acknowledge(self, response_id: str, rendered_sample: int) -> None:
        if self.manifest is None or not self.manifest.retain_output:
            return
        if response_id not in self.outputs or not self.rendered.get(response_id, 0) <= rendered_sample <= len(self.outputs[response_id]) // 2:
            raise ValueError("retention_render_ack_invalid")
        self.rendered[response_id] = rendered_sample

    def finalize(self, root: Path, session_id: str) -> dict[str, object]:
        """Called once after admission closes, never on an input prefix match."""
        self.finished = True
        try:
            if self.manifest is None:
                return {"input_persisted": False, "output_persisted": False, "reason": "no_synthetic_retention_manifest"}
            manifest = self.manifest
            raw = b"".join(self.inputs)
            digest = hashlib.sha256(raw).hexdigest()
            expiry = datetime.fromisoformat(manifest.retention_until_utc.replace("Z", "+00:00"))
            if not raw or digest not in manifest.approved_input_sha256 or datetime.now(timezone.utc) >= expiry:
                return {"input_content_hash": digest, "input_persisted": False, "output_persisted": False, "reason": "synthetic_input_mismatch_or_approval_expired", "manifest_reference": manifest.manifest_reference}
            record: dict[str, object] = {"schema_version": 1, "run_id": manifest.run_id, "trial_id": manifest.trial_id, "fixture_id": manifest.fixture_id, "session_id": session_id, "input_content_hash": digest, "input_persisted": False, "output_persisted": False, "sample_rate": 24000, "channels": 1, "encoding": "signed_little_endian_pcm16", "approval_reference": manifest.approval_reference, "input_rights_reference": manifest.input_rights_reference, "output_rights_reference": manifest.output_rights_reference, "manifest_reference": manifest.manifest_reference, "retention_until_utc": manifest.retention_until_utc, "custody_reference": manifest.custody_reference, "policy": manifest.policy, "reason": "exact_approved_synthetic_trial_input"}
            directory = safe_artifact_path(root, "approved-synthetic-audio")
            if manifest.retain_input:
                record["input_artifact_hash"] = store_pcm(directory, raw, maximum_bytes=MAX_INPUT_BYTES)
                record["input_persisted"] = True
            rendered_output = b"".join(bytes(audio[:self.rendered.get(response, 0) * 2]) for response, audio in self.outputs.items())
            if manifest.retain_output and rendered_output:
                record["output_artifact_hash"] = store_pcm(directory, rendered_output, maximum_bytes=MAX_OUTPUT_BYTES)
                record["output_persisted"] = True
                record["output_boundary"] = "client_rendered_ack_pcm_prefixes"
            directory.mkdir(parents=True, exist_ok=True)
            sidecar = safe_artifact_path(root, f"approved-synthetic-audio/retention-{session_id}.json")
            with sidecar.open("x", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            return record
        finally:
            self.inputs.clear()
            self.outputs.clear()
            self.rendered.clear()
