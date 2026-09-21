"""FastAPI core service: REST + WebSocket, bound to 127.0.0.1, token auth.

Implements the UI <-> core contract in architecture Part II §6.2 with §27 session
state, §28 confirmation and §34 failure behavior. The agent loop and DB are
injected so tests can substitute fakes.

Guarantees enforced here (not in the model):
- every REST/WS request needs the per-launch bearer token; ``Origin`` is checked;
- ``/health`` is the only unauthenticated endpoint (readiness probe);
- one active turn per session (a second ``user_text`` returns a typed busy error);
- ``assistant_delta`` for tool routes is only emitted after verification passes
  (the loop buffers it; see ``aizen.agent.loop``);
- a disconnected/silent client's turn is canceled after ``client_timeout_s``.
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any, Literal, cast

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, TypeAdapter, ValidationError

from aizen import __version__
from aizen.agent import AgentEvent, AgentLoop
from aizen.api.errors import busy_error, error_event
from aizen.api.protocol import (
    AssistantDeltaEvent,
    AssistantDoneEvent,
    ClientEvent,
    ConfirmRequestEvent,
    ErrorEvent,
    ModelStatusEvent,
    OrbStateEvent,
    Ping,
    RouteEvent,
    SessionStateEvent,
    ToolEndEvent,
    ToolStartEvent,
    dump_server_event,
)
from aizen.config import ProfileSet, Settings
from aizen.domains import Domain
from aizen.llm import LLMProvider, OllamaProvider
from aizen.llm.base import Usage
from aizen.observability.logging import setup_logging
from aizen.orb import OrbState
from aizen.session import SessionEvent
from aizen.storage import ConversationStore, Database

log = structlog.get_logger(__name__)

_BEARER = "Bearer "
_CLIENT_ADAPTER = TypeAdapter(ClientEvent)

_RISK_BY_TIER = {"T4": "write", "T5": "exec"}


class TurnBody(BaseModel):
    text: str
    conversation_id: int | None = None
    private: bool = False


def build_default_loop(settings: Settings) -> AgentLoop:
    profiles = ProfileSet.load()
    profile = profiles.get(settings.default_profile)
    provider = OllamaProvider(settings.ollama_base_url, model=profile.model)
    return AgentLoop(provider, settings=settings)


def _origin_ok(origin: str | None, allowed: set[str]) -> bool:
    # Browsers always send Origin cross-origin; local CLI clients may omit it.
    return origin is None or origin in allowed


def _current_model(settings: Settings) -> str | None:
    try:
        return ProfileSet.load().get(settings.default_profile).model
    except Exception:  # noqa: BLE001 - profiles are config; never crash on error mapping
        return None


def _confirm_risk(tier: str) -> Literal["write", "exec", "network"]:
    return cast(Literal["write", "exec", "network"], _RISK_BY_TIER.get(tier, "network"))


def agent_event_to_wire(event: AgentEvent, turn_id: str) -> list[Any]:
    """Translate a loop event into one or more wire (ServerEvent) models."""
    payload: dict[str, Any] = event.payload or {}
    kind = event.type
    if kind == "session_state":
        return [
            SessionStateEvent(
                state=str(payload.get("state", "IDLE")),
                seq=int(payload.get("seq", 0)),
                detail=payload.get("detail"),
            )
        ]
    if kind == "orb_state":
        return [
            OrbStateEvent(
                state=OrbState(str(payload.get("state", "IDLE"))),
                detail=payload.get("detail"),
            )
        ]
    if kind == "route":
        return [
            RouteEvent(
                domain=Domain(str(payload.get("domain", "chat"))),
                toolset=list(payload.get("toolset", [])),
                hint=payload.get("hint"),
            )
        ]
    if kind == "assistant_delta":
        return [AssistantDeltaEvent(turn_id=turn_id, text=event.detail or "")]
    if kind == "assistant_done":
        return [
            AssistantDoneEvent(
                turn_id=turn_id,
                usage=Usage(**payload.get("usage", {})),
                verified=bool(payload.get("verified", False)),
                domain=Domain(str(payload.get("domain", "chat"))),
                tainted=bool(payload.get("tainted", False)),
            )
        ]
    if kind == "tool_start":
        return [
            ToolStartEvent(
                name=str(payload.get("name", "")),
                args_summary=str(payload.get("summary", "")),
            )
        ]
    if kind == "tool_end":
        return [
            ToolEndEvent(
                name=str(payload.get("name", "")),
                ok=bool(payload.get("ok", True)),
                tainted=bool(payload.get("tainted", False)),
            )
        ]
    if kind == "confirm_request":
        tier = str(payload.get("tier", "T3"))
        return [
            ConfirmRequestEvent(
                request_id=str(payload["request_id"]),
                turn_id=str(payload["turn_id"]),
                call_id=str(payload["call_id"]),
                tool=str(payload["tool"]),
                tier=tier,
                risk=_confirm_risk(tier),
                summary=str(payload["summary"]),
                preview=payload.get("preview"),
                args_hash=str(payload["args_hash"]),
                tainted=bool(payload.get("tainted", False)),
                taint_sources=list(payload.get("taint_sources", [])),
                expires_at=str(payload["expires_at"]),
            )
        ]
    if kind == "model_status":
        return [
            ModelStatusEvent(
                profile=str(payload.get("profile", "")),
                loaded=bool(payload.get("loaded", False)),
            )
        ]
    if kind == "error":
        return [
            ErrorEvent(
                code="agent_notice",
                message=event.detail or "error",
                recoverable=True,
            )
        ]
    return []


def create_app(
    settings: Settings | None = None,
    *,
    loop: AgentLoop | None = None,
    database: Database | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if not loop:
        setup_logging(settings.log_level)
        loop = build_default_loop(settings)
    token = settings.api_token or secrets.token_urlsafe(24)
    allowed_origins = set(settings.api_allowed_origins)
    app = FastAPI(title="A.I.Z.E.N. core", version=__version__)

    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        # Created (and connected) inside the event-loop thread: sqlite3
        # connections must not cross threads.
        db = database if database is not None else Database(settings.db_path)
        app_.state.db = db
        app_.state.loop = loop
        app_.state.token = token
        app_.state.conversations = ConversationStore(db)
        loop.on_event = None
        yield
        db.close()
        close = getattr(loop.llm, "aclose", None)
        if close is not None:
            await close()

    app.router.lifespan_context = lifespan

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        if authorization != f"{_BEARER}{token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_origin(origin: Annotated[str | None, Header()] = None) -> None:
        if not _origin_ok(origin, allowed_origins):
            raise HTTPException(status_code=403, detail="origin not allowed")

    @app.get("/health")
    async def health() -> dict[str, object]:
        provider: LLMProvider = loop.llm
        status = await provider.health()
        return {
            "status": "ok",
            "version": __version__,
            "profile": settings.default_profile,
            "provider_ok": status.ok,
            "provider_ms": round(status.ms, 1),
            "provider_version": status.version,
        }

    @app.get("/v1/models")
    async def models(_: None = Depends(require_token)) -> list[dict[str, object]]:
        infos = await loop.llm.list_models()
        return [info.model_dump() for info in infos]

    @app.post(
        "/v1/turn",
        dependencies=[Depends(require_token), Depends(require_origin)],
        response_model=None,
    )
    async def turn(body: TurnBody) -> JSONResponse:
        store: ConversationStore = app.state.conversations
        conversation_id = store.ensure(body.conversation_id, private=body.private)
        history = store.history(conversation_id)
        if not body.private:
            store.add_message(conversation_id, "user", body.text, origin="USER")
        try:
            result = await loop.run_turn(
                body.text, conversation=history, conversation_id=conversation_id
            )
        except Exception as exc:  # noqa: BLE001 - mapped to the §34 error taxonomy
            event = error_event(exc, model=_current_model(settings))
            status = 502 if event.recoverable else 400
            return JSONResponse(status_code=status, content=dump_server_event(event))
        if not body.private:
            store.add_message(
                conversation_id,
                "assistant",
                result.answer,
                origin="SYSTEM",
                tainted=result.taint_level == "TAINTED",
            )
        return JSONResponse(
            content={
                "conversation_id": conversation_id,
                "result": result.model_dump(mode="json"),
            }
        )

    @app.websocket("/ws")
    async def ws(
        websocket: WebSocket,
        token_q: Annotated[str | None, Query(alias="token")] = None,
    ) -> None:
        header_auth = websocket.headers.get("authorization")
        if token_q != token and header_auth != f"{_BEARER}{token}":
            await websocket.close(code=4401, reason="unauthorized")
            return
        if not _origin_ok(websocket.headers.get("origin"), allowed_origins):
            await websocket.close(code=4403, reason="origin not allowed")
            return
        await websocket.accept()

        store: ConversationStore = app.state.conversations
        ctx: dict[str, Any] = {
            "conversation_id": None,
            "private": False,
            "turn_id": "",
            "turn_task": None,
            "last_frame": time.monotonic(),
        }

        async def send(model: BaseModel) -> None:
            with suppress(Exception):  # noqa: BLE001 - client may have gone away mid-turn
                await websocket.send_json(dump_server_event(model))

        async def forward(event: AgentEvent) -> None:
            for wire in agent_event_to_wire(event, str(ctx["turn_id"])):
                await send(wire)

        async def run_turn(text: str) -> None:
            conversation_id = store.ensure(ctx["conversation_id"], private=bool(ctx["private"]))
            ctx["conversation_id"] = conversation_id
            history = store.history(conversation_id)
            if not ctx["private"]:
                store.add_message(conversation_id, "user", text, origin="USER")
            try:
                result = await loop.run_turn(
                    text,
                    conversation=history,
                    conversation_id=conversation_id,
                    turn_id=str(ctx["turn_id"]),
                )
            except Exception as exc:  # noqa: BLE001 - mapped to the §34 taxonomy
                await send(error_event(exc, model=_current_model(settings)))
                return
            if not ctx["private"]:
                store.add_message(
                    conversation_id,
                    "assistant",
                    result.answer,
                    origin="SYSTEM",
                    tainted=result.taint_level == "TAINTED",
                )

        async def set_model(profile_name: str) -> None:
            try:
                profile = ProfileSet.load().get(profile_name)
            except KeyError as exc:
                await send(
                    ErrorEvent(
                        code="unknown_profile",
                        message=f"Unknown model profile {profile_name!r}.",
                        recoverable=True,
                        hint=str(exc),
                    )
                )
                return
            # §32 gate enforcement (a passing report for the exact digest) lands
            # with the model-gate work; for now only existence is validated.
            settings.default_profile = profile_name
            if getattr(loop.llm, "model", None) is not None:
                loop.llm.model = profile.model  # type: ignore[attr-defined]
            await send(ModelStatusEvent(profile=profile_name, loaded=False))

        async def dispatch(event: Any) -> None:
            kind = event.t
            if kind == "user_text":
                if loop.is_busy:
                    await send(busy_error())
                    return
                ctx["turn_id"] = event.turn_id or uuid.uuid4().hex
                ctx["turn_task"] = asyncio.create_task(run_turn(event.text))
            elif kind == "cancel_turn":
                await loop.cancel_active()
            elif kind == "interrupt_speech":
                await loop.session.apply(SessionEvent.INTERRUPT_SPEECH)
            elif kind == "confirm_response":
                loop.confirmer.respond(event.request_id, event.approve, scope=event.scope)
            elif kind == "set_model":
                await set_model(event.profile)
            elif kind == "private_mode":
                ctx["private"] = event.on
                if ctx["conversation_id"] is not None:
                    store.set_private(int(ctx["conversation_id"]), event.on)
            # ptt_start / ptt_stop / pong: accepted; audio arrives in the voice phase.

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(settings.heartbeat_interval_s)
                await send(Ping())
                silent_for = time.monotonic() - float(ctx["last_frame"])
                if loop.is_busy and silent_for > settings.client_timeout_s:
                    await loop.cancel_active()

        loop.on_event = forward
        await send(SessionStateEvent(state=loop.session.state.value, seq=loop.session.seq))
        beat = asyncio.create_task(heartbeat())
        try:
            while True:
                raw = await websocket.receive_json()
                ctx["last_frame"] = time.monotonic()
                try:
                    event = _CLIENT_ADAPTER.validate_python(raw)
                except ValidationError:
                    await send(
                        ErrorEvent(
                            code="bad_request",
                            message="Unrecognized client event.",
                            recoverable=True,
                        )
                    )
                    continue
                await dispatch(event)
        except WebSocketDisconnect:
            pass
        finally:
            beat.cancel()
            loop.on_event = None
            task: asyncio.Task[Any] | None = ctx["turn_task"]
            if task is not None and not task.done():
                # §34: a client that never comes back gets its turn canceled.
                asyncio.create_task(_cancel_after(loop, settings.client_timeout_s, task))

    return app


async def _cancel_after(loop: AgentLoop, delay: float, task: asyncio.Task[Any]) -> None:
    await asyncio.sleep(delay)
    if not task.done():
        await loop.cancel_active()
