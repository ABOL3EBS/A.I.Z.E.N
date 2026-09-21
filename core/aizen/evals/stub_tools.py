"""Deterministic stub tools for the model gate (Part II §32).

The gate runs the REAL router, registry and agent loop; only the tools are
stubs so the eval never touches finance workbooks, the index, or the network.
Every stub returns canned facts — and for configurable no-data cases a clear
"no data" failure — never a fabricated number. Web/knowledge stubs return
tainted origins so taint propagation is exercised for real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from aizen.domains import Domain
from aizen.facts import Fact, date_fact, number, text_fact
from aizen.orb import OrbState
from aizen.tools import FunctionTool, Origin, PermissionTier, ToolResult, ToolSpec

_KNOWN_SPEND: dict[str, str] = {
    "groceries": "148.75",
    "coffee": "64.50",
    "transport": "212.30",
}

_STUB_NAMES = (
    "finance.describe",
    "finance.current_balances",
    "finance.spend_by_period",
    "finance.affordability",
    "knowledge.search",
    "files.read",
    "memory.recall",
    "web.search",
    "web.fetch",
)


@dataclass
class StubRecorder:
    """Per-question capture of what the stubs actually returned."""

    facts: list[Fact] = field(default_factory=list)
    executed: list[str] = field(default_factory=list)

    def add(self, tool: str, facts: list[Fact]) -> None:
        self.executed.append(tool)
        self.facts.extend(facts)


class _Args(BaseModel):
    pass


class _SpendArgs(BaseModel):
    category: str
    period: str | None = None


class _ItemArgs(BaseModel):
    item: str | None = None


class _QueryArgs(BaseModel):
    query: str


class _UrlArgs(BaseModel):
    url: str


class _PathArgs(BaseModel):
    path: str


class _TopicArgs(BaseModel):
    topic: str


def _spec(
    name: str,
    args_model: type[BaseModel],
    *,
    domain: Domain,
    tier: PermissionTier,
    origin: Origin,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        domain=domain,
        description=f"Deterministic stub for {name} (eval-only).",
        args_model=args_model,
        permission=tier,
        result_origin=origin,
        orb_state=OrbState.SEARCHING,
    )


def build_stub_tools(
    recorder: StubRecorder, config: dict[str, dict[str, object]] | None = None
) -> list[FunctionTool]:
    """Build every eval stub, honoring a question's per-tool ``stub`` config."""

    already = dict.fromkeys(_STUB_NAMES)
    config = config or {}

    async def describe(args: _Args, ctx: Any) -> ToolResult:
        result = ToolResult(
            facts=[text_fact("current profile is validated", path="summary")],
            narration_template="Your finance workbook is {summary}.",
        )
        recorder.add("finance.describe", result.facts)
        return result

    async def balances(args: _Args, ctx: Any) -> ToolResult:
        result = ToolResult(
            facts=[
                number("5240.25", path="checking_balance", unit="USD", currency="USD"),
                date_fact(datetime(2026, 9, 1, tzinfo=UTC), path="as_of"),
            ],
            narration_template="Your checking balance is {checking_balance} as of {as_of}.",
        )
        recorder.add("finance.current_balances", result.facts)
        return result

    async def spend(args: _SpendArgs, ctx: Any) -> ToolResult:
        amount = _KNOWN_SPEND.get((args.category or "").strip().lower())
        if amount is None:
            return ToolResult.fail(f"no transactions for category {args.category!r}")
        result = ToolResult(
            facts=[
                number(amount, path="spend", unit="USD", currency="USD"),
                text_fact(args.category, path="category"),
            ],
            narration_template="You spent {spend} on {category} in that period.",
        )
        recorder.add("finance.spend_by_period", result.facts)
        return result

    async def afford(args: _ItemArgs, ctx: Any) -> ToolResult:
        item = args.item or "this"
        result = ToolResult(
            facts=[
                number("312.00", path="safe_margin", unit="USD", currency="USD"),
                text_fact(item, path="item"),
            ],
            narration_template="Based on your safe margin of {safe_margin}, {item} is affordable.",
        )
        recorder.add("finance.affordability", result.facts)
        return result

    async def knowledge_search(args: _QueryArgs, ctx: Any) -> ToolResult:
        result = ToolResult(
            facts=[text_fact(f"matches for {args.query}", path="results")],
            narration_template="Found in your notes: {results}.",
        )
        recorder.add("knowledge.search", result.facts)
        return result

    async def files_read(args: _PathArgs, ctx: Any) -> ToolResult:
        result = ToolResult(
            facts=[text_fact(f"excerpt of {args.path}", path="content")],
            narration_template="The file reads: {content}.",
        )
        recorder.add("files.read", result.facts)
        return result

    async def memory_recall(args: _TopicArgs, ctx: Any) -> ToolResult:
        result = ToolResult(
            facts=[text_fact("noted in memory", path="recall")],
            narration_template="I recall: {recall}.",
        )
        recorder.add("memory.recall", result.facts)
        return result

    async def web_search(args: _QueryArgs, ctx: Any) -> ToolResult:
        result = ToolResult(
            facts=[text_fact(f"search results for {args.query}", path="results")],
            narration_template="Web search returned: {results}.",
        )
        recorder.add("web.search", result.facts)
        return result

    async def web_fetch(args: _UrlArgs, ctx: Any) -> ToolResult:
        result = ToolResult(
            facts=[text_fact(f"excerpt of {args.url}", path="excerpt")],
            narration_template="The page says: {excerpt}.",
        )
        recorder.add("web.fetch", result.facts)
        return result

    handlers: dict[str, Any] = {
        "finance.describe": describe,
        "finance.current_balances": balances,
        "finance.spend_by_period": spend,
        "finance.affordability": afford,
        "knowledge.search": knowledge_search,
        "files.read": files_read,
        "memory.recall": memory_recall,
        "web.search": web_search,
        "web.fetch": web_fetch,
    }
    args_models: dict[str, type[BaseModel]] = {
        "finance.describe": _Args,
        "finance.current_balances": _Args,
        "finance.spend_by_period": _SpendArgs,
        "finance.affordability": _ItemArgs,
        "knowledge.search": _QueryArgs,
        "files.read": _PathArgs,
        "memory.recall": _TopicArgs,
        "web.search": _QueryArgs,
        "web.fetch": _UrlArgs,
    }
    tiers: dict[str, tuple[Domain, PermissionTier, Origin]] = {
        "finance.describe": (Domain.FINANCE, PermissionTier.T2, Origin.SYSTEM),
        "finance.current_balances": (Domain.FINANCE, PermissionTier.T2, Origin.FINANCE_FACT),
        "finance.spend_by_period": (Domain.FINANCE, PermissionTier.T2, Origin.FINANCE_FACT),
        "finance.affordability": (Domain.FINANCE, PermissionTier.T2, Origin.FINANCE_FACT),
        "knowledge.search": (Domain.PERSONAL, PermissionTier.T1, Origin.LOCAL_CONTENT),
        "files.read": (Domain.PERSONAL, PermissionTier.T1, Origin.LOCAL_CONTENT),
        "memory.recall": (Domain.PERSONAL, PermissionTier.T1, Origin.MEMORY_EXPLICIT),
        "web.search": (Domain.WEB, PermissionTier.T3, Origin.WEB),
        "web.fetch": (Domain.WEB, PermissionTier.T3, Origin.WEB),
    }

    tools: list[FunctionTool] = []
    for name in _STUB_NAMES:
        handler = handlers[name]
        args_model = args_models[name]
        domain, tier, origin = tiers[name]

        if name == "finance.spend_by_period":
            wanted = str(config.get(name, {}).get("category", "")).lower()
            if wanted not in _KNOWN_SPEND:
                async def empty_spend(args: _SpendArgs, ctx: Any) -> ToolResult:
                    return ToolResult.fail(f"no transactions for category {args.category!r}")

                handler = empty_spend

        fn = handler.__annotations__.get("ctx")
        tools.append(
            FunctionTool(
                handler,
                _spec(name, args_model, domain=domain, tier=tier, origin=origin),
            )
        )
    return tools


__all__ = ["StubRecorder", "build_stub_tools"]