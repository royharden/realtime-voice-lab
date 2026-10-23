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

from voicelab.accounting.ledger import AttemptLedger
from voicelab.adapters import create_arm
from voicelab.adapters.live import validate_live_config
from voicelab.contracts import Audio, Commit, End, Hello, Interrupt, Ping, Rendered, Resume, ServerMessage, parse_client, serialize_server
from voicelab.core.session import Admission, SessionState, SessionViolation
from voicelab.core.retention import RetentionManifest, TrialRetention
from voicelab.core.task import TaskAuthority
from voicelab.core.trace import TraceWriter, store_pcm
from voicelab.security import safe_artifact_path, validate_boundary

EVIDENCE_LABEL = "keyless mock — diagnostic synthetic tone; no spoken-task acceptance"
LIVE_EVIDENCE_LABEL = "authorized live source — runtime unverified; independent acceptance pending"


@dataclass(frozen=True)
class Settings:
    nonce: str
    allowed_origin: str
    artifact_root: Path = Path(".agent-work/runtime")
    mode: str = "mock"
    live_config: dict[str, Any] | None = None
    enabled_arms: tuple[str, ...] = ("V", "C")
    retention_manifests: tuple[dict[str, Any], ...] = ()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings(nonce=os.environ.get("VOICELAB_NONCE", ""), allowed_origin=os.environ.get("VOICELAB_ALLOWED_ORIGIN", ""), artifact_root=Path(os.environ.get("VOICELAB_ARTIFACT_ROOT", ".agent-work/runtime")))
    if settings.mode not in {"mock", "live"} or not 16 <= len(settings.nonce) <= 128:
        raise ValueError("relay requires explicit mode and VOICELAB_NONCE of 16..128 characters")
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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for ancestor in (settings.artifact_root.absolute(), *settings.artifact_root.absolute().parents):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError("artifact_root_link_refused")
        settings.artifact_root.mkdir(parents=True, exist_ok=True)
        yield
        for relay in list(sessions.values()):
            await relay.shutdown("service_restart")

    app = FastAPI(title=f"Realtime Voice Lab — {settings.mode}", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"mode": settings.mode, "protocol_version": 1, "live_admission": settings.mode == "live", "runtime_verified": False, "evidence_label": evidence_label}

    @app.get("/diagnostics")
    async def diagnostics(request: Request) -> dict[str, object]:
        try:
            validate_boundary(request.headers.get("host", ""), request.headers.get("origin", ""), request.headers.get("x-voicelab-nonce", ""), settings.nonce, settings.allowed_origin)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="local boundary refused") from exc
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
        return {"scope": "own_server_process_only", "clock_domain": "server_monotonic_ns", "timestamp_ns": time.monotonic_ns(), "metrics": metrics}

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
                relay.socket = socket
                resumed = True
                await relay.send("ready", {"mode": settings.mode, "arm": first.arm, "evidence_label": evidence_label, "trial_namespace": relay.state.trial_namespace, "resume_required": True})
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
                message = parse_client(await asyncio.wait_for(socket.receive_text(), timeout=min(8, remaining)))
                if resumed and not isinstance(message, (Resume, End)):
                    raise SessionViolation("resume_required")
                await relay.receive(message)
                if isinstance(message, Resume):
                    resumed = False
        except WebSocketDisconnect:
            if relay and not relay.state.closed:
                await relay.detach()
                return
        except Exception as exc:
            if relay:
                code = str(exc) if isinstance(exc, SessionViolation) else ("silence_timeout" if isinstance(exc, asyncio.TimeoutError) else ("malformed_protocol" if isinstance(exc, ValueError) else "provider_or_transport_failure"))
                await relay.fail(code)
            else:
                await socket.close(code=1008, reason="admission refused")
        finally:
            if relay and relay.state.closed:
                admission.release(relay.state.session_id)
                sessions.pop(relay.state.session_id, None)

    # Expiration owns only this factory's admitted sessions, never other processes.
    async def expire(relay: Relay) -> None:
        await asyncio.sleep(5)
        if relay.state.state == "disconnected":
            await relay.shutdown("transport_loss")
            admission.release(relay.state.session_id)
            sessions.pop(relay.state.session_id, None)

    class Relay:
        def __init__(self, socket: WebSocket, config: Settings, state: SessionState, retention_manifest: RetentionManifest | None = None):
            self.socket, self.settings, self.state = socket, config, state
            self.retention = TrialRetention(retention_manifest)
            # Revalidate expiry and all manifests at admission, not only factory creation.
            self.adapter_config = ({**validate_live_config(config.live_config, state.arm), "mode": "live"} if config.mode == "live" else {"mode": "mock"})
            self.adapter_config.update(connection_epoch=0, response_epoch=0)
            budget = Decimal(self.adapter_config["budget_usd"]) if config.mode == "live" else Decimal("0")
            run_id = self.adapter_config["run_id"] if config.mode == "live" else "mock-local"
            ledger_name = f"attempts-{run_id}.sqlite3"
            self.authority = TaskAuthority(safe_artifact_path(config.artifact_root, "task.sqlite3"))
            try:
                self.ledger = AttemptLedger(safe_artifact_path(config.artifact_root, ledger_name), budget)
                self.adapter = create_arm(state.arm, task_authority=self.authority, trial_namespace=state.trial_namespace, ledger=self.ledger, config=self.adapter_config)
                self.trace = TraceWriter(safe_artifact_path(config.artifact_root, "traces"), state.session_id, state.trial_namespace, state.arm, run_id=run_id)
            except BaseException:
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

        async def send(self, kind: str, payload: dict[str, Any]) -> None:
            async with self.lock:
                message = ServerMessage(type=kind, session_id=self.state.session_id, connection_epoch=self.state.connection_epoch, response_epoch=self.state.response_epoch, sequence=self.sequence, payload=payload)
                self.sequence += 1
                await asyncio.wait_for(self.socket.send_text(serialize_server(message)), timeout=2)

        def record(self, kind: str, **fields: Any) -> None:
            self.trace.emit(kind, self.state.connection_epoch, self.state.response_epoch, **fields)

        async def open(self) -> None:
            await self.adapter.open({"mode": self.settings.mode})
            self.authority.set_epoch(self.state.trial_namespace, 0)
            self.record("session_started", reason=evidence_label)
            await self.send("ready", {"mode": self.settings.mode, "arm": self.state.arm, "evidence_label": evidence_label, "trial_namespace": self.state.trial_namespace})
            await self.send("state", {"state": "listening"})
            self.sender = asyncio.create_task(self.pump())

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
                    self.adapter_sequence = sequence
                    kind = event.get("type", "event")
                    if (kind in {"audio", "task", "transcript"} or event.get("kind") == "response_done") and event.get("turn_id") != self.state.active_turn:
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
                        self.record("audio_sent", response_id=event["response_id"], sample_offset=self.state.response_samples, sample_rate=24000, artifact_hash=artifact_hash, content_hash=content_hash)
                        await self.send("audio", {**event, "synthetic": self.settings.mode == "mock", "artifact_hash": artifact_hash, "content_hash": content_hash, "artifact_persisted": artifact_hash is not None})
                    elif kind == "task":
                        outcome = event.get("outcome", {})
                        if outcome.get("effect_committed"):
                            self.record("effect_commit", operation_id=outcome["logical_operation_id"])
                        await self.send("task", outcome)
                    elif kind == "transcript":
                        source = "mock_debug_text" if self.settings.mode == "mock" else "provider_transcript_unverified"
                        self.record("transcript", logical_turn_id=event.get("turn_id"), reason=source)
                        await self.send("transcript", {**event, "source": source})
                    else:
                        self.record("provider_event", reason=str(event.get("kind", "unknown"))[:500])
                        await self.send("event", event)
                        if event.get("kind") == "response_done":
                            self.state.done()
                            await self.send("state", {"state": self.state.state})
            except asyncio.CancelledError:
                raise
            except (Exception,) as exc:
                await self.fail(str(exc) if isinstance(exc, SessionViolation) else "adapter_failure")

        async def receive(self, message: Any) -> None:
            self.state.check_live()
            if isinstance(message, Audio):
                raw = base64.b64decode(message.pcm16)
                self.state.admit_audio(message.turn_id, message.connection_epoch, message.response_epoch, message.sequence, len(raw) // 2)
                self.retention.append_input(raw)
                frame = message.model_dump()
                trace_fields: dict[str, Any] = {}
                if self.settings.mode == "live":
                    self.adapter.bind_turn(message.turn_id, self.recovery_logical_turn or message.turn_id)
                    content_hash = hashlib.sha256(raw).hexdigest()
                    frame["input_artifact_hash"] = content_hash
                    frame["input_artifact_persisted"] = False
                    trace_fields["content_hash"] = content_hash
                self.record("audio_received", transport_turn_id=message.turn_id, sample_offset=self.state.input_samples, sample_rate=24000, **trace_fields)
                await self.adapter.append_audio(frame)
            elif isinstance(message, Commit):
                if self.settings.mode == "live" and message.text is not None:
                    raise SessionViolation("live_debug_text_refused")
                self.state.commit(message.turn_id, message.connection_epoch, message.response_epoch)
                self.active_logical_turn = self.recovery_logical_turn or message.turn_id
                self.recovery_logical_turn = None
                self.record("commit_received", logical_turn_id=self.active_logical_turn, transport_turn_id=message.turn_id)
                await self.send("state", {"state": "awaiting_response"})
                self.turn_task = asyncio.create_task(self.commit_adapter(message))
            elif isinstance(message, Interrupt):
                previous, cutoff = self.state.interrupt(message.response_epoch, message.rendered_through_sample)
                if previous is not None:
                    self.retention.acknowledge(previous, cutoff)
                self.authority.set_epoch(self.state.trial_namespace, self.state.response_epoch)
                if self.turn_task and not self.turn_task.done():
                    self.turn_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.turn_task
                self.record("cancel_dispatch", response_id=previous, sample_offset=cutoff, sample_rate=24000)
                await self.adapter.cancel(previous, self.state.response_epoch, cutoff)
                await self.send("state", {"state": "listening", "fence_epoch": self.state.response_epoch, "task": self.authority.snapshot(self.state.trial_namespace)})
            elif isinstance(message, Rendered):
                self.state.rendered(message.response_id, message.rendered_through_sample)
                self.retention.acknowledge(message.response_id, message.rendered_through_sample)
                self.record("rendered_ack", response_id=message.response_id, sample_offset=message.rendered_through_sample, sample_rate=24000)
                if self.state.state == "listening":
                    await self.send("state", {"state": "listening"})
            elif isinstance(message, Resume):
                ack = message.rendered_ack
                old_response = self.state.active_response
                repeat = self.state.resume(message.connection_epoch, ack.response_id if ack else None, ack.rendered_through_sample if ack else None)
                if ack and ack.response_id == old_response and self.state.rendered_through_sample <= ack.rendered_through_sample <= self.state.response_samples:
                    self.retention.acknowledge(ack.response_id, ack.rendered_through_sample)
                self.recovery_logical_turn = self.active_logical_turn if repeat else None
                self.authority.set_epoch(self.state.trial_namespace, self.state.response_epoch)
                await self.adapter.cancel(old_response, self.state.response_epoch, ack.rendered_through_sample if ack else 0)
                await self.adapter.resume(self.state.connection_epoch, ack.model_dump() if ack else None)
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
            else:
                raise SessionViolation("unexpected_hello")

        async def commit_adapter(self, message: Commit) -> None:
            try:
                await asyncio.wait_for(self.adapter.commit_turn(message.turn_id, text=message.text, logical_turn_id=self.active_logical_turn), timeout=10)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.fail("provider_error_or_timeout")

        async def detach(self) -> None:
            self.state.disconnect()
            for task in (self.sender, self.turn_task):
                if task and task is not asyncio.current_task():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            self.expiry = asyncio.create_task(expire(self))

        async def fail(self, code: str) -> None:
            with suppress(Exception):
                self.record("error", reason=code[:500])
                await self.send("error", {"code": code, "message": "Session ended; inspect the labeled trace and task journal.", "terminal": True})
            await self.shutdown(code)

        async def shutdown(self, reason: str) -> None:
            if self.state.closed:
                return
            self.state.end()
            for task in (self.sender, self.turn_task, self.expiry):
                if task and task is not asyncio.current_task():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
            try:
                await asyncio.wait_for(self.adapter.close(), timeout=2)
            except Exception:
                self.record("provider_event", reason="adapter_close_unknown; inspect retained liability")
            task_outcome = self.authority.snapshot(self.state.trial_namespace)
            try:
                retention = self.retention.finalize(self.settings.artifact_root, self.state.session_id)
            except Exception:
                retention = {"input_persisted": "unknown", "output_persisted": "unknown", "reason": "retention_write_failure; inspect approved artifact directory"}
            self.record("session_ended", reason=reason)
            trace_hash = self.trace.close()
            self.authority.close()
            with suppress(Exception):
                await self.send("ended", {"reason": reason, "task": task_outcome, "trace_hash": trace_hash, "ledger": self.ledger.summary(), "retention": retention, "evidence_label": evidence_label})
                await self.socket.close(code=1000)

    return app
