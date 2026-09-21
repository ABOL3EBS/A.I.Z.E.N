"""Tool registry + the single execution wrapper.

One wrapper enforces: schema validation, the permission broker (§28), out-of-band
confirmation, timeout, cancellation, taint/origin normalization, and recording to
``tool_calls``/``audit_log``. Adding a tool must never change the agent loop.
"""

from __future__ import annotations

from typing import Any

import structlog

from aizen.domains import Domain
from aizen.session import Session, SessionEvent
from aizen.storage.records import ToolCallRecorder
from aizen.tools.base import (
    Tool,
    ToolContext,
    ToolResult,
    execution_capability,
    run_with_timeout,
)
from aizen.tools.broker import (
    ConfirmationManager,
    DecisionKind,
    PermissionBroker,
    ToolCallRequest,
    hash_args,
)
from aizen.tools.taint import origin_is_tainted

log = structlog.get_logger(__name__)


class ToolRegistry:
    def __init__(
        self,
        *,
        broker: PermissionBroker | None = None,
        recorder: ToolCallRecorder | None = None,
        confirmer: ConfirmationManager | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.broker = broker or PermissionBroker()
        self.recorder = recorder
        self.confirmer = confirmer or self.broker.confirmer

    def register(self, tool: Tool) -> Tool:
        if tool.spec.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.spec.name}")
        self._tools[tool.spec.name] = tool
        return tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"unknown tool {name!r}") from None

    def for_domain(self, domain: Domain) -> list[Tool]:
        from aizen.domains import DOMAIN_TOOLSETS

        names = DOMAIN_TOOLSETS.get(domain, ())
        return [self._tools[n] for n in names if n in self._tools]

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        ctx: ToolContext,
        *,
        session: Session | None = None,
    ) -> ToolResult:
        try:
            tool = self.get(name)
        except KeyError:
            return ToolResult.fail(f"unknown tool: {name}")
        spec = tool.spec

        try:
            args = spec.args_model.model_validate(arguments)
        except Exception as exc:  # pydantic ValidationError
            return ToolResult.fail(f"invalid arguments for {name}: {exc}")

        request = ToolCallRequest(
            turn_id=ctx.turn_id or "turn-local",
            call_id=ctx.call_id or f"{name}-call",
            tool=name,
            args=arguments,
            args_hash=hash_args(arguments),
            route=ctx.route,
            toolset=ctx.toolset,
            turn_tainted=ctx.tainted,
            taint_sources=list(ctx.taint_sources),
        )

        decision = self.broker.decide(request, spec)
        row_id = self._record_decision(request, spec, decision)

        if decision.kind is DecisionKind.DENY:
            self._finish_record(row_id, status="denied", summary=decision.reason)
            return ToolResult.fail(f"permission denied: {decision.reason}")

        if decision.kind is DecisionKind.CONFIRM:
            approved = await self._confirm(request, spec, args, session)
            if not approved:
                self._finish_record(row_id, status="declined", summary="user declined")
                return ToolResult.fail("permission denied: user declined")

        if session is not None:
            await session.apply(SessionEvent.TOOL_CALL, tool=spec.orb_state)
        if ctx.emit is not None:
            await ctx.emit("tool_start", {"name": name, "summary": spec.summarize_args(args)})
        self._mark_running(row_id)

        try:
            with execution_capability():
                result = await run_with_timeout(tool.run(args, ctx), spec.timeout_s)
        except BaseException as exc:  # noqa: BLE001 - surface anything to the model
            result = ToolResult.fail(f"{name} failed: {type(exc).__name__}: {exc}")

        result = self._normalize_origin(result, spec)
        if session is not None:
            await session.apply(SessionEvent.TOOL_RESULT)
        if ctx.emit is not None:
            await ctx.emit(
                "tool_end", {"name": name, "ok": result.ok, "tainted": result.is_tainted}
            )
        self._finish_record(
            row_id,
            status="ok" if result.ok else "error",
            summary=result.error or ", ".join(result.sources) or "ok",
        )
        return result

    async def _confirm(
        self,
        request: ToolCallRequest,
        spec: Any,
        args: Any,
        session: Session | None,
    ) -> bool:
        if session is not None:
            await session.apply(SessionEvent.CONFIRM_REQUIRED)
        confirm = self.broker.build_confirm_request(
            request, spec, summary=spec.summarize_args(args)
        )
        response = await self.confirmer.request(confirm)
        if response.approve:
            if session is not None:
                # CONFIRM_APPROVE lands on TOOL_CALL, which must carry the orb.
                await session.apply(SessionEvent.CONFIRM_APPROVE, tool=spec.orb_state)
            if response.scope == "session" and spec.permission.value in ("T2", "T3"):
                self.confirmer.grant(request.tool, request.args_hash, tier=spec.permission.value)
            return True
        if session is not None:
            await session.apply(SessionEvent.CONFIRM_DENY)
        return False

    def _normalize_origin(self, result: ToolResult, spec: Any) -> ToolResult:
        origin = result.origin
        if origin.value == "SYSTEM" and spec.result_origin.value != "SYSTEM":
            origin = spec.result_origin
        return result.model_copy(update={"origin": origin, "tainted": origin_is_tainted(origin)})

    def _record_decision(self, request: ToolCallRequest, spec: Any, decision: Any) -> int | None:
        if self.recorder is None:
            return None
        return self.recorder.record_decision(
            turn_id=request.turn_id,
            call_id=request.call_id,
            tool=request.tool,
            tier=spec.permission.value,
            args=request.args,
            args_hash=request.args_hash,
            decision=decision.kind.value,
            reason=decision.reason,
            tainted=request.turn_tainted,
            taint_sources=request.taint_sources,
        )

    def _mark_running(self, row_id: int | None) -> None:
        if self.recorder is not None and row_id is not None:
            self.recorder.mark_running(row_id)

    def _finish_record(self, row_id: int | None, *, status: str, summary: str) -> None:
        if self.recorder is not None and row_id is not None:
            self.recorder.finish(row_id, status=status, result_summary=summary)

    @classmethod
    def load_defaults(cls, **kwargs: Any) -> ToolRegistry:
        # Imported here to avoid a circular import at module load time.
        from aizen.tools.builtin import BUILTIN_TOOLS

        registry = cls(**kwargs)
        for tool in BUILTIN_TOOLS:
            registry.register(tool)
        return registry
