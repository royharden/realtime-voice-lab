"""TaskAuthority confirmation contract (plan section 2; F1 repair e73c226). NOT YET RUN."""
from __future__ import annotations

from voicelab.core.task import TaskAuthority


def _offer_and_select(authority: TaskAuthority, namespace: str, selection_turn: str) -> dict:
    authority.execute(namespace, 'list_slots', {}, logical_turn_id='turn-offer', response_epoch=0)
    selected = authority.execute(namespace, 'select_slot', {'slot_id': 'slot-a'},
                                 logical_turn_id=selection_turn, response_epoch=0)
    assert selected['ok'] is True
    assert selected['state'] == 'awaiting_confirmation'
    return selected


def _confirm(authority: TaskAuthority, namespace: str, selected: dict, turn: str) -> dict:
    return authority.execute(namespace, 'confirm_slot',
                             {'slot_id': selected['slot_id'], 'challenge': selected['challenge'],
                              'state_version': selected['state_version']},
                             logical_turn_id=turn, response_epoch=0, explicit_confirmation=True)


def test_same_logical_turn_select_and_confirm_refuses_without_booking(tmp_path):
    """what_bug_this_catches: F1 - a native model emitting select_slot and confirm_slot in the
    SAME logical turn (with a schema-valid challenge) committed a booking the human never
    confirmed. Plan section 2 requires a distinct current-epoch explicit affirmative turn;
    core/task.py refuses when turn == selection_turn."""
    authority = TaskAuthority(tmp_path / 'task.sqlite')
    try:
        selected = _offer_and_select(authority, 'trial-f1', 'turn-select')
        refused = _confirm(authority, 'trial-f1', selected, 'turn-select')
        assert refused['ok'] is False
        assert refused['code'] == 'explicit_current_confirmation_required'
        assert refused.get('effect_committed') is not True
        snapshot = authority.snapshot('trial-f1')
        assert snapshot['booking'] is None
        assert snapshot['state'] == 'awaiting_confirmation'
    finally:
        authority.close()


def test_distinct_confirmation_turn_books_exactly_once(tmp_path):
    """what_bug_this_catches: an over-broad F1 fix that refuses every confirmation, or a
    replayed confirmation that creates a second booking. A distinct turn commits once; the
    same logical operation replays its original outcome as a duplicate, never a new effect."""
    authority = TaskAuthority(tmp_path / 'task.sqlite')
    try:
        selected = _offer_and_select(authority, 'trial-ok', 'turn-select')
        booked = _confirm(authority, 'trial-ok', selected, 'turn-confirm')
        assert booked['ok'] is True and booked['effect_committed'] is True
        replay = _confirm(authority, 'trial-ok', selected, 'turn-confirm')
        assert replay['duplicate'] is True
        history = authority.history('trial-ok')
        assert history['task']['booking']['slot_id'] == 'slot-a'
        assert sum(op['command'] == 'confirm_slot' for op in history['original_operations']) == 1
        assert history['attempt_count'] == 4  # list, select, confirm, duplicate confirm
    finally:
        authority.close()


def test_confirmation_without_explicit_affirmative_refuses(tmp_path):
    """what_bug_this_catches: treating a schema-valid native tool proposal as human consent.
    explicit_confirmation must be exactly True (plan: tool calls are untrusted proposals)."""
    authority = TaskAuthority(tmp_path / 'task.sqlite')
    try:
        selected = _offer_and_select(authority, 'trial-implicit', 'turn-select')
        refused = authority.execute('trial-implicit', 'confirm_slot',
                                    {'slot_id': selected['slot_id'], 'challenge': selected['challenge'],
                                     'state_version': selected['state_version']},
                                    logical_turn_id='turn-confirm', response_epoch=0)
        assert refused['ok'] is False
        assert refused['code'] == 'explicit_current_confirmation_required'
        assert authority.snapshot('trial-implicit')['booking'] is None
    finally:
        authority.close()
