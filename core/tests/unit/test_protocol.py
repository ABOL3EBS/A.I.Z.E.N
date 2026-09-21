"""Wire protocol models (§6.2/§27/§28/§35)."""

from __future__ import annotations

import pytest
from aizen.api.protocol import (
    PROTOCOL_MODELS,
    AssistantDoneEvent,
    ClientEvent,
    ConfirmRequestEvent,
    ErrorEvent,
    ServerEvent,
    SessionStateEvent,
    dump_server_event,
)
from pydantic import TypeAdapter, ValidationError

_CLIENT = TypeAdapter(ClientEvent)
_SERVER = TypeAdapter(ServerEvent)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"t": "user_text", "turnId": "t1", "text": "hi"}, "user_text"),
        ({"t": "ptt_start"}, "ptt_start"),
        ({"t": "ptt_stop"}, "ptt_stop"),
        ({"t": "interrupt_speech"}, "interrupt_speech"),
        ({"t": "cancel_turn", "turnId": "t1"}, "cancel_turn"),
        ({"t": "confirm_response", "requestId": "r1", "approve": True}, "confirm_response"),
        ({"t": "set_model", "profile": "light"}, "set_model"),
        ({"t": "private_mode", "on": True}, "private_mode"),
        ({"t": "pong"}, "pong"),
    ],
)
def test_client_event_discriminates(raw: dict, expected: str) -> None:
    assert _CLIENT.validate_python(raw).t == expected


def test_unknown_client_event_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _CLIENT.validate_python({"t": "not_a_real_event"})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _CLIENT.validate_python({"t": "pong", "extra": 1})


def test_wire_fields_are_camel_case() -> None:
    from aizen.llm.base import Usage

    event = AssistantDoneEvent(turn_id="t1", usage=Usage(prompt_tokens=3), verified=True)
    dumped = dump_server_event(event)
    assert dumped["t"] == "assistant_done"
    assert dumped["turnId"] == "t1"
    assert dumped["usage"] == {"promptTokens": 3, "completionTokens": 0}
    assert "turn_id" not in dumped


def test_server_event_discriminator_covers_the_contract() -> None:
    schema = _SERVER.json_schema()
    mapping = set(schema["discriminator"]["mapping"])
    assert {
        "session_state",
        "orb_state",
        "route",
        "assistant_delta",
        "assistant_done",
        "tool_start",
        "tool_end",
        "sources",
        "confirm_request",
        "model_status",
        "error",
    } <= mapping


def test_generated_models_include_the_required_shapes() -> None:
    assert {"ConfirmRequest", "ConfirmResponse", "VerifyResult"} <= set(PROTOCOL_MODELS)


def test_session_state_serializes_seq_and_detail() -> None:
    dumped = dump_server_event(SessionStateEvent(state="THINKING", seq=4, detail="x"))
    assert dumped == {"t": "session_state", "state": "THINKING", "seq": 4, "detail": "x"}


def test_confirm_request_carries_the_binding_fields() -> None:
    event = ConfirmRequestEvent(
        request_id="r1",
        turn_id="t1",
        call_id="c1",
        tool="files.create",
        tier="T4",
        risk="write",
        summary="write a file",
        args_hash="abc",
        expires_at="2026-01-01T00:00:00+00:00",
    )
    dumped = dump_server_event(event)
    assert dumped["argsHash"] == "abc"
    assert dumped["risk"] == "write"


def test_error_event_has_code_and_recoverable() -> None:
    dumped = dump_server_event(ErrorEvent(code="busy", message="nope", recoverable=True))
    assert dumped["t"] == "error"
    assert dumped["code"] == "busy"
    assert dumped["recoverable"] is True
