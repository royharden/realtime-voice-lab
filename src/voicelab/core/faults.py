"""Opt-in mock fault controls. An injection receipt is never product acceptance."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import time
from typing import Callable

from voicelab.contracts import MockFaultControl, MockFaultPolicy, MockFaultReceipt


BOUNDARIES = {
    "late_pcm": "mock_provider_pcm_queue", "duplicate_pcm": "mock_provider_pcm_queue",
    "reordered_pcm": "mock_provider_pcm_queue", "queue_overrun": "mock_provider_pcm_queue",
    "missing_audio": "mock_provider_pcm_stream", "missing_transcript": "mock_transcript_stream",
    "lost_ack": "relay_render_ack", "drop_before_commit": "mock_authority_effect",
    "drop_after_commit": "mock_authority_effect", "fresh_tool_duplicate": "mock_proposal_parser_authority",
    "service_restart": "owned_mock_session_lifecycle", "provider_error": "mock_provider_error_parser",
}


class FaultRefused(ValueError):
    pass


class MockFaultController:
    def __init__(self, policy: MockFaultPolicy, identity: dict[str, object],
                 notify: Callable[[dict], None], epochs: Callable[[], tuple[int, int]]):
        self.policy, self.identity, self.notify = policy, dict(identity), notify
        self.entries: dict[str, dict] = {}
        self.responses: dict[str, dict] = {}
        self.claimed: set[str] = set()
        self.epochs = epochs

    def arm(self, control: MockFaultControl, connection_epoch: int, response_epoch: int) -> tuple[dict, bool]:
        previous = self.entries.get(control.control_id)
        if previous is not None:
            if previous["kind"] != control.kind:
                raise FaultRefused("control_id_reuse_conflict")
            previous["request_count"] += 1
            return deepcopy(self.responses.get(control.control_id, previous)), False
        if len(self.entries) >= self.policy.max_controls:
            raise FaultRefused("session_fault_control_limit")
        if any(row["kind"] == control.kind and row["status"] == "armed" for row in self.entries.values()):
            raise FaultRefused("fault_kind_already_pending")
        # Random-looking parameters depend only on the operator seed and stable control ID.
        seed_bytes = hashlib.sha256(f"{self.policy.seed}:{control.control_id}:{control.kind}".encode()).digest()
        receipt = {"schema_version": 1, **self.identity, "control_id": control.control_id,
            "kind": control.kind, "seed": self.policy.seed, "status": "armed", "simulation": True,
            "actual_process_restart": False, "affected_boundary": BOUNDARIES[control.kind],
            "authorization_reference": self.policy.authorization_reference,
            "connection_epoch": connection_epoch, "response_epoch": response_epoch,
            "server_timestamp_ns": time.monotonic_ns(), "clock_domain": "server_monotonic_ns",
            "request_count": 1, "injected_count": 0, "effect_observation": "unknown",
            "observed_terminal_outcome": None,
            "parameters": {"delay_ms": 100 + int.from_bytes(seed_bytes[:2], "big") % 401} if control.kind == "late_pcm" else {}}
        receipt = MockFaultReceipt.model_validate(receipt).model_dump(mode="json")
        self.entries[control.control_id] = receipt
        self.notify(deepcopy(receipt))
        return deepcopy(receipt), True

    def response(self, control_id: str) -> dict:
        """Freeze the first HTTP response; retries cannot inject twice or rewrite it."""
        if control_id not in self.responses:
            self.responses[control_id] = deepcopy(self.entries[control_id])
        return deepcopy(self.responses[control_id])

    def take(self, kind: str) -> dict | None:
        for control_id, row in self.entries.items():
            if row["kind"] == kind and row["status"] == "armed" and control_id not in self.claimed:
                self.claimed.add(control_id)
                return deepcopy(row)
        return None

    def applied(self, control_id: str, *, injected_count: int = 1, **details: object) -> None:
        row = self.entries[control_id]
        connection, response = self.epochs()
        row.update(status="applied", injected_count=injected_count,
            effect_observation="observed", applied_server_ns=time.monotonic_ns(), details=details,
            applied_connection_epoch=connection, applied_response_epoch=response)
        MockFaultReceipt.model_validate(row)
        self.notify(deepcopy(row))

    def finish(self, outcome: str) -> None:
        notification_error = None
        for row in self.entries.values():
            row["observed_terminal_outcome"] = outcome
            if row["status"] == "armed":
                row.update(status="unresolved", effect_observation="unknown",
                    details={"reason": "session_ended_before_injection_boundary"})
                try:
                    self.notify(deepcopy(row))
                except Exception as exc:
                    notification_error = exc
        if notification_error is not None:
            raise notification_error

    def snapshot(self) -> dict:
        return {"schema_version": 1, **self.identity, "simulation": True,
            "seed": self.policy.seed, "controls": deepcopy(list(self.entries.values())),
            "control_count": len(self.entries), "runtime_verified": False}
