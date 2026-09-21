"""The registry wrapper is the only door: no path may bypass the broker (§28)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from aizen.domains import Domain
from aizen.tools import (
    FunctionTool,
    PermissionTier,
    ToolContext,
    ToolExecutionNotPermitted,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from aizen.tools.broker import PermissionBroker
from aizen.tools.scope import PathScopeGuard
from pydantic import BaseModel

AIZEN_ROOT = Path(__file__).resolve().parents[2] / "aizen"


def test_tool_run_is_called_only_from_the_registry() -> None:
    """A structural guard: `tool.run(...)` appears nowhere but the wrapper."""
    call_pattern = re.compile(r"\.run\(\s*(?:args|arguments)\b")
    offenders = [
        str(path.relative_to(AIZEN_ROOT))
        for path in AIZEN_ROOT.rglob("*.py")
        if path.name != "registry.py" and call_pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


class _Args(BaseModel):
    path: str


async def test_broker_deny_prevents_execution() -> None:
    executed: list[str] = []

    async def _impl(args: _Args, ctx: ToolContext) -> ToolResult:
        executed.append(args.path)
        return ToolResult(facts=[])

    tool = FunctionTool(
        _impl,
        ToolSpec(
            name="files.read",
            domain=Domain.PERSONAL,
            description="read",
            args_model=_Args,
            permission=PermissionTier.T1,
            path_args=frozenset({"path"}),
        ),
    )
    broker = PermissionBroker(path_guard=PathScopeGuard(Path("/tmp/aizen-nonexistent-root")))
    registry = ToolRegistry(broker=broker)
    registry.register(tool)

    result = await registry.execute("files.read", {"path": "../escape.txt"}, ToolContext())
    assert not result.ok
    assert "permission denied" in (result.error or "")
    assert executed == []


def _ok_tool() -> FunctionTool:
    async def _impl(args: _Args, ctx: ToolContext) -> ToolResult:
        return ToolResult(facts=[])

    return FunctionTool(
        _impl,
        ToolSpec(
            name="system.now",
            domain=Domain.SYSTEM,
            description="now",
            args_model=_Args,
            permission=PermissionTier.T0,
        ),
    )


async def test_direct_tool_run_is_refused_at_runtime() -> None:
    """The wrapper is not just convention: a direct call has no capability."""
    tool = _ok_tool()
    with pytest.raises(ToolExecutionNotPermitted):
        await tool.run(_Args(path="x"), ToolContext())


async def test_registry_execute_grants_the_execution_capability() -> None:
    registry = ToolRegistry()
    registry.register(_ok_tool())
    result = await registry.execute("system.now", {"path": "x"}, ToolContext())
    assert result.ok is True
