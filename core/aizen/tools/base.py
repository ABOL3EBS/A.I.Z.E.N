"""Tool contracts: ToolSpec, ToolResult, Tool Protocol, permission tiers.

One execution wrapper in the registry enforces schema validation, timeouts,
permissions, audit and events; the agent loop never changes when a tool is
added (architecture section 8).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from aizen.cancellation import CancellationToken
from aizen.domains import Domain
from aizen.facts import Fact
from aizen.llm.base import ToolSchema
from aizen.orb import OrbState
from aizen.tools.taint import Origin, origin_is_tainted


class PermissionTier(StrEnum):
    T0 = "T0"  # pure side-effect-free (time, calculate)      -> auto-allow
    T1 = "T1"  # read-scoped (knowledge.search, files.read)   -> auto-allow, logged
    T2 = "T2"  # read-sensitive (finance.*)                   -> allowed, logged w/o values
    T3 = "T3"  # network (web.search, web.fetch)              -> allowed + egress log
    T4 = "T4"  # write (files.create/modify, memory.forget)   -> confirm every time
    T5 = "T5"  # execute (shell, scripts)                     -> confirm every time, sandboxed
    T6 = "T6"  # never exposed


class ConfirmPolicy(StrEnum):
    NEVER = "never"
    FIRST_USE = "first_use"
    ALWAYS = "always"


def default_summary(args: BaseModel) -> str:
    return json.dumps(args.model_dump(), default=str)


class ToolSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=False)

    name: str
    domain: Domain = Domain.SYSTEM
    description: str
    args_model: type[BaseModel]
    permission: PermissionTier = PermissionTier.T1
    timeout_s: float = Field(default=10.0, gt=0)
    orb_state: OrbState = OrbState.SEARCHING
    produces_untrusted: bool = False
    confirm: ConfirmPolicy = ConfirmPolicy.NEVER
    summarize_args: Callable[[BaseModel], str] = default_summary
    # Path/URL arguments checked by the broker (§28.2) before ANY execution.
    path_args: frozenset[str] = frozenset()
    url_args: frozenset[str] = frozenset()
    # Origin of the tool's results (assigned by code, never by the model).
    result_origin: Origin = Origin.SYSTEM

    def to_schema(self) -> ToolSchema:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return ToolSchema(name=self.name, description=self.description, parameters=schema)


class ToolResult(BaseModel):
    ok: bool = True
    facts: list[Fact] = []
    sources: list[str] = []
    provenance: list[str] = []
    truncated: bool = False
    tainted: bool = False
    origin: Origin = Origin.SYSTEM
    narration_template: str | None = None
    error: str | None = None

    @classmethod
    def fail(cls, error: str, *, origin: Origin = Origin.SYSTEM) -> ToolResult:
        return cls(ok=False, error=error, origin=origin, tainted=origin_is_tainted(origin))

    @property
    def is_tainted(self) -> bool:
        return self.tainted or origin_is_tainted(self.origin)


class ToolContext(BaseModel):
    cancel: CancellationToken | None = None
    emit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None

    # --- per-turn execution bag (set by the agent loop) ---
    turn_id: str = ""
    call_id: str = ""
    route: Domain = Domain.SYSTEM
    toolset: tuple[str, ...] = ()
    tainted: bool = False
    taint_sources: list[str] = []

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ToolExecutionNotPermitted(RuntimeError):
    """Raised when a tool body runs without the registry's execution capability."""


# The registry is the only door (§28). It holds this per-task capability open
# only while it awaits the wrapper, so a direct ``tool.run(...)`` from anywhere
# else fails at runtime instead of silently bypassing the permission broker.
_EXECUTION_CAPABILITY: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "aizen_tool_execution_capability", default=False
)


@contextmanager
def execution_capability() -> Iterator[None]:
    token = _EXECUTION_CAPABILITY.set(True)
    try:
        yield
    finally:
        _EXECUTION_CAPABILITY.reset(token)


def require_execution_capability() -> None:
    if not _EXECUTION_CAPABILITY.get():
        raise ToolExecutionNotPermitted(
            "tool.run() may only be invoked by the registry wrapper, which enforces "
            "the permission broker; call ToolRegistry.execute() instead"
        )


@runtime_checkable
class Tool(Protocol):
    spec: ToolSpec

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult: ...


class FunctionTool:
    """Wraps `async def fn(args_model, ctx) -> ToolResult` into a Tool."""

    def __init__(
        self, impl: Callable[[Any, ToolContext], Awaitable[ToolResult]], spec: ToolSpec
    ) -> None:
        self._impl = impl
        self.spec = spec

    async def run(self, args: BaseModel, ctx: ToolContext) -> ToolResult:
        require_execution_capability()
        return await self._impl(args, ctx)


async def run_with_timeout(coro: Awaitable[ToolResult], timeout_s: float) -> ToolResult:
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except TimeoutError:
        return ToolResult.fail(f"tool timed out after {timeout_s}s")
