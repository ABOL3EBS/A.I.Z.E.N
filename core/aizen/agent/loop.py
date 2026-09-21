"""Custom thin agent loop: route -> narrow toolset -> model -> tool -> verify.

Part II §26/§27/§28/§29 are normative here: the loop owns the session state
machine, tracks per-turn taint, wraps untrusted content, enforces the permission
broker through the tool wrapper, records turns/tool_calls, and guarantees
cancellation reaches IDLE quickly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Literal

import structlog
from pydantic import BaseModel

from aizen.agent.context import SYSTEM_PROMPT, ContextBuilder
from aizen.agent.router import RuleRouter
from aizen.cancellation import CancellationToken, token_or_new
from aizen.config import RouteDecision, Settings
from aizen.domains import Domain
from aizen.errors import CancelledError as AizenCancelledError
from aizen.facts import Fact, FactTable
from aizen.llm.base import (
    GenOptions,
    LLMEventKind,
    LLMProvider,
    Message,
    MessageRole,
    ToolCall,
    Usage,
)
from aizen.session import Session, SessionEvent, SessionState, orb_for
from aizen.storage.db import Database
from aizen.storage.records import ToolCallRecorder, TurnRecorder, TurnTraceRecorder
from aizen.tools.base import OrbState, ToolContext, ToolResult
from aizen.tools.broker import ConfirmationManager
from aizen.tools.registry import ToolRegistry
from aizen.tools.taint import TurnTaint, new_nonce, taint_from_messages, wrap_untrusted
from aizen.verifier import (
    VerifyResult,
    allowed_numbers_text,
    render_narration,
    verify_answer,
)

log = structlog.get_logger(__name__)

# Routes whose answers must be backed by tool facts (§26.11/§31).
VERIFY_DOMAINS: frozenset[Domain] = frozenset({Domain.FINANCE, Domain.PERSONAL, Domain.WEB})

NO_DATA_ANSWER = "I don't have tool-derived data to answer that."


class AgentEvent(BaseModel):
    type: Literal[
        "route",
        "orb_state",
        "session_state",
        "assistant_delta",
        "assistant_done",
        "tool_start",
        "tool_end",
        "confirm_request",
        "model_status",
        "error",
    ]
    detail: str | None = None
    payload: dict[str, Any] = {}


class TurnResult(BaseModel):
    answer: str
    domain: Domain
    toolset: tuple[str, ...] = ()
    model_calls: int = 0
    used_tools: list[str] = []
    truncated: bool = False
    canceled: bool = False
    verified: bool = False
    fell_back: bool = False
    context_tokens: dict[str, int] = {}
    usage: Usage = Usage()
    turn_id: str = ""
    taint_level: str = "CLEAN"
    taint_sources: list[str] = []
    final_state: str = SessionState.IDLE.value


class AgentLoop:
    def __init__(
        self,
        llm: LLMProvider,
        *,
        router: RuleRouter | None = None,
        registry: ToolRegistry | None = None,
        settings: Settings | None = None,
        system_prompt: str | None = None,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
        db: Database | None = None,
    ) -> None:
        self.llm = llm
        self.router = router or RuleRouter.load()
        self.settings = settings or Settings()
        self.system_prompt = system_prompt
        self.on_event = on_event
        self.session = Session(on_state=self._emit_session_state)

        self.confirmer = ConfirmationManager(
            timeout_s=self.settings.confirm_timeout_s,
            on_request=self._emit_confirm_request,
        )
        self.registry = registry or ToolRegistry.load_defaults()
        self.registry.confirmer = self.confirmer
        self.registry.broker.confirmer = self.confirmer
        if db is not None:
            self.turn_recorder: TurnRecorder | None = TurnRecorder(db)
            self.trace_recorder: TurnTraceRecorder | None = TurnTraceRecorder(db)
            self.registry.recorder = ToolCallRecorder(db)
        else:
            self.turn_recorder = None
            self.trace_recorder = None

        self._active_task: asyncio.Task[Any] | None = None
        self._active_token: CancellationToken | None = None

    # --- event plumbing ---
    async def _emit(self, event: AgentEvent) -> None:
        if self.on_event is not None:
            await self.on_event(event)

    async def _emit_tool(self, type_: str, payload: dict[str, Any]) -> None:
        await self._emit(AgentEvent(type=type_, payload=payload))  # type: ignore[arg-type]

    async def _emit_session_state(self, payload: dict[str, object]) -> None:
        await self._emit(AgentEvent(type="session_state", payload=payload))
        state = SessionState(str(payload["state"]))
        orb = (
            OrbState(str(payload["detail"])) if state is SessionState.TOOL_CALL else orb_for(state)
        )
        await self._emit(AgentEvent(type="orb_state", payload={"state": orb.value}))

    async def _emit_confirm_request(self, confirm: Any) -> None:
        await self._emit(AgentEvent(type="confirm_request", payload=confirm.model_dump()))

    # --- cancellation (§27 guarantees) ---
    @property
    def is_busy(self) -> bool:
        """True while a turn is in flight; one turn per session (§26)."""
        task = self._active_task
        return task is not None and not task.done()

    async def cancel_active(self) -> None:
        if self._active_token is not None:
            self._active_token.request_cancel()
        self.confirmer.reject_all("canceled")
        task = self._active_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def _warming_watchdog(self) -> None:
        """§34: if the first token is slow, emit subtle 'warming up' status."""
        await asyncio.sleep(self.settings.ttft_timeout_s)
        await self._emit(
            AgentEvent(
                type="model_status",
                payload={
                    "profile": self.settings.default_profile,
                    "loaded": False,
                    "warming": True,
                },
            )
        )

    async def run_turn(
        self,
        text: str,
        *,
        cancel: CancellationToken | None = None,
        conversation: list[Message] | None = None,
        conversation_id: int | None = None,
        turn_id: str | None = None,
    ) -> TurnResult:
        token = token_or_new(cancel)
        self._active_token = token
        self._active_task = asyncio.current_task()

        turn_id = turn_id or uuid.uuid4().hex
        nonce = new_nonce()
        taint = TurnTaint()
        # §29: taint is computed from context contents, so tainted messages (or a
        # summary derived from them) still in the window keep later turns tainted.
        taint.absorb(taint_from_messages(list(conversation or [])))
        if self.session.state is SessionState.IDLE:
            await self.session.apply(SessionEvent.USER_TEXT)
        if self.turn_recorder is not None:
            self.turn_recorder.start(
                turn_id,
                conversation_id=conversation_id,
                model_profile=self.settings.default_profile,
            )

        decision = self.router.route(text)
        await self._emit_route(decision)
        if self.trace_recorder is not None:
            self.trace_recorder.record_route(
                turn_id,
                domain=decision.domain.value,
                rule_hits=decision.rule_hits,
            )

        history = list(conversation or [])
        used_tools: list[str] = []
        seen_calls: set[tuple[str, str]] = set()
        facts = FactTable()
        invocations: list[tuple[str, dict[str, Any], str | None]] = []
        spans: list[dict[str, Any]] = [{"name": "route", "domain": decision.domain.value}]
        budget = max(1, self.settings.max_model_calls_per_turn)
        toolset = self.registry.for_domain(decision.domain)
        toolset_names = tuple(t.spec.name for t in toolset)
        step = 0
        regenerated = False
        usage = Usage()
        # §26.5/§26.12: only the tool-free `chat` route may stream tokens before
        # verification; finance/personal/web answers are buffered and emitted
        # after the verifier passes so no unverified text reaches the client.
        stream_live = decision.domain not in VERIFY_DOMAINS

        try:
            for step in range(1, budget + 1):
                builder = ContextBuilder(
                    system_prompt=self.system_prompt or SYSTEM_PROMPT,
                    max_tokens=self.settings.context_budget_tokens,
                )
                messages, breakdown = builder.build(user_text=text, conversation=history)
                tool_schemas = [t.spec.to_schema() for t in toolset] or None
                options = GenOptions(num_ctx=self.settings.context_budget_tokens)
                parts: list[str] = []
                calls: list[ToolCall] = []

                try:
                    watchdog = asyncio.create_task(self._warming_watchdog())
                    saw_any = False
                    try:
                        async for ev in self.llm.chat_stream(
                            messages,
                            tools=tool_schemas,
                            options=options,
                            cancel=token,
                        ):
                            if not saw_any:
                                saw_any = True
                                watchdog.cancel()
                            if ev.usage is not None:
                                usage = ev.usage
                            if ev.kind == LLMEventKind.TOKEN and ev.text:
                                parts.append(ev.text)
                                if stream_live:
                                    await self._emit(
                                        AgentEvent(type="assistant_delta", detail=ev.text)
                                    )
                            elif ev.kind == LLMEventKind.TOOL_CALL and ev.tool_call is not None:
                                calls.append(ev.tool_call)
                            elif ev.kind == LLMEventKind.ERROR:
                                await self._emit(AgentEvent(type="error", detail=ev.error))
                    finally:
                        watchdog.cancel()
                        with suppress(asyncio.CancelledError):
                            await watchdog
                except (asyncio.CancelledError, AizenCancelledError):
                    return await self._finish_canceled(
                        turn_id, taint, decision, step, used_tools, usage=usage
                    )

                if token.is_cancelled():
                    return await self._finish_canceled(
                        turn_id, taint, decision, step, used_tools, usage=usage
                    )

                spans.append({"name": "model", "step": step, "tool_calls": len(calls)})
                tokens = dict(getattr(breakdown, "sections", {}) or {})

                if not calls:
                    draft = "".join(parts).strip()
                    if decision.domain not in VERIFY_DOMAINS:
                        return await self._finish_answer(
                            turn_id,
                            taint,
                            decision,
                            step,
                            used_tools,
                            draft,
                            breakdown,
                            verified=True,
                            spans=spans,
                            tokens=tokens,
                            conversation_id=conversation_id,
                            streamed=stream_live,
                            usage=usage,
                        )
                    verdict = verify_answer(draft, facts.facts, user_text=text)
                    if not verdict.ok and not regenerated:
                        regenerated = True
                        spans.append({"name": "verify", "ok": False, "retry": "regenerate"})
                        history.append(
                            Message(
                                role=MessageRole.SYSTEM,
                                content=_constraint_prompt(facts.facts),
                            )
                        )
                        continue
                    if not verdict.ok:
                        answer = _fallback_narration(invocations, facts)
                        fell_back = True
                        if answer is None:
                            answer = NO_DATA_ANSWER
                        spans.append({"name": "verify", "ok": False, "retry": "template"})
                        return await self._finish_answer(
                            turn_id,
                            taint,
                            decision,
                            step,
                            used_tools,
                            answer,
                            breakdown,
                            verified=False,
                            fell_back=fell_back,
                            verify=verdict,
                            spans=spans,
                            tokens=tokens,
                            conversation_id=conversation_id,
                            streamed=stream_live,
                            usage=usage,
                        )
                    spans.append({"name": "verify", "ok": True})
                    return await self._finish_answer(
                        turn_id,
                        taint,
                        decision,
                        step,
                        used_tools,
                        draft,
                        breakdown,
                        verified=True,
                        verify=verdict,
                        spans=spans,
                        tokens=tokens,
                        conversation_id=conversation_id,
                        streamed=stream_live,
                        usage=usage,
                    )

                history.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content="".join(parts).strip() or None,
                        tool_calls=calls,
                    )
                )
                repeated = False
                for call in calls:
                    key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
                    if key in seen_calls:
                        repeated = True
                        break
                    seen_calls.add(key)

                    used_tools.append(call.name)
                    ctx = ToolContext(
                        cancel=token,
                        emit=self._emit_tool,
                        turn_id=turn_id,
                        call_id=call.id,
                        route=decision.domain,
                        toolset=toolset_names,
                        tainted=taint.is_tainted,
                        taint_sources=list(taint.sources),
                    )
                    result = await self.registry.execute(
                        call.name, call.arguments, ctx, session=self.session
                    )
                    registered = facts.register_all(
                        call.name, result.facts, tainted=result.is_tainted
                    )
                    result = result.model_copy(update={"facts": registered})
                    if result.ok:
                        invocations.append(
                            (call.name, dict(call.arguments), result.narration_template)
                        )
                    spans.append(
                        {
                            "name": "tool",
                            "tool": call.name,
                            "ok": result.ok,
                            "tainted": result.is_tainted,
                        }
                    )
                    source = result.sources[0] if result.sources else result.origin.value
                    taint.mark(result.origin, source)
                    history.append(
                        Message(
                            role=MessageRole.TOOL,
                            content=_tool_result_content(result, nonce),
                            tool_call_id=call.id,
                            origin=result.origin.value,
                            tainted=result.is_tainted,
                        )
                    )
                    if token.is_cancelled():
                        return await self._finish_canceled(
                            turn_id, taint, decision, step, used_tools, usage=usage
                        )
                if repeated:
                    await self._emit(
                        AgentEvent(type="error", detail="repeated identical tool call")
                    )
                    return await self._finish_answer(
                        turn_id,
                        taint,
                        decision,
                        step,
                        used_tools,
                        "".join(parts).strip() or "I already have that result.",
                        breakdown,
                        verified=True,
                        spans=spans,
                        tokens=tokens,
                        conversation_id=conversation_id,
                        streamed=stream_live,
                        usage=usage,
                    )

            await self._emit(
                AgentEvent(type="error", detail="turn truncated: model-call budget exceeded")
            )
            return await self._finish_answer(
                turn_id,
                taint,
                decision,
                budget,
                used_tools,
                "I hit my work limit for this request — could you narrow it down?",
                {},
                truncated=True,
                verified=False,
                spans=spans,
                conversation_id=conversation_id,
                streamed=stream_live,
                usage=usage,
            )
        except (asyncio.CancelledError, AizenCancelledError):
            return await self._finish_canceled(
                turn_id, taint, decision, step, used_tools, usage=usage
            )
        except Exception:
            # §34: typed failures move the session to ERROR (recoverable by the
            # next user_text) and are re-raised for the API layer to map.
            await self.session.apply(SessionEvent.ERROR)
            if self.turn_recorder is not None:
                self.turn_recorder.finish(
                    turn_id,
                    verified=None,
                    final_state=self.session.state.value,
                    taint_level=taint.level,
                )
            raise
        finally:
            self._active_task = None
            self._active_token = None

    async def _finish_answer(
        self,
        turn_id: str,
        taint: TurnTaint,
        decision: RouteDecision,
        model_calls: int,
        used_tools: list[str],
        answer: str,
        breakdown: Any,
        *,
        truncated: bool = False,
        verified: bool = True,
        fell_back: bool = False,
        verify: VerifyResult | None = None,
        spans: list[dict[str, Any]] | None = None,
        tokens: dict[str, int] | None = None,
        conversation_id: int | None = None,
        streamed: bool = False,
        usage: Usage | None = None,
    ) -> TurnResult:
        await self.session.apply(SessionEvent.ANSWER_VERIFIED)
        # §26.12: buffered (tool-route) answers are only released once verified.
        if not streamed and answer:
            await self._emit(AgentEvent(type="assistant_delta", detail=answer))
        await self._emit(
            AgentEvent(
                type="assistant_done",
                payload={
                    "domain": decision.domain.value,
                    "verified": verified,
                    "fell_back": fell_back,
                    "tainted": taint.is_tainted,
                    "usage": (usage or Usage()).model_dump(),
                },
            )
        )
        await self.session.apply(SessionEvent.PLAYBACK_DRAINED)
        if self.trace_recorder is not None:
            self.trace_recorder.record_trace(
                turn_id,
                conversation_id=conversation_id,
                spans=spans or [],
                tokens=tokens or {},
                tainted=taint.is_tainted,
                verified=verified,
                verify=verify,
                redacted=not self.settings.debug_content,
            )
        if self.turn_recorder is not None:
            self.turn_recorder.finish(
                turn_id,
                verified=verified,
                final_state=self.session.state.value,
                taint_level=taint.level,
            )
        return TurnResult(
            answer=answer,
            domain=decision.domain,
            toolset=decision.toolset,
            model_calls=model_calls,
            used_tools=used_tools,
            truncated=truncated,
            verified=verified,
            fell_back=fell_back,
            context_tokens=getattr(breakdown, "sections", {}) or {},
            usage=usage or Usage(),
            turn_id=turn_id,
            taint_level=taint.level,
            taint_sources=list(taint.sources),
            final_state=self.session.state.value,
        )

    async def _finish_canceled(
        self,
        turn_id: str,
        taint: TurnTaint,
        decision: RouteDecision,
        model_calls: int,
        used_tools: list[str],
        *,
        usage: Usage | None = None,
    ) -> TurnResult:
        await self.session.apply(SessionEvent.CANCEL_TURN)
        if self.turn_recorder is not None:
            self.turn_recorder.finish(
                turn_id,
                verified=None,
                final_state=self.session.state.value,
                taint_level=taint.level,
            )
        return TurnResult(
            answer="(canceled)",
            domain=decision.domain,
            toolset=decision.toolset,
            model_calls=model_calls,
            used_tools=used_tools,
            canceled=True,
            usage=usage or Usage(),
            turn_id=turn_id,
            taint_level=taint.level,
            taint_sources=list(taint.sources),
            final_state=self.session.state.value,
        )

    async def _emit_route(self, decision: RouteDecision) -> None:
        await self._emit(
            AgentEvent(
                type="route",
                payload={
                    "domain": decision.domain.value,
                    "toolset": list(decision.toolset),
                    "rules_hit": decision.rule_hits,
                    "tied": decision.tied,
                },
            )
        )


def _tool_result_content(result: ToolResult, nonce: str) -> str:
    if not result.ok:
        return f"tool error: {result.error or 'unknown error'}"
    payload = json.dumps(
        {
            "facts": [
                {
                    "id": fact.fact_id,
                    "path": fact.path,
                    "kind": fact.kind.value,
                    "value": fact.display_text(),
                    **({"currency": fact.currency} if fact.currency else {}),
                }
                for fact in result.facts
            ],
            "sources": list(result.sources),
        },
        default=str,
    )
    if result.is_tainted:
        return wrap_untrusted(payload, result.origin, nonce)
    return payload


def _constraint_prompt(facts: list[Fact]) -> str:
    if facts:
        return (
            "Your draft contained numbers that no tool returned. Rewrite the answer "
            "using only the tool-provided values below, exactly as written. Do not "
            "compute, estimate or infer any new number.\n\n" + allowed_numbers_text(facts)
        )
    return (
        "You must answer this using a tool; do not state any number from your own "
        "knowledge. Call an available tool first, then answer from its result."
    )


def _fallback_narration(
    invocations: list[tuple[str, dict[str, Any], str | None]], facts: FactTable
) -> str | None:
    """Deterministic fallback: render the last tool's narration template."""
    for _name, args, template in reversed(invocations):
        if template:
            return render_narration(template, args, facts.facts)
    return None
