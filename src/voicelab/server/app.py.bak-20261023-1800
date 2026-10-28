"""FastAPI factory; explicit activation/port claim belongs to the CLI operator."""
from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from decimal import Decimal
import hmac
import hashlib
import ipaddress
import os
from pathlib import Path
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import TypeAdapter

from voicelab.accounting.ledger import AttemptLedger, BudgetRefused
from voicelab.adapters import create_arm
from voicelab.adapters.live import validate_live_config
from voicelab.contracts import Audio, ClientStatus, Commit, End, Hash, Hello, Identifier, Interrupt, Ping, Rendered, Resume, ServerMessage, parse_client, serialize_server
from voicelab.core.journal import LifecycleJournal
from voicelab.core.session import Admission, SessionState, SessionViolation
from voicelab.core.retention import RetentionManifest, TrialRetention
from voicelab.core.task import TaskAuthority
from voicelab.core.trace import TraceWriter, store_pcm
from voicelab.security import safe_artifact_path, validate_boundary

EVIDENCE_LABEL = "keyless mock — diagnostic synthetic tone; no spoken-task acceptance"
LIVE_EVIDENCE_LABEL = "authorized live source — runtime unverified; independent acceptance pending"


def failure_outcome(code: str) -> str:
    if code in {"silence_timeout", "permission_denied", "device_lost", "service_restart", "transport_loss", "abandoned", "unsupported_audio", "calibration_invalid"}:
        return code
    if code in {"invalid_audio_samples", "invalid_adapter_audio"}:
        return "unsupported_audio"
    if code in {"response_deadline", "provider_timeout"}:
        return "provider_timeout"
    if code in {"budget_exhausted", "budget_halt"}:
        return "budget_halt"
    if code in {"output_queue_overflow", "input_audio_overflow", "output_duration_overflow", "trace_closed_or_overflow", "queue_overrun"}:
        return "queue_overrun"
    if code in {"recovery_exhausted", "recovery_unknown"}:
        return "recovery_unknown"
    if code in {"task_deadline", "turn_limit"}:
        return "task_failure"
    if code in {"provider_error", "adapter_failure", "provider_or_transport_failure", "missing_response_audio"}:
        return "provider_error"
    return "protocol_error"


def exception_code(exc: Exception) -> str:
    """Never copy provider messages, transcripts, paths or credentials into telemetry."""
    if isinstance(exc, BudgetRefused):
        return "budget_exhausted"
    if isinstance(exc, asyncio.TimeoutError):
        return "provider_timeout"
    if isinstance(exc, asyncio.QueueFull):
        return "queue_overrun"
    if isinstance(exc, SessionViolation):
        return str(exc)
    if isinstance(exc, ValueError) and str(exc) in {"provider_timeout", "provider_error", "trace_closed_or_overflow"}:
        return str(exc)
    if isinstance(exc, ValueError) and "recovery_unknown" in str(exc):
        return "recovery_unknown"
    return "adapter_failure"


