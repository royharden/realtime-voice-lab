"""Pure lifecycle reducer. One active turn, bounded ingress and recovery."""
from __future__ import annotations

from dataclasses import dataclass, field
import time


class SessionViolation(ValueError):
    pass


@dataclass
class SessionState:
    session_id: str
    trial_namespace: str
    arm: str
    connection_epoch: int = 0
    response_epoch: int = 0
    state: str = "listening"
    active_turn: str | None = None
    active_response: str | None = None
    input_sequence: int = -1
    input_samples: int = 0
    response_samples: int = 0
    rendered_through_sample: int = 0
    logical_turns: int = 0
    reconnect_attempts: int = 0
    repeat_requested: bool = False
    closed: bool = False
    started_ns: int = field(default_factory=time.monotonic_ns)
    disconnect_ns: int | None = None
    response_started_ns: int | None = None
    committed_turns: set[str] = field(default_factory=set)
    started_turns: set[str] = field(default_factory=set)
    generation_done: bool = False
    last_input_ns: int = field(default_factory=time.monotonic_ns)
    response_turn: str | None = None
    calibration_id: str | None = None
    calibration_deadline_ns: int | None = None
    calibration_phases: dict[str, dict] = field(default_factory=dict)

    def check_live(self) -> None:
        if self.closed:
            raise SessionViolation("session_closed")
        if time.monotonic_ns() - self.started_ns > 120_000_000_000:
            raise SessionViolation("task_deadline")

    def deadline_reason(self) -> str | None:
        """Independent of new packets; stalled streams cannot evade a deadline."""
        now = time.monotonic_ns()
        if self.closed:
            return None
        if now - self.started_ns >= 120_000_000_000:
            return "task_deadline"
        if self.calibration_deadline_ns is not None and now >= self.calibration_deadline_ns:
            return "calibration_invalid"
        if self.state in {"awaiting_response", "playing"} and self.response_started_ns is not None and now - self.response_started_ns >= 10_000_000_000:
            return "response_deadline"
        if self.state in {"listening", "capturing"} and now - self.last_input_ns >= 8_000_000_000:
            return "silence_timeout"
        return None

    def calibration(self, phase_id: str, action: str) -> dict:
        self.check_live()
        if self.deadline_reason() is not None:
            raise SessionViolation(self.deadline_reason())
        previous = self.calibration_phases.get(phase_id)
        # Duplicate requests return original boundaries; they never renew any deadline.
        if previous and (action == 'begin' or previous['status'] == 'ended'):
            return {**previous, 'duplicate': True}
        if action == 'begin':
            if (len(self.calibration_phases) >= 2 or self.state != 'listening' or self.active_turn is not None
                    or (self.active_response is not None and (not self.generation_done
                        or self.rendered_through_sample != self.response_samples))):
                raise SessionViolation('calibration_phase_refused')
            now = time.monotonic_ns()
            self.calibration_id = phase_id
            self.calibration_deadline_ns = min(now + 30_000_000_000, self.started_ns + 120_000_000_000)
            self.state = 'calibrating'
            previous = {'phase_id': phase_id, 'status': 'active', 'started_ns': now,
                'deadline_ns': self.calibration_deadline_ns, 'ended_ns': None,
                'session_deadline_ns': self.started_ns + 120_000_000_000,
                'connection_epoch': self.connection_epoch, 'response_epoch': self.response_epoch,
                'absolute_cap_ms': 30000, 'phase_limit': 2, 'clock_domain': 'server_monotonic_ns'}
            self.calibration_phases[phase_id] = previous
        elif action == 'end' and self.calibration_id == phase_id and previous is not None:
            previous.update(status='ended', ended_ns=time.monotonic_ns())
            self.calibration_id = None
            self.calibration_deadline_ns = None
            self._listen()
        else:
            raise SessionViolation('calibration_phase_conflict')
        return {**previous, 'duplicate': False}

    def admit_audio(self, turn: str, connection_epoch: int, response_epoch: int, sequence: int, samples: int) -> None:
        self.check_live()
        if (connection_epoch, response_epoch) != (self.connection_epoch, self.response_epoch):
            raise SessionViolation("stale_epoch")
        if self.state not in {"listening", "capturing"}:
            raise SessionViolation("response_active_interrupt_first")
        if turn in self.committed_turns or (self.active_turn is not None and self.active_turn != turn):
            raise SessionViolation("turn_conflict")
        new_turn = self.active_turn is None
        if new_turn and turn in self.started_turns:
            raise SessionViolation("reused_transport_turn")
        if new_turn and self.logical_turns >= 8:
            raise SessionViolation("turn_limit")
        if sequence != self.input_sequence + 1:
            raise SessionViolation("audio_sequence_integrity")
        if type(samples) is not int or not 1 <= samples <= 4_800:
            raise SessionViolation("invalid_audio_samples")
        if self.input_samples + samples > 24_000 * 15:
            raise SessionViolation("input_audio_overflow")
        if new_turn:
            self.started_turns.add(turn)
            self.logical_turns += 1
        self.active_turn = turn
        self.input_sequence = sequence
        self.input_samples += samples
        self.last_input_ns = time.monotonic_ns()
        self.state = "capturing"

    def commit(self, turn: str, connection_epoch: int, response_epoch: int) -> None:
        self.check_live()
        if (connection_epoch, response_epoch) != (self.connection_epoch, self.response_epoch):
            raise SessionViolation("stale_epoch")
        if self.state not in {"listening", "capturing"} or turn in self.committed_turns or self.active_turn not in {None, turn}:
            raise SessionViolation("duplicate_or_active_turn")
        if self.active_turn is None:
            if turn in self.started_turns:
                raise SessionViolation("reused_transport_turn")
            if self.logical_turns >= 8:
                raise SessionViolation("turn_limit")
            self.started_turns.add(turn)
            self.logical_turns += 1
        self.committed_turns.add(turn)
        self.active_turn = turn
        self.response_turn = turn
        self.active_response = None
        self.response_samples = 0
        self.rendered_through_sample = 0
        self.response_started_ns = time.monotonic_ns()
        self.generation_done = False
        self.state = "awaiting_response"

    def admit_output(self, response_id: str, epoch: int, connection_epoch: int, samples: int) -> bool:
        if self.closed or epoch != self.response_epoch or connection_epoch != self.connection_epoch:
            return False
        self.check_live()
        if self.state not in {"awaiting_response", "playing"}:
            return False
        if self.active_response not in {None, response_id}:
            raise SessionViolation("multiple_active_responses")
        if self.generation_done:
            raise SessionViolation("audio_after_response_done")
        if type(samples) is not int or not 1 <= samples <= 4_800:
            raise SessionViolation("invalid_audio_samples")
        if self.response_started_ns is None or time.monotonic_ns() - self.response_started_ns > 10_000_000_000:
            raise SessionViolation("response_deadline")
        if self.response_samples + samples - self.rendered_through_sample > 48_000:
            raise SessionViolation("output_queue_overflow")
        if self.response_samples + samples > 240_000:
            raise SessionViolation("output_duration_overflow")
        self.active_response = response_id
        self.response_samples += samples
        self.state = "playing"
        return True

    def validate_rendered(self, response_id: str, sample: int) -> None:
        self.check_live()
        if self.deadline_reason() == "response_deadline":
            raise SessionViolation("response_deadline")
        if self.state not in {"playing", "awaiting_response"} or response_id != self.active_response or sample < self.rendered_through_sample or sample > self.response_samples:
            raise SessionViolation("invalid_rendered_ack")

    def rendered(self, response_id: str, sample: int) -> None:
        self.validate_rendered(response_id, sample)
        self.rendered_through_sample = sample
        if self.generation_done and sample == self.response_samples:
            self._listen()

    def interrupt(self, next_epoch: int, cutoff: int) -> tuple[str | None, int]:
        self.check_live()
        if self.calibration_id is not None:
            raise SessionViolation('calibration_phase_active')
        if next_epoch != self.response_epoch + 1 or cutoff < self.rendered_through_sample or cutoff > self.response_samples:
            raise SessionViolation("invalid_cancel_fence")
        previous = self.active_response
        self.response_epoch = next_epoch
        self.active_response = None
        self.response_turn = None
        self.response_started_ns = None
        self._listen()
        return previous, cutoff

    def _listen(self) -> None:
        self.state = "listening"
        self.active_turn = None
        self.input_sequence = -1
        self.input_samples = 0
        self.last_input_ns = time.monotonic_ns()

    def done(self, response_id: str) -> None:
        # Keep response/sample acknowledgement identity after generation terminates.
        self.check_live()
        if self.deadline_reason() == "response_deadline":
            raise SessionViolation("response_deadline")
        if self.state not in {"awaiting_response", "playing"} or self.generation_done or self.active_response not in {None, response_id}:
            raise SessionViolation("invalid_response_done")
        if not self.response_samples:
            raise SessionViolation("missing_response_audio")
        self.generation_done = True
        if self.rendered_through_sample == self.response_samples:
            self._listen()
        else:
            self.state = "playing"

    def disconnect(self) -> None:
        self.check_live()
        # A disconnected calibration cannot acquire a fresh phase deadline on reconnect.
        if self.calibration_id is not None:
            raise SessionViolation('calibration_invalid')
        self.state = "disconnected"
        self.disconnect_ns = time.monotonic_ns()

    def resume(self, epoch: int, response_id: str | None, rendered: int | None) -> bool:
        self.check_live()
        if self.state not in {"disconnected", "reconnecting"} or self.disconnect_ns is None or epoch != self.connection_epoch + 1 or self.reconnect_attempts >= 2 or time.monotonic_ns() - self.disconnect_ns > 5_000_000_000:
            raise SessionViolation("recovery_exhausted")
        complete = (self.active_response is None and self.active_turn is None) or (self.generation_done and self.active_response is not None and response_id == self.active_response and rendered == self.response_samples)
        if not complete and self.repeat_requested:
            raise SessionViolation("recovery_unknown")
        self.repeat_requested |= not complete
        self.reconnect_attempts += 1
        self.connection_epoch = epoch
        self.response_epoch += 1
        self.active_response = None
        self.response_turn = None
        self.response_started_ns = None
        self._listen()
        return not complete

    def end(self) -> None:
        self.closed = True
        self.response_epoch += 1
        self.state = "closed"
        self.active_turn = None
        self.active_response = None


class Admission:
    def __init__(self, maximum: int = 3):
        self.maximum = maximum
        self.sessions: set[str] = set()

    def admit(self, session_id: str) -> None:
        if session_id in self.sessions or len(self.sessions) >= self.maximum:
            raise SessionViolation("session_capacity")
        self.sessions.add(session_id)

    def release(self, session_id: str) -> None:
        self.sessions.discard(session_id)
