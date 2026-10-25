"""Selected plan sections 3–6: bounded playback, fences and recovery."""
import pytest

from voicelab.core.session import Admission, SessionState, SessionViolation


def playing():
    state = SessionState('session', 'trial', 'V')
    state.commit('turn-1', 0, 0)
    state.admit_output('response', 0, 0, 4800)
    return state


def test_cancel_fences_delayed_audio_and_old_commits():
    """what_bug_this_catches: delayed provider PCM or commits surviving a barge-in fence."""
    state = playing()
    assert state.interrupt(1, 0) == ('response', 0)
    assert not state.admit_output('response', 0, 0, 4800)
    with pytest.raises(SessionViolation, match='stale_epoch'):
        state.commit('late', 0, 0)
    state.commit('turn-2', 0, 1)
    assert state.admit_output('fresh', 1, 0, 4800)


def test_queue_bound_uses_unrendered_samples():
    """what_bug_this_catches: output producers building more than two seconds of queued PCM."""
    state = playing()
    for _ in range(9):
        assert state.admit_output('response', 0, 0, 4800)
    with pytest.raises(SessionViolation, match='output_queue_overflow'):
        state.admit_output('response', 0, 0, 1)
    state.rendered('response', 4800)
    assert state.admit_output('response', 0, 0, 4800)


def test_unknown_render_ack_allows_only_one_reprompt():
    """what_bug_this_catches: lost playback ACKs silently replaying audio repeatedly."""
    state = playing()
    state.disconnect()
    assert state.resume(1, None, None) is True
    state.commit('repeat-transport', 1, 1)
    state.admit_output('repeat-response', 1, 1, 4800)
    state.disconnect()
    with pytest.raises(SessionViolation, match='recovery_unknown'):
        state.resume(2, None, None)


@pytest.mark.parametrize('phase', ['listening', 'awaiting_response', 'playing', 'disconnected'])
def test_end_prevents_further_admission(phase):
    """what_bug_this_catches: End racing a delayed callback and reopening capture/generation."""
    state = SessionState('session', 'trial', 'C', state=phase)
    state.end()
    assert state.closed and state.state == 'closed'
    assert not state.admit_output('late', 0, 0, 1)
    with pytest.raises(SessionViolation, match='session_closed'):
        state.commit('late', 0, 1)


def test_three_sessions_refuse_fourth_then_release_capacity():
    """what_bug_this_catches: a fourth concurrent session bypassing the selected-plan cap."""
    admission = Admission()
    for name in ('one', 'two', 'three'):
        admission.admit(name)
    with pytest.raises(SessionViolation, match='session_capacity'):
        admission.admit('four')
    admission.release('two')
    admission.admit('four')
    assert admission.sessions == {'one', 'three', 'four'}
