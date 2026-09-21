"""Session state machine (§27): only listed edges move; the backend owns state."""

from __future__ import annotations

import pytest
from aizen.orb import OrbState
from aizen.session import Session, SessionEvent, SessionState, orb_for, transition


@pytest.mark.parametrize(
    ("state", "event", "expected"),
    [
        (SessionState.IDLE, SessionEvent.PTT_START, SessionState.LISTENING),
        (SessionState.LISTENING, SessionEvent.PTT_STOP_SPEECH, SessionState.THINKING),
        (SessionState.LISTENING, SessionEvent.PTT_STOP_EMPTY, SessionState.IDLE),
        (SessionState.THINKING, SessionEvent.TOOL_CALL, SessionState.TOOL_CALL),
        (SessionState.THINKING, SessionEvent.CONFIRM_REQUIRED, SessionState.AWAITING_CONFIRM),
        (SessionState.AWAITING_CONFIRM, SessionEvent.CONFIRM_APPROVE, SessionState.TOOL_CALL),
        (SessionState.AWAITING_CONFIRM, SessionEvent.CONFIRM_DENY, SessionState.THINKING),
        (SessionState.TOOL_CALL, SessionEvent.TOOL_RESULT, SessionState.THINKING),
        (SessionState.THINKING, SessionEvent.ANSWER_VERIFIED, SessionState.SPEAKING),
        (SessionState.SPEAKING, SessionEvent.PLAYBACK_DRAINED, SessionState.IDLE),
        (SessionState.SPEAKING, SessionEvent.INTERRUPT_SPEECH, SessionState.IDLE),
        (SessionState.THINKING, SessionEvent.CANCEL_TURN, SessionState.IDLE),
        (SessionState.AWAITING_CONFIRM, SessionEvent.CANCEL_TURN, SessionState.IDLE),
        (SessionState.THINKING, SessionEvent.ERROR, SessionState.ERROR),
        (SessionState.ERROR, SessionEvent.ERROR_ACK, SessionState.IDLE),
    ],
)
def test_transitions(state: SessionState, event: SessionEvent, expected: SessionState) -> None:
    assert transition(state, event) is expected


def test_illegal_transition_is_noop() -> None:
    assert transition(SessionState.IDLE, SessionEvent.TOOL_RESULT) is SessionState.IDLE
    assert transition(SessionState.IDLE, SessionEvent.ANSWER_VERIFIED) is SessionState.IDLE


def test_orb_mapping_is_total() -> None:
    for state in SessionState:
        assert isinstance(orb_for(state), OrbState)
    assert orb_for(SessionState.IDLE) is OrbState.IDLE
    assert orb_for(SessionState.AWAITING_CONFIRM) is OrbState.THINKING
    assert orb_for(SessionState.SPEAKING) is OrbState.SPEAKING


async def test_session_apply_emits_sequenced_events() -> None:
    seen: list[dict[str, object]] = []

    async def on_state(payload: dict[str, object]) -> None:
        seen.append(payload)

    session = Session(on_state=on_state)
    await session.apply(SessionEvent.USER_TEXT)
    assert session.state is SessionState.THINKING
    assert session.seq == 1
    await session.apply(SessionEvent.TOOL_CALL, tool=OrbState.CALCULATING)
    assert session.state is SessionState.TOOL_CALL
    assert session.seq == 2
    assert seen[-1] == {"state": "TOOL_CALL", "seq": 2, "detail": "CALCULATING"}
    # no-op event does not bump seq or emit
    await session.apply(SessionEvent.USER_TEXT)
    assert session.seq == 2
    assert len(seen) == 2
