"""UI <-> core wire protocol (architecture Part II §6.2, §27, §28, §35).

These are the only shapes that cross the WebSocket / REST boundary. They are
Pydantic models so ``just gen-types`` can emit a JSON Schema (and TypeScript
when a JS toolchain is present) into ``protocol/``.

Wire fields are camelCase (``turnId``, ``argsSummary``, ...) exactly as §6.2
shows; Python attributes stay snake_case via the alias generator. Serialize with
``model_dump(mode="json", by_alias=True)``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from aizen.domains import Domain
from aizen.llm.base import Usage
from aizen.orb import OrbState
from aizen.tools.broker import ConfirmRequest, ConfirmResponse
from aizen.verifier import VerifyResult


class WireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


# --- shared leaf types ---


class SourceRef(WireModel):
    title: str
    url: str | None = None
    path: str | None = None
    snippet: str | None = None


class ErrorEvent(WireModel):
    t: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool = True
    hint: str | None = None


# --- client -> core (§6.2) ---


class UserText(WireModel):
    t: Literal["user_text"] = "user_text"
    turn_id: str = ""
    text: str


class PttStart(WireModel):
    t: Literal["ptt_start"] = "ptt_start"


class PttStop(WireModel):
    t: Literal["ptt_stop"] = "ptt_stop"


class InterruptSpeech(WireModel):
    t: Literal["interrupt_speech"] = "interrupt_speech"


class CancelTurn(WireModel):
    t: Literal["cancel_turn"] = "cancel_turn"
    turn_id: str = ""


class ConfirmResponseEvent(WireModel):
    t: Literal["confirm_response"] = "confirm_response"
    request_id: str
    approve: bool
    scope: Literal["once", "session"] = "once"


class SetModel(WireModel):
    t: Literal["set_model"] = "set_model"
    profile: str


class PrivateMode(WireModel):
    t: Literal["private_mode"] = "private_mode"
    on: bool


class Pong(WireModel):
    """Transport heartbeat reply; not part of §6.2's app-level events."""

    t: Literal["pong"] = "pong"


ClientEvent = Annotated[
    UserText
    | PttStart
    | PttStop
    | InterruptSpeech
    | CancelTurn
    | ConfirmResponseEvent
    | SetModel
    | PrivateMode
    | Pong,
    Field(discriminator="t"),
]


# --- core -> client (§6.2 + §27 session_state + §28 confirm_request) ---


class SessionStateEvent(WireModel):
    t: Literal["session_state"] = "session_state"
    state: str
    seq: int
    detail: str | None = None


class OrbStateEvent(WireModel):
    t: Literal["orb_state"] = "orb_state"
    state: OrbState
    detail: str | None = None


class RouteEvent(WireModel):
    t: Literal["route"] = "route"
    domain: Domain
    toolset: list[str] = []
    hint: str | None = None


class TranscriptPartialEvent(WireModel):
    t: Literal["transcript_partial"] = "transcript_partial"
    text: str


class TranscriptFinalEvent(WireModel):
    t: Literal["transcript_final"] = "transcript_final"
    text: str


class AssistantDeltaEvent(WireModel):
    t: Literal["assistant_delta"] = "assistant_delta"
    turn_id: str
    text: str


class AssistantDoneEvent(WireModel):
    t: Literal["assistant_done"] = "assistant_done"
    turn_id: str
    usage: Usage = Usage()
    verified: bool = False
    domain: Domain = Domain.CHAT
    tainted: bool = False


class ToolStartEvent(WireModel):
    t: Literal["tool_start"] = "tool_start"
    call_id: str = ""
    name: str
    args_summary: str = ""


class ToolEndEvent(WireModel):
    t: Literal["tool_end"] = "tool_end"
    call_id: str = ""
    name: str = ""
    ok: bool = True
    ms: float = 0.0
    summary: str = ""
    tainted: bool = False


class SourcesEvent(WireModel):
    t: Literal["sources"] = "sources"
    turn_id: str
    items: list[SourceRef] = []


class ConfirmRequestEvent(WireModel):
    """§28 confirmation, carried to the UI as the §6.2 ``confirm_request``."""

    t: Literal["confirm_request"] = "confirm_request"
    request_id: str
    turn_id: str
    call_id: str
    tool: str
    tier: str
    risk: Literal["write", "exec", "network"] = "network"
    summary: str
    preview: str | None = None
    args_hash: str
    tainted: bool = False
    taint_sources: list[str] = []
    expires_at: str


class ModelStatusEvent(WireModel):
    t: Literal["model_status"] = "model_status"
    profile: str
    loaded: bool = False
    tokens_per_sec: float | None = None
    ram_mb: float | None = None


class IndexStatusEvent(WireModel):
    t: Literal["index_status"] = "index_status"
    pending: int = 0
    current: str | None = None


class Ping(WireModel):
    """Transport heartbeat; the client answers with ``pong``."""

    t: Literal["ping"] = "ping"


ServerEvent = Annotated[
    SessionStateEvent
    | OrbStateEvent
    | RouteEvent
    | TranscriptPartialEvent
    | TranscriptFinalEvent
    | AssistantDeltaEvent
    | AssistantDoneEvent
    | ToolStartEvent
    | ToolEndEvent
    | SourcesEvent
    | ConfirmRequestEvent
    | ModelStatusEvent
    | IndexStatusEvent
    | ErrorEvent
    | Ping,
    Field(discriminator="t"),
]


# §35: generated types must include these named models in addition to the
# event unions. ``ConfirmRequest``/``ConfirmResponse`` come from the broker and
# ``VerifyResult`` from the verifier so the UI binds to the same shapes.
PROTOCOL_MODELS: dict[str, Any] = {
    "ClientEvent": ClientEvent,
    "ServerEvent": ServerEvent,
    "ConfirmRequest": ConfirmRequest,
    "ConfirmResponse": ConfirmResponse,
    "VerifyResult": VerifyResult,
    "SourceRef": SourceRef,
    "ErrorEvent": ErrorEvent,
    "SessionStateEvent": SessionStateEvent,
    "AssistantDoneEvent": AssistantDoneEvent,
}


def dump_server_event(event: BaseModel) -> dict[str, Any]:
    """Serialize a server event for the wire (camelCase, JSON-safe)."""
    return event.model_dump(mode="json", by_alias=True)


__all__ = [
    "PROTOCOL_MODELS",
    "AssistantDeltaEvent",
    "AssistantDoneEvent",
    "CancelTurn",
    "ClientEvent",
    "ConfirmRequestEvent",
    "ConfirmResponseEvent",
    "ErrorEvent",
    "IndexStatusEvent",
    "InterruptSpeech",
    "ModelStatusEvent",
    "OrbStateEvent",
    "Ping",
    "Pong",
    "PrivateMode",
    "PttStart",
    "PttStop",
    "RouteEvent",
    "ServerEvent",
    "SessionStateEvent",
    "SetModel",
    "SourceRef",
    "SourcesEvent",
    "ToolEndEvent",
    "ToolStartEvent",
    "TranscriptFinalEvent",
    "TranscriptPartialEvent",
    "UserText",
    "WireModel",
    "dump_server_event",
]
