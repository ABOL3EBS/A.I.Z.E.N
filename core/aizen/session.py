"""Session state machine (architecture Part II §27 - authoritative).

The backend owns state: the core emits ``session_state{state, seq, detail?}``
events and the UI never infers state from text, timing, or audio. The orb is a
pure function of ``session_state`` (plus audio amplitude in the webview).

Top-level module on purpose: the tool execution wrapper imports it, and it must
not pull in the ``agent`` package (same reason as ``cancellation.py``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from aizen.orb import OrbState


class SessionState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    TOOL_CALL = "TOOL_CALL"
    AWAITING_CONFIRM = "AWAITING_CONFIRM"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


class SessionEvent(StrEnum):
    PTT_START = "ptt_start"
    USER_TEXT = "user_text"
    PTT_STOP_SPEECH = "ptt_stop_speech"
    PTT_STOP_EMPTY = "ptt_stop_empty"
    TOOL_CALL = "tool_call"
    CONFIRM_REQUIRED = "confirm_required"
    CONFIRM_APPROVE = "confirm_approve"
    CONFIRM_DENY = "confirm_deny"
    TOOL_RESULT = "tool_result"
    ANSWER_VERIFIED = "answer_verified"
    VOICE_OFF = "voice_off"
    PLAYBACK_DRAINED = "playback_drained"
    INTERRUPT_SPEECH = "interrupt_speech"
    CANCEL_TURN = "cancel_turn"
    ERROR = "error"
    ERROR_ACK = "error_ack"


# §27 transition table. Only listed edges are legal; everything else is a no-op
# (keeps the reducer total and testable).
_TRANSITIONS: dict[tuple[SessionState, SessionEvent], SessionState] = {
    (SessionState.IDLE, SessionEvent.PTT_START): SessionState.LISTENING,
    (SessionState.IDLE, SessionEvent.USER_TEXT): SessionState.THINKING,
    (SessionState.LISTENING, SessionEvent.PTT_STOP_SPEECH): SessionState.THINKING,
    (SessionState.LISTENING, SessionEvent.PTT_STOP_EMPTY): SessionState.IDLE,
    (SessionState.THINKING, SessionEvent.TOOL_CALL): SessionState.TOOL_CALL,
    (SessionState.THINKING, SessionEvent.CONFIRM_REQUIRED): SessionState.AWAITING_CONFIRM,
    (SessionState.AWAITING_CONFIRM, SessionEvent.CONFIRM_APPROVE): SessionState.TOOL_CALL,
    (SessionState.AWAITING_CONFIRM, SessionEvent.CONFIRM_DENY): SessionState.THINKING,
    (SessionState.TOOL_CALL, SessionEvent.TOOL_RESULT): SessionState.THINKING,
    (SessionState.THINKING, SessionEvent.ANSWER_VERIFIED): SessionState.SPEAKING,
    (SessionState.THINKING, SessionEvent.VOICE_OFF): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.PLAYBACK_DRAINED): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.INTERRUPT_SPEECH): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.PTT_START): SessionState.LISTENING,
    (SessionState.LISTENING, SessionEvent.CANCEL_TURN): SessionState.IDLE,
    (SessionState.THINKING, SessionEvent.CANCEL_TURN): SessionState.IDLE,
    (SessionState.TOOL_CALL, SessionEvent.CANCEL_TURN): SessionState.IDLE,
    (SessionState.AWAITING_CONFIRM, SessionEvent.CANCEL_TURN): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.CANCEL_TURN): SessionState.IDLE,
    (SessionState.LISTENING, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.THINKING, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.TOOL_CALL, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.AWAITING_CONFIRM, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.SPEAKING, SessionEvent.ERROR): SessionState.ERROR,
    (SessionState.ERROR, SessionEvent.PTT_START): SessionState.LISTENING,
    (SessionState.ERROR, SessionEvent.USER_TEXT): SessionState.THINKING,
    (SessionState.ERROR, SessionEvent.ERROR_ACK): SessionState.IDLE,
}


def transition(state: SessionState, event: SessionEvent) -> SessionState:
    return _TRANSITIONS.get((state, event), state)


def orb_for(state: SessionState) -> OrbState:
    """§27 deterministic orb mapping (awaiting confirm = THINKING with accent)."""
    return {
        SessionState.IDLE: OrbState.IDLE,
        SessionState.LISTENING: OrbState.LISTENING,
        SessionState.THINKING: OrbState.THINKING,
        SessionState.AWAITING_CONFIRM: OrbState.THINKING,
        SessionState.TOOL_CALL: OrbState.THINKING,  # refined by the tool's orb_state
        SessionState.SPEAKING: OrbState.SPEAKING,
        SessionState.ERROR: OrbState.ERROR,
    }[state]


class Session:
    """Owning state machine; every transition bumps ``seq`` and emits an event."""

    def __init__(
        self,
        *,
        on_state: Callable[[dict[str, object]], Awaitable[None]] | None = None,
    ) -> None:
        self._on_state = on_state
        self.state: SessionState = SessionState.IDLE
        self.seq: int = 0

    async def apply(
        self,
        event: SessionEvent,
        *,
        detail: str | None = None,
        tool: OrbState | None = None,
    ) -> SessionState:
        new = transition(self.state, event)
        if new is self.state:
            return self.state
        self.state = new
        self.seq += 1
        payload: dict[str, object] = {"state": self.state.value, "seq": self.seq}
        if detail is not None:
            payload["detail"] = detail
        if tool is not None and self.state is SessionState.TOOL_CALL:
            payload["detail"] = tool.value
        if self._on_state is not None:
            await self._on_state(payload)
        return self.state
