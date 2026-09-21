"""API integration tests: WS protocol, auth/Origin, busy/cancel, persistence.

The agent loop runs against a scripted fake provider, so these tests exercise
the real loop, broker, verifier and session state machine — only the model is
fake. One ``live`` test hits a real Ollama and is skipped unless
``AIZEN_LIVE_OLLAMA=1``.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aizen.agent import AgentLoop, CancellationToken, RuleRouter
from aizen.api import create_app
from aizen.config import RouteDecision, Settings
from aizen.domains import Domain
from aizen.facts import text_fact
from aizen.llm.base import (
    GenOptions,
    LLMEvent,
    LLMEventKind,
    LLMProvider,
    Message,
    ModelInfo,
    ProviderHealth,
    ToolCall,
    ToolSchema,
)
from aizen.storage import Database
from aizen.tools import (
    FunctionTool,
    PermissionTier,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

LIVE = os.environ.get("AIZEN_LIVE_OLLAMA") == "1"

_ORIGIN = {"origin": "http://localhost:1420"}


class FakeProvider(LLMProvider):
    """Yields scripted event lists in order; optionally blocks until canceled."""

    def __init__(self, scripts: list[list[LLMEvent]] | None = None, *, block: bool = False):
        self.scripts = deque(scripts or [])
        self.requests: list[list[Message]] = []
        self.block = block

    async def chat_stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        options: GenOptions,
        cancel: CancellationToken | None = None,
    ) -> AsyncIterator[LLMEvent]:
        self.requests.append(list(messages))
        if self.block:
            # Test fake: block until the loop cancels us; there is no Event to wait on.
            while cancel is None or not cancel.is_cancelled():  # noqa: ASYNC110
                await asyncio.sleep(0.01)
            if cancel is not None:
                cancel.check()
        events = self.scripts.popleft() if self.scripts else [LLMEvent(kind=LLMEventKind.DONE)]
        for event in events:
            yield event

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        return []

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, ms=0.5)


class ChatRouter(RuleRouter):
    def route(self, text: str) -> RouteDecision:
        return RouteDecision(domain=Domain.CHAT, toolset=())


class FinanceRouter(RuleRouter):
    def route(self, text: str) -> RouteDecision:
        return RouteDecision(
            domain=Domain.FINANCE, toolset=("finance.describe", "system.calculate")
        )


class SystemRouter(RuleRouter):
    def route(self, text: str) -> RouteDecision:
        return RouteDecision(domain=Domain.SYSTEM, toolset=("test.write",))


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    base: dict[str, object] = {
        "api_token": "test-token",
        "data_dir": tmp_path / "data",
        "content_dir": tmp_path / "content",
        "ollama_base_url": "http://127.0.0.1:1",
        "heartbeat_interval_s": 30.0,
        "ttft_timeout_s": 30.0,
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _make(
    tmp_path: Path,
    *,
    scripts: list[list[LLMEvent]] | None = None,
    block: bool = False,
    router: RuleRouter | None = None,
    registry: ToolRegistry | None = None,
    **kwargs: object,
) -> tuple[TestClient, AgentLoop, FakeProvider]:
    settings = _settings(tmp_path, **kwargs)
    provider = FakeProvider(scripts, block=block)
    loop = AgentLoop(
        provider,
        router=router or ChatRouter(),
        registry=registry or ToolRegistry.load_defaults(),
        settings=settings,
    )
    return TestClient(create_app(settings, loop=loop)), loop, provider


def _token(text: str) -> LLMEvent:
    return LLMEvent(kind=LLMEventKind.TOKEN, text=text)


def _done() -> LLMEvent:
    return LLMEvent(kind=LLMEventKind.DONE)


def _tool_call(name: str, args: dict[str, object]) -> LLMEvent:
    return LLMEvent(
        kind=LLMEventKind.TOOL_CALL,
        tool_call=ToolCall(id="call-1", name=name, arguments=args),
    )


def _recv_until(ws, event_type: str, *, code: str | None = None) -> list[dict]:
    """Read frames until one matches; returns everything read (including it)."""
    seen: list[dict] = []
    for _ in range(100):
        frame = ws.receive_json()
        seen.append(frame)
        if frame.get("t") == event_type and (code is None or frame.get("code") == code):
            return seen
    raise AssertionError(f"never saw {event_type!r} (code={code!r}); saw {seen}")


def _recv_until_state(ws, state: str) -> list[dict]:
    """Read frames until ``session_state`` reaches ``state``."""
    seen: list[dict] = []
    for _ in range(100):
        frame = ws.receive_json()
        seen.append(frame)
        if frame.get("t") == "session_state" and frame.get("state") == state:
            return seen
    raise AssertionError(f"never saw session_state {state!r}; saw {seen}")


def _wait_until(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# --- REST ---


def test_health_is_ok(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "version" in body


def test_turn_requires_token(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with client:
        assert client.post("/v1/turn", json={"text": "hi"}).status_code == 401


def test_turn_rejects_bad_origin(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with client:
        response = client.post(
            "/v1/turn",
            json={"text": "hi"},
            headers={"Authorization": "Bearer test-token", "origin": "http://evil.example"},
        )
        assert response.status_code == 403


def test_turn_with_token_answers(tmp_path) -> None:
    client, _, _ = _make(tmp_path, scripts=[[_token("Hi!"), _done()]])
    with client:
        response = client.post(
            "/v1/turn",
            json={"text": "hello"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["result"]["answer"] == "Hi!"
        assert body["result"]["verified"] is True
        assert body["conversation_id"] >= 1


def test_turn_maps_provider_failure_to_recoverable_error(tmp_path) -> None:
    # Default loop points at an unreachable Ollama (port 1).
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/turn",
            json={"text": "hello"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 502
        body = response.json()
        assert body["code"] == "provider_unavailable"
        assert body["recoverable"] is True
        assert "Ollama" in body["hint"]


def test_turn_restores_conversation_context(tmp_path) -> None:
    client, _, provider = _make(
        tmp_path, scripts=[[_token("Hi!"), _done()], [_token("Again."), _done()]]
    )
    headers = {"Authorization": "Bearer test-token"}
    with client:
        first = client.post("/v1/turn", json={"text": "hello"}, headers=headers).json()
        conversation_id = first["conversation_id"]
        client.post(
            "/v1/turn",
            json={"text": "one more", "conversation_id": conversation_id},
            headers=headers,
        )
    contents = [m.content for m in provider.requests[-1]]
    assert "hello" in contents and "Hi!" in contents and "one more" in contents


def test_private_mode_does_not_persist_messages(tmp_path) -> None:
    client, _, _ = _make(tmp_path, scripts=[[_token("Hi!"), _done()]])
    headers = {"Authorization": "Bearer test-token"}
    with client:
        body = client.post(
            "/v1/turn", json={"text": "secret", "private": True}, headers=headers
        ).json()
    # The app's connection lives in the event-loop thread; read via a fresh one.
    db = Database(tmp_path / "data" / "aizen.db")
    try:
        rows = db.fetchall(
            "SELECT * FROM messages WHERE conversation_id=?", (body["conversation_id"],)
        )
    finally:
        db.close()
    assert rows == []


# --- WebSocket ---


def test_websocket_requires_token(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with client, pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws?token=wrong"):
        pass


def test_websocket_rejects_bad_origin(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with (
        client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws?token=test-token", headers={"origin": "http://evil.example"}),
    ):
        pass


def test_websocket_sends_initial_session_state(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        assert ws.receive_json() == {
            "t": "session_state",
            "state": "IDLE",
            "seq": 0,
            "detail": None,
        }


def test_websocket_chat_streams_deltas_then_done(tmp_path) -> None:
    client, _, _ = _make(tmp_path, scripts=[[_token("Hel"), _token("lo"), _done()]])
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        ws.receive_json()  # initial session_state
        ws.send_json({"t": "user_text", "turnId": "t1", "text": "hello"})
        seen = _recv_until(ws, "assistant_done")
    deltas = [f["text"] for f in seen if f["t"] == "assistant_delta"]
    done = seen[-1]
    assert "".join(deltas) == "Hello"
    assert done["turnId"] == "t1"
    assert done["verified"] is True


def test_websocket_tool_route_never_streams_unverified_text(tmp_path) -> None:
    wrong = "The result is 9999."
    client, _, _ = _make(
        tmp_path,
        router=FinanceRouter(),
        scripts=[
            [_tool_call("system.calculate", {"expression": "20 * 150"}), _done()],
            [_token(wrong), _done()],
            [_token(wrong), _done()],
        ],
    )
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"t": "user_text", "turnId": "t1", "text": "20 times 150"})
        seen = _recv_until(ws, "assistant_done")
    deltas = [f["text"] for f in seen if f["t"] == "assistant_delta"]
    assert not any("9999" in text for text in deltas)
    assert any("3000" in text for text in deltas)
    assert seen[-1]["verified"] is False


def test_websocket_second_turn_while_busy_is_rejected(tmp_path) -> None:
    client, _, _ = _make(tmp_path, block=True)
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"t": "user_text", "turnId": "t1", "text": "hello"})
        _recv_until(ws, "session_state", code=None)  # THINKING
        ws.send_json({"t": "user_text", "turnId": "t2", "text": "again"})
        seen = _recv_until(ws, "error", code="busy")
    assert seen[-1]["code"] == "busy"
    assert seen[-1]["recoverable"] is True


def test_websocket_cancel_turn_reaches_idle(tmp_path) -> None:
    client, loop, _ = _make(tmp_path, block=True)
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"t": "user_text", "turnId": "t1", "text": "hello"})
        assert _wait_until(lambda: loop.is_busy)
        ws.send_json({"t": "cancel_turn", "turnId": "t1"})
        assert _wait_until(lambda: not loop.is_busy)
        seen = _recv_until_state(ws, "IDLE")
    assert any(f["t"] == "session_state" and f["state"] == "IDLE" for f in seen)


def test_websocket_disconnect_cancels_the_active_turn(tmp_path) -> None:
    client, loop, _ = _make(tmp_path, block=True, client_timeout_s=0.05)
    with client:
        with client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
            ws.receive_json()
            ws.send_json({"t": "user_text", "turnId": "t1", "text": "hello"})
            assert _wait_until(lambda: loop.is_busy)
        assert _wait_until(lambda: not loop.is_busy, timeout=3.0)


class _WriteArgs(BaseModel):
    path: str


async def _write(args: _WriteArgs, ctx: ToolContext) -> ToolResult:
    return ToolResult(facts=[text_fact("done")])


def test_websocket_confirmation_round_trip(tmp_path, monkeypatch) -> None:
    from aizen import domains

    monkeypatch.setitem(domains.DOMAIN_TOOLSETS, Domain.SYSTEM, ("test.write",))
    registry = ToolRegistry.load_defaults()
    registry.register(
        FunctionTool(
            _write,
            ToolSpec(
                name="test.write",
                domain=Domain.SYSTEM,
                description="write a file",
                args_model=_WriteArgs,
                permission=PermissionTier.T4,
            ),
        )
    )
    client, _, _ = _make(
        tmp_path,
        router=SystemRouter(),
        registry=registry,
        scripts=[
            [_tool_call("test.write", {"path": "notes.txt"}), _done()],
            [_token("Wrote it."), _done()],
        ],
    )
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"t": "user_text", "turnId": "t1", "text": "write notes"})
        seen = _recv_until(ws, "confirm_request")
        request = seen[-1]
        assert request["tier"] == "T4"
        assert request["risk"] == "write"
        assert request["argsHash"]
        ws.send_json(
            {
                "t": "confirm_response",
                "requestId": request["requestId"],
                "approve": True,
                "scope": "once",
            }
        )
        done = _recv_until(ws, "assistant_done")
    assert done[-1]["verified"] is True


def test_websocket_unknown_client_event_is_an_error(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"t": "not_a_real_event"})
        seen = _recv_until(ws, "error", code="bad_request")
    assert seen[-1]["recoverable"] is True


def test_websocket_set_model_unknown_profile_is_an_error(tmp_path) -> None:
    client, _, _ = _make(tmp_path)
    with client, client.websocket_connect("/ws?token=test-token", headers=_ORIGIN) as ws:
        ws.receive_json()
        ws.send_json({"t": "set_model", "profile": "does-not-exist"})
        seen = _recv_until(ws, "error", code="unknown_profile")
    assert seen[-1]["recoverable"] is True


@pytest.mark.skipif(not LIVE, reason="set AIZEN_LIVE_OLLAMA=1 with Ollama running + model pulled")
def test_live_ollama_health_and_turn(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["provider_ok"] is True
        response = client.post(
            "/v1/turn",
            json={"text": "hello"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        assert response.json()["result"]["answer"]