@dataclass(frozen=True)
class Settings:
    nonce: str
    allowed_origin: str
    artifact_root: Path = Path(".agent-work/runtime")
    mode: str = "mock"
    live_config: dict[str, Any] | None = None
    enabled_arms: tuple[str, ...] = ("V", "C")
    retention_manifests: tuple[dict[str, Any], ...] = ()
    run_id: str = "mock-local"
    run_manifest_hash: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings(nonce=os.environ.get("VOICELAB_NONCE", ""), allowed_origin=os.environ.get("VOICELAB_ALLOWED_ORIGIN", ""), artifact_root=Path(os.environ.get("VOICELAB_ARTIFACT_ROOT", ".agent-work/runtime")))
    if settings.mode not in {"mock", "live"} or not 16 <= len(settings.nonce) <= 128:
        raise ValueError("relay requires explicit mode and VOICELAB_NONCE of 16..128 characters")
    TypeAdapter(Identifier).validate_python(settings.run_id)
    if settings.run_manifest_hash is not None:
        TypeAdapter(Hash).validate_python(settings.run_manifest_hash)
    live_configs: dict[str, dict[str, Any]] = {}
    if settings.mode == "live":
        if not settings.live_config or not settings.enabled_arms or any(arm not in {"V", "C"} for arm in settings.enabled_arms):
            raise ValueError("live_gate_closed: explicit manifests and V/C arms required; live K control audio not supplied")
        for arm in settings.enabled_arms:
            live_configs[arm] = validate_live_config(settings.live_config, arm)
        if len({entry["budget_usd"] for entry in live_configs.values()}) != 1:
            raise ValueError("live_gate_closed: inconsistent run budget")
    evidence_label = LIVE_EVIDENCE_LABEL if settings.mode == "live" else EVIDENCE_LABEL
    retention_by_arm: dict[str, list[RetentionManifest]] = {"V": [], "C": []}
    trial_ids: set[str] = set()
    if settings.retention_manifests and settings.mode != "live":
        raise ValueError("live_synthetic_retention_assignments_require_live_mode")
    if len(settings.retention_manifests) > 1000:
        raise ValueError("retention_trial_assignment_bound")
    for raw_manifest in settings.retention_manifests:
        manifest = RetentionManifest.parse(raw_manifest)
        if manifest.arm not in live_configs or manifest.run_id != live_configs[manifest.arm]["run_id"] or manifest.trial_id in trial_ids:
            raise ValueError("retention_run_arm_or_trial_identity_conflict")
        trial_ids.add(manifest.trial_id)
        retention_by_arm[manifest.arm].append(manifest)
    origin = urlsplit(settings.allowed_origin)
    try:
        local = origin.hostname == "localhost" or ipaddress.ip_address(origin.hostname or "").is_loopback
        valid = local and origin.scheme == "http" and origin.port is not None and not any((origin.path, origin.query, origin.fragment, origin.username, origin.password))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("VOICELAB_ALLOWED_ORIGIN must be an explicit exact loopback http origin with port")
    admission = Admission(3)
    sessions: dict[str, Relay] = {}
    boot_id = "boot-" + secrets.token_hex(12)
    journal: LifecycleJournal | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal journal
        for ancestor in (settings.artifact_root.absolute(), *settings.artifact_root.absolute().parents):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError("artifact_root_link_refused")
        settings.artifact_root.mkdir(parents=True, exist_ok=True)
        journal = LifecycleJournal(safe_artifact_path(settings.artifact_root, "task.sqlite3"))
        journal.recover(boot_id)
        try:
            yield
        finally:
            for relay in list(sessions.values()):
                await relay.shutdown("service_restart", outcome="service_restart")

    app = FastAPI(title=f"Realtime Voice Lab — {settings.mode}", lifespan=lifespan)

    def authorize(request: Request) -> None:
        try:
            validate_boundary(request.headers.get("host", ""), request.headers.get("origin", ""), request.headers.get("x-voicelab-nonce", ""), settings.nonce, settings.allowed_origin)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="local boundary refused") from exc

    @app.get("/sessions/{session_id}/evidence")
    async def session_evidence(session_id: str, request: Request) -> dict[str, object]:
        authorize(request)
        if journal is None:
            raise HTTPException(status_code=503, detail="relay not initialized")
        evidence = journal.export(session_id)
        if evidence is None:
            raise HTTPException(status_code=404, detail="unknown session")
        # SQLite lookup only; client ID never becomes a filesystem path.
        authority = TaskAuthority(safe_artifact_path(settings.artifact_root, "task.sqlite3"))
        try:
            evidence["task_journal"] = authority.history(evidence["session"]["trial_id"])
        finally:
            authority.close()
        evidence["runtime_verified"] = False
        return evidence

    @app.get("/sessions/{session_id}/trace")
    async def session_trace(session_id: str, request: Request) -> Response:
        authorize(request)
        evidence = journal.export(session_id) if journal is not None else None
        if evidence is None:
            raise HTTPException(status_code=404, detail="unknown session")
        record = evidence["session"]
        if record["terminal_outcome"] is None:
            raise HTTPException(status_code=409, detail="session still active")
        # The file reference comes from a registered server-generated ID, never a path parameter.
        path = safe_artifact_path(settings.artifact_root, evidence["trace_reference"])
        try:
            with path.open("rb") as handle:
                raw = handle.read(32_000_001)
        except OSError as exc:
            raise HTTPException(status_code=404, detail="trace unavailable") from exc
        if len(raw) > 32_000_000:
            raise HTTPException(status_code=413, detail="trace export bound exceeded")
        digest = hashlib.sha256(raw).hexdigest()
        if record["trace_hash"] is not None and record["trace_hash"] != digest:
            raise HTTPException(status_code=409, detail="trace hash mismatch")
        return Response(content=raw, media_type="application/x-ndjson", headers={
            "X-Voicelab-Trace-SHA256": digest,
            "X-Voicelab-Trace-Complete": "true" if record["trace_complete"] else "false"})

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"mode": settings.mode, "protocol_version": 1, "live_admission": settings.mode == "live", "runtime_verified": False, "evidence_label": evidence_label}

    @app.get("/diagnostics")
    async def diagnostics(request: Request) -> dict[str, object]:
        authorize(request)
        metrics: dict[str, dict[str, object]] = {
            name: {"value": None, "observation": "unknown", "unit": unit, "reason": "own-process instrumentation unavailable"}
            for name, unit in (("rss_bytes", "bytes"), ("open_sockets", "count"), ("open_files", "count"))
        }
        try:
            import psutil
            process = psutil.Process(os.getpid())
            readers = {"rss_bytes": lambda: process.memory_info().rss, "open_sockets": lambda: len(process.net_connections(kind="inet")), "open_files": lambda: len(process.open_files())}
            for name, reader in readers.items():
                try:
                    metrics[name] = {"value": reader(), "observation": "observed", "unit": "bytes" if name == "rss_bytes" else "count"}
                except (psutil.Error, OSError, NotImplementedError):
                    pass
        except Exception:
            pass
        metrics["asyncio_tasks"] = {"value": len(asyncio.all_tasks()), "observation": "observed", "unit": "count", "reason": "current server event loop; includes this request"}
        loop = asyncio.get_running_loop()
        expected = loop.time() + 0.01
        await asyncio.sleep(0.01)
        metrics["event_loop_lag_ms"] = {"value": max(0.0, (loop.time() - expected) * 1000), "observation": "observed", "unit": "milliseconds", "reason": "single 10ms scheduled-yield wake delay; diagnostic sample"}
        return {"scope": "own_server_process_only", "boot_id": boot_id, "clock_domain": "server_monotonic_ns", "timestamp_ns": time.monotonic_ns(), "metrics": metrics, "admitted_sessions": len(admission.sessions)}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        # Never echo query values or nonce in errors. Validate before socket admission.
        try:
            validate_boundary(socket.headers.get("host", ""), socket.headers.get("origin", ""), socket.query_params.get("nonce", ""), settings.nonce, settings.allowed_origin)
        except ValueError:
            await socket.close(code=1008, reason="local boundary refused")
            return
        await socket.accept()
        relay: Relay | None = None
        resumed = False
        try:
            first = parse_client(await asyncio.wait_for(socket.receive_text(), timeout=5))
            if not isinstance(first, Hello) or not hmac.compare_digest(first.nonce.encode(), settings.nonce.encode()):
                raise SessionViolation("hello_required")
            if first.mode != settings.mode or (first.mode == "live" and first.arm not in live_configs):
                raise SessionViolation("operator_mode_or_arm_gate_closed")
            # Reconnect uses opaque session ID plus same local boundary and arm.
            resume_id = socket.query_params.get("session_id")
            if resume_id:
                relay = sessions.get(resume_id)
                if relay is None or relay.state.arm != first.arm or relay.state.state != "disconnected":
                    raise SessionViolation("resume_unknown_or_active")
                # Claim synchronously before awaiting send: two sockets cannot attach.
                relay.state.state = "reconnecting"
                relay.socket = socket
                resumed = True
                await relay.send("ready", {"mode": settings.mode, "arm": first.arm, "evidence_label": evidence_label, "trial_namespace": relay.state.trial_namespace, "resume_required": True, "run_id": relay.run_id, "trial_id": relay.state.trial_namespace, "boot_id": boot_id, "evidence_reference": f"/sessions/{relay.state.session_id}/evidence"})
            else:
                identifier = "s-" + secrets.token_hex(12)
                admission.admit(identifier)
                try:
                    policy = retention_by_arm[first.arm].pop(0) if settings.mode == "live" and retention_by_arm[first.arm] else None
                    trial_namespace = policy.trial_id if policy else "trial-" + secrets.token_hex(12)
                    relay = Relay(socket, settings, SessionState(identifier, trial_namespace, first.arm), policy)
                except BaseException:
                    admission.release(identifier)
                    raise
                sessions[identifier] = relay
                await relay.open()
            while not relay.state.closed:
                remaining = max(0.01, 120 - (time.monotonic_ns() - relay.state.started_ns) / 1e9)
                try:
                    payload = await asyncio.wait_for(socket.receive_text(), timeout=min(1, remaining))
                except asyncio.TimeoutError:
                    # Watchdog owns state-specific deadlines, including quiet playback.
                    continue
                if relay.socket is not socket:
                    break
                try:
                    message = parse_client(payload)
                except ValueError as exc:
                    raise SessionViolation("malformed_protocol") from exc
                if resumed and not isinstance(message, (Resume, End)):
                    raise SessionViolation("resume_required")
                await relay.receive(message)
                if isinstance(message, Resume):
                    resumed = False
        except WebSocketDisconnect:
            if relay and relay.socket is socket and not relay.state.closed:
                await relay.detach()
                return
        except Exception as exc:
            if relay and relay.socket is socket:
                await relay.fail(exception_code(exc))
            else:
                await socket.close(code=1008, reason="admission refused")

    # Expiration owns only this factory's admitted sessions, never other processes.
    async def expire(relay: Relay) -> None:
        await asyncio.sleep(5)
        if relay.state.state in {"disconnected", "reconnecting"}:
            await relay.shutdown("transport_loss", outcome="transport_loss")

    class Relay:
        def __init__(self, socket: WebSocket, config: Settings, state: SessionState, retention_manifest: RetentionManifest | None = None):
            if journal is None:
                raise SessionViolation("relay_not_initialized")
            self.socket, self.settings, self.state = socket, config, state
            self.journal = journal
            self.retention = TrialRetention(retention_manifest)
            # Revalidate expiry and all manifests at admission, not only factory creation.
            self.adapter_config = ({**validate_live_config(config.live_config, state.arm), "mode": "live"} if config.mode == "live" else {"mode": "mock"})
            self.adapter_config.update(connection_epoch=0, response_epoch=0)
            budget = Decimal(self.adapter_config["budget_usd"]) if config.mode == "live" else Decimal("0")
            self.run_id = run_id = self.adapter_config["run_id"] if config.mode == "live" else config.run_id
            ledger_name = f"attempts-{run_id}.sqlite3"
            self.authority = TaskAuthority(safe_artifact_path(config.artifact_root, "task.sqlite3"))
            try:
                self.ledger = AttemptLedger(safe_artifact_path(config.artifact_root, ledger_name), budget)
                self.adapter = create_arm(state.arm, task_authority=self.authority, trial_namespace=state.trial_namespace, ledger=self.ledger, config=self.adapter_config)
                self.trace = TraceWriter(safe_artifact_path(config.artifact_root, "traces"), state.session_id, state.trial_namespace, state.arm, run_id=run_id, boot_id=boot_id)
                self.journal.start(session_id=state.session_id, run_id=run_id, trial_id=state.trial_namespace, arm=state.arm, mode=config.mode, boot_id=boot_id, started_ns=state.started_ns, run_manifest_hash=config.run_manifest_hash)
            except BaseException:
                if hasattr(self, "trace"):
                    self.trace.close()
                self.authority.close()
                raise
            self.sequence = 0
            self.adapter_sequence = -1
            self.sender: asyncio.Task | None = None
            self.expiry: asyncio.Task | None = None
            self.turn_task: asyncio.Task | None = None
            self.recovery_logical_turn: str | None = None
            self.active_logical_turn: str | None = None
            self.lock = asyncio.Lock()
            self.watchdog: asyncio.Task | None = None
            self.turn_outcome = "task_failure"
            self.turn_terminal_written: set[str] = set()
            self.current_transport_turn: str | None = None
            self.logical_response_starts: dict[str, int] = {}
            self.trace_integrity_failed = False

        async def send(self, kind: str, payload: dict[str, Any]) -> None:
            async with self.lock:
                message = ServerMessage(type=kind, session_id=self.state.session_id, connection_epoch=self.state.connection_epoch, response_epoch=self.state.response_epoch, sequence=self.sequence, payload=payload)
                self.sequence += 1
                await asyncio.wait_for(self.socket.send_text(serialize_server(message)), timeout=2)

        def record(self, kind: str, **fields: Any) -> None:
            try:
                self.trace.emit(kind, self.state.connection_epoch, self.state.response_epoch, **fields)
            except Exception:
                self.trace_integrity_failed = True
                raise

        async def open(self) -> None:
            self.record("session_started", reason=evidence_label)
            self.state.state = "connecting"
            self.watchdog = asyncio.create_task(self.watch_deadlines())
            await asyncio.wait_for(self.adapter.open({"mode": self.settings.mode}), timeout=10)
            if self.state.closed:
                return
            self.state._listen()
            self.authority.set_epoch(self.state.trial_namespace, 0)
            await self.send("ready", {"mode": self.settings.mode, "arm": self.state.arm, "evidence_label": evidence_label, "trial_namespace": self.state.trial_namespace, "run_id": self.run_id, "trial_id": self.state.trial_namespace, "boot_id": boot_id, "evidence_reference": f"/sessions/{self.state.session_id}/evidence"})
            await self.send("state", {"state": "listening"})
            self.sender = asyncio.create_task(self.pump())

        async def watch_deadlines(self) -> None:
            while not self.state.closed:
                await asyncio.sleep(0.1)
                reason = self.state.deadline_reason()
                if reason:
                    await self.fail(reason)
                    return

        def finish_turn(self, outcome: str, reason: str, source: str = "relay") -> None:
            turn = self.current_transport_turn
            if turn is None or turn in self.turn_terminal_written:
                return
            if self.journal.finish_turn(self.state.session_id, turn, outcome, reason):
                self.turn_terminal_written.add(turn)
                self.record("turn_ended", logical_turn_id=self.active_logical_turn, transport_turn_id=turn, terminal_outcome=outcome, outcome_source=source, reason=reason)

        def start_turn(self, transport: str) -> None:
            if self.current_transport_turn == transport:
                return
            self.current_transport_turn = transport
            self.active_logical_turn = self.recovery_logical_turn or transport
            self.journal.start_turn(self.state.session_id, transport, self.active_logical_turn, self.state.connection_epoch, self.state.response_epoch, time.monotonic_ns())
            self.record("turn_started", logical_turn_id=self.active_logical_turn, transport_turn_id=transport)

        async def pump(self) -> None:
            try:
                async for event in self.adapter.events():
                    if self.state.closed:
                        return
                    if event.get("response_epoch") != self.state.response_epoch or event.get("connection_epoch") != self.state.connection_epoch:
                        self.record("stale_discarded", reason="adapter epoch fence")
                        continue
                    sequence = event.get("sequence")
                    if type(sequence) is not int or sequence <= self.adapter_sequence:
                        raise SessionViolation("adapter_sequence_integrity")
                    if self.adapter_sequence >= 0 and sequence != self.adapter_sequence + 1:
                        self.record("integrity_error", reason="adapter sequence gap; skipped/cancelled events possible", observation="unknown")
                    self.adapter_sequence = sequence
                    kind = event.get("type", "event")
                    if ((kind in {"audio", "task", "transcript"} or event.get("kind") == "response_done") or event.get("turn_id") is not None) and event.get("turn_id") != self.state.active_turn:
                        self.record("stale_discarded", reason="inactive transport turn")
                        continue
                    if kind == "audio":
                        raw = base64.b64decode(event["pcm16"], validate=True)
                        if len(raw) % 2 or not 0 < len(raw) <= 9600 or event.get("sample_rate") != 24000:
                            raise SessionViolation("invalid_adapter_audio")
                        if not self.state.admit_output(event["response_id"], event["response_epoch"], event["connection_epoch"], len(raw) // 2):
                            self.record("stale_discarded", reason="inactive response")
                            continue
                        self.retention.append_output(event["response_id"], raw)
                        content_hash = hashlib.sha256(raw).hexdigest()
                        artifact_hash = store_pcm(safe_artifact_path(self.settings.artifact_root, "synthetic-audio"), raw) if self.settings.mode == "mock" else None
                        if artifact_hash is not None:
                            self.journal.artifact(self.state.session_id, artifact_hash, {"artifact_hash": artifact_hash, "artifact_reference": f"synthetic-audio/{artifact_hash}.pcm16", "sample_rate": 24000, "channels": 1, "encoding": "signed_little_endian_pcm16", "synthetic": True, "kind": "diagnostic_output_tone"})
                        self.record("audio_sent", logical_turn_id=self.active_logical_turn, transport_turn_id=self.state.active_turn, response_id=event["response_id"], sample_offset=self.state.response_samples, sample_rate=24000, artifact_hash=artifact_hash, content_hash=content_hash)
                        await self.send("audio", {**event, "synthetic": self.settings.mode == "mock", "artifact_hash": artifact_hash, "content_hash": content_hash, "artifact_persisted": artifact_hash is not None})
                    elif kind == "task":
                        outcome = event.get("outcome", {})
                        self.turn_outcome = "off_script" if outcome.get("lexical_miss") else ("success" if outcome.get("ok") is True else "task_failure")
                        if outcome.get("effect_committed") and not outcome.get("duplicate"):
                            self.record("effect_commit", operation_id=outcome["logical_operation_id"])
                        await self.send("task", outcome)
                    elif kind == "transcript":
                        source = "mock_debug_text" if self.settings.mode == "mock" else "provider_transcript_unverified"
                        self.record("transcript", logical_turn_id=self.active_logical_turn, transport_turn_id=event.get("turn_id"), reason=source)
                        await self.send("transcript", {**event, "source": source})
                    else:
                        if event.get("kind") == "controller_span":
                            start, end = event.get("start_ns"), event.get("end_ns")
                            if event.get("clock_domain") != "server_monotonic_ns" or type(start) is not int or type(end) is not int or not 0 <= start <= end:
                                raise SessionViolation("invalid_controller_span")
                            self.record("controller_end", logical_turn_id=self.active_logical_turn, transport_turn_id=event.get("turn_id"), span_start_ns=start, span_end_ns=end, span_clock_domain="server_monotonic_ns", reason="adapter-observed controller span; translation identity noop")
                        self.record("provider_event", reason=str(event.get("kind", "unknown"))[:500])
                        await self.send("event", event)
                        if event.get("kind") == "response_done":
                            self.state.done(event.get("response_id"))
                            if self.state.state == "listening":
                                self.finish_turn(self.turn_outcome, "generation_complete_and_render_ack")
                            await self.send("state", {"state": self.state.state})
                if not self.state.closed:
                    await self.fail("adapter_failure")
            except asyncio.CancelledError:
                raise
            except (Exception,) as exc:
                await self.fail(exception_code(exc))

        async def receive(self, message: Any) -> None:
            self.state.check_live()
            if isinstance(message, Audio):
                raw = base64.b64decode(message.pcm16)
                self.state.admit_audio(message.turn_id, message.connection_epoch, message.response_epoch, message.sequence, len(raw) // 2)
                self.start_turn(message.turn_id)
                self.retention.append_input(raw)
                frame = message.model_dump()
                trace_fields: dict[str, Any] = {}
                if self.settings.mode == "live":
                    self.adapter.bind_turn(message.turn_id, self.recovery_logical_turn or message.turn_id)
                    content_hash = hashlib.sha256(raw).hexdigest()
                    frame["input_artifact_hash"] = content_hash
                    frame["input_artifact_persisted"] = False
                    trace_fields["content_hash"] = content_hash
                self.record("audio_received", logical_turn_id=self.active_logical_turn, transport_turn_id=message.turn_id, sample_offset=self.state.input_samples, sample_rate=24000, **trace_fields)
                await self.adapter.append_audio(frame)
            elif isinstance(message, Commit):
                if self.settings.mode == "live" and message.text is not None:
                    raise SessionViolation("live_debug_text_refused")
                self.state.commit(message.turn_id, message.connection_epoch, message.response_epoch)
                self.start_turn(message.turn_id)
                self.active_logical_turn = self.recovery_logical_turn or message.turn_id
                self.recovery_logical_turn = None
                # Recovery keeps the original commit boundary; it cannot buy a new deadline.
                if self.state.response_started_ns is None:
                    raise SessionViolation("response_deadline")
                original_start = self.logical_response_starts.setdefault(self.active_logical_turn, self.state.response_started_ns)
                self.state.response_started_ns = original_start
                if self.state.deadline_reason() == "response_deadline":
                    raise SessionViolation("response_deadline")
                self.turn_outcome = "task_failure"
                self.record("commit_received", logical_turn_id=self.active_logical_turn, transport_turn_id=message.turn_id)
                await self.send("state", {"state": "awaiting_response"})
                self.turn_task = asyncio.create_task(self.commit_adapter(message, self.active_logical_turn))
            elif isinstance(message, Interrupt):
                # Validate first; retain the old transport identity until its terminal.
                if message.response_epoch != self.state.response_epoch + 1 or not self.state.rendered_through_sample <= message.rendered_through_sample <= self.state.response_samples:
                    raise SessionViolation("invalid_cancel_fence")
                self.finish_turn("expected_interruption", "explicit_interrupt")
                previous, cutoff = self.state.interrupt(message.response_epoch, message.rendered_through_sample)
                if previous is not None:
                    self.retention.acknowledge(previous, cutoff)
                self.authority.set_epoch(self.state.trial_namespace, self.state.response_epoch)
                if self.turn_task and not self.turn_task.done():
                    self.turn_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await self.turn_task
                self.record("cancel_dispatch", response_id=previous, sample_offset=cutoff, sample_rate=24000)
                self.record("fence_applied", reason="relay epoch advanced before provider cancellation", response_id=previous, sample_offset=cutoff, sample_rate=24000)
                await asyncio.wait_for(self.adapter.cancel(previous, self.state.response_epoch, cutoff), timeout=2)
                await self.send("state", {"state": "listening", "fence_epoch": self.state.response_epoch, "task": self.authority.snapshot(self.state.trial_namespace)})
            elif isinstance(message, Rendered):
                self.state.rendered(message.response_id, message.rendered_through_sample)
                self.retention.acknowledge(message.response_id, message.rendered_through_sample)
                self.record("rendered_ack", logical_turn_id=self.active_logical_turn, transport_turn_id=self.current_transport_turn, response_id=message.response_id, sample_offset=message.rendered_through_sample, sample_rate=24000)
                if self.state.state == "listening":
                    self.finish_turn(self.turn_outcome, "generation_complete_and_render_ack")
                    await self.send("state", {"state": "listening"})
            elif isinstance(message, Resume):
                ack = message.rendered_ack
                old_response = self.state.active_response
                old_turn = self.current_transport_turn
                repeat = self.state.resume(message.connection_epoch, ack.response_id if ack else None, ack.rendered_through_sample if ack else None)
                if old_turn and old_turn not in self.turn_terminal_written:
                    # Resume clears the reducer's old identity; journal that attempt explicitly.
                    outcome = "transport_loss" if repeat else self.turn_outcome
                    if self.journal.finish_turn(self.state.session_id, old_turn, outcome, "reconnect_no_audio_replay"):
                        self.turn_terminal_written.add(old_turn)
                        self.record("turn_ended", logical_turn_id=self.active_logical_turn, transport_turn_id=old_turn, terminal_outcome=outcome, outcome_source="relay", reason="reconnect_no_audio_replay")
                if ack and ack.response_id == old_response and self.state.rendered_through_sample <= ack.rendered_through_sample <= self.state.response_samples:
                    self.retention.acknowledge(ack.response_id, ack.rendered_through_sample)
                self.recovery_logical_turn = self.active_logical_turn if repeat else None
                self.current_transport_turn = None
                self.authority.set_epoch(self.state.trial_namespace, self.state.response_epoch)
                await asyncio.wait_for(self.adapter.cancel(old_response, self.state.response_epoch, ack.rendered_through_sample if ack else 0), timeout=2)
                await asyncio.wait_for(self.adapter.resume(self.state.connection_epoch, ack.model_dump() if ack else None), timeout=2)
                if self.expiry:
                    self.expiry.cancel()
                self.record("reconnect", reason="no audio replay; task journal preserved")
                await self.send("task", self.authority.snapshot(self.state.trial_namespace))
                if repeat:
                    self.record("repeat_requested", reason="partial/lost rendered ACK")
                    await self.send("event", {"kind": "repeat_requested", "message": "Please repeat your last request; any booking remains recorded.", "audio_replayed": False})
                self.sender = asyncio.create_task(self.pump())
                await self.send("state", {"state": "listening"})
            elif isinstance(message, Ping):
                stamp = time.monotonic_ns()
                await self.send("pong", {"sequence": message.sequence, "browser_ms": message.browser_ms, "server_receive_ns": stamp, "server_send_ns": time.monotonic_ns(), "clock_domain": "server_monotonic_ns", "one_way_delay_known": False})
            elif isinstance(message, End):
                await self.shutdown("ended_by_user")
            elif isinstance(message, ClientStatus):
                await self.shutdown(message.outcome, outcome=message.outcome, source="client_report")
            else:
                raise SessionViolation("unexpected_hello")

        async def commit_adapter(self, message: Commit, logical_turn: str) -> None:
            try:
                await asyncio.wait_for(self.adapter.commit_turn(message.turn_id, text=message.text, logical_turn_id=logical_turn), timeout=10)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.fail(exception_code(exc))

        async def detach(self) -> None:
            if self.state.state in {"disconnected", "reconnecting"}:
                # An unsuccessful reconnect does not reset the original five-second window.
                self.state.state = "disconnected"
                return
            self.state.disconnect()
            for task in (self.sender, self.turn_task):
                if task and task is not asyncio.current_task():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            self.expiry = asyncio.create_task(expire(self))

        async def fail(self, code: str) -> None:
            await self.shutdown(code, outcome=failure_outcome(code), failed=True)

        async def shutdown(self, reason: str, *, outcome: str | None = None, source: str = "relay", failed: bool = False) -> None:
            if self.state.closed:
                return
            # Claim termination before the first await. Every competing caller is a no-op.
            self.state.end()
            # Fence durable effects as well as audio before any asynchronous cleanup.
            effect_fence = "applied"
            try:
                self.authority.set_epoch(self.state.trial_namespace, self.state.response_epoch)
            except Exception:
                effect_fence = "unknown"
                self.trace_integrity_failed = True
                with suppress(Exception):
                    self.record("integrity_error", reason="durable effect fence unavailable during terminal cleanup", observation="unknown")
            if failed:
                with suppress(Exception):
                    self.record("error", reason=reason, terminal_outcome=outcome, outcome_source=source)
                    await self.send("error", {"code": reason, "outcome": outcome, "message": "Session ended; inspect the labeled trace and task journal.", "terminal": True})
            for task in (self.sender, self.turn_task, self.expiry, self.watchdog):
                if task and task is not asyncio.current_task():
                    task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            try:
                await asyncio.wait_for(self.adapter.close(), timeout=2)
            except Exception:
                with suppress(Exception):
                    self.record("provider_event", reason="adapter_close_unknown; inspect retained liability", observation="unknown")
            admission.release(self.state.session_id)
            sessions.pop(self.state.session_id, None)
            task_outcome: dict[str, Any] = {"state": "unknown"}
            with suppress(Exception):
                task_outcome = self.authority.snapshot(self.state.trial_namespace)
            if outcome is None:
                outcome = ("success" if task_outcome.get("state") in {"booked", "complete"} else "abandoned") if reason == "ended_by_user" else failure_outcome(reason)
            try:
                retention = self.retention.finalize(self.settings.artifact_root, self.state.session_id)
            except Exception:
                retention = {"input_persisted": "unknown", "output_persisted": "unknown", "reason": "retention_write_failure; inspect approved artifact directory"}
            for key in ("input_artifact_hash", "output_artifact_hash"):
                digest = retention.get(key)
                if isinstance(digest, str):
                    with suppress(Exception):
                        self.journal.artifact(self.state.session_id, digest, {"artifact_hash": digest, "artifact_reference": f"approved-synthetic-audio/{digest}.pcm16", "kind": key, "retention": retention})
            with suppress(Exception):
                self.finish_turn("abandoned" if reason == "ended_by_user" else outcome, reason, source)
            # Missing trace terminal must stay unknown in durable metadata.
            with suppress(Exception):
                self.record("session_ended", reason=reason, terminal_outcome=outcome, outcome_source=source)
            trace_hash: str | None = None
            with suppress(Exception):
                trace_hash = self.trace.close()
            trace_metadata = self.trace.metadata()
            trace_metadata["integrity_error"] = self.trace_integrity_failed
            journal_saved = False
            try:
                self.journal.finish(self.state.session_id, outcome, reason, source, trace_hash, self.trace.durable_terminal and trace_hash is not None and not self.trace_integrity_failed)
                journal_saved = True
            except Exception:
                journal_saved = False
            finally:
                with suppress(Exception):
                    self.authority.close()
            with suppress(Exception):
                await self.send("ended", {"reason": reason, "outcome": outcome, "outcome_source": source, "task": task_outcome, "trace_hash": trace_hash, "trace": trace_metadata, "lifecycle_persisted": journal_saved, "effect_fence": effect_fence, "ledger": self.ledger.summary(), "retention": retention, "evidence_label": evidence_label, "evidence_reference": f"/sessions/{self.state.session_id}/evidence", "spoken_correctness": "unknown"})
            with suppress(Exception):
                await self.socket.close(code=1000)

    return app
