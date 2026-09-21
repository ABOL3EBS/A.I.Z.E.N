"""Permission broker (§28): deterministic first-match-wins decisions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aizen.domains import Domain
from aizen.tools.base import ConfirmPolicy, PermissionTier, ToolSpec
from aizen.tools.broker import (
    ConfirmationManager,
    ConfirmRequest,
    DecisionKind,
    PermissionBroker,
    ToolCallRequest,
    hash_args,
)
from aizen.tools.scope import PathScopeGuard, UrlGuard
from pydantic import BaseModel


class Args(BaseModel):
    path: str | None = None
    url: str | None = None
    query: str | None = None


def _spec(
    *,
    tier: PermissionTier,
    confirm: ConfirmPolicy = ConfirmPolicy.NEVER,
    path_args: frozenset[str] = frozenset(),
    url_args: frozenset[str] = frozenset(),
) -> ToolSpec:
    return ToolSpec(
        name="t",
        domain=Domain.SYSTEM,
        description="test",
        args_model=Args,
        permission=tier,
        confirm=confirm,
        path_args=path_args,
        url_args=url_args,
    )


def _req(tool: str = "t", **args: object) -> ToolCallRequest:
    payload = {k: v for k, v in args.items()}
    return ToolCallRequest(
        turn_id="turn-1",
        call_id="call-1",
        tool=tool,
        args=payload,
        args_hash=hash_args(payload),
        toolset=(tool,),
    )


def _confirm_request(
    *, request_id: str = "req-1", turn_id: str = "turn-1", args_hash: str = "hash-a"
) -> ConfirmRequest:
    return ConfirmRequest(
        request_id=request_id,
        turn_id=turn_id,
        call_id="call-1",
        tool="t",
        tier="T4",
        summary="test",
        args_hash=args_hash,
        expires_at=(datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
    )


def test_tool_outside_toolset_denied() -> None:
    broker = PermissionBroker()
    request = _req("t")
    request.toolset = ("other.tool",)
    decision = broker.decide(request, _spec(tier=PermissionTier.T0))
    assert decision.kind is DecisionKind.DENY


def test_t6_never_exposed() -> None:
    decision = PermissionBroker().decide(_req(), _spec(tier=PermissionTier.T6))
    assert decision.kind is DecisionKind.DENY


def test_t0_and_t1_allowed() -> None:
    broker = PermissionBroker()
    assert broker.decide(_req(), _spec(tier=PermissionTier.T0)).kind is DecisionKind.ALLOW
    assert broker.decide(_req(), _spec(tier=PermissionTier.T1)).kind is DecisionKind.ALLOW


def test_t4_t5_always_confirm() -> None:
    broker = PermissionBroker()
    assert broker.decide(_req(), _spec(tier=PermissionTier.T4)).kind is DecisionKind.CONFIRM
    assert broker.decide(_req(), _spec(tier=PermissionTier.T5)).kind is DecisionKind.CONFIRM


def test_confirm_policy_always_forces_confirm() -> None:
    spec = _spec(tier=PermissionTier.T0, confirm=ConfirmPolicy.ALWAYS)
    assert PermissionBroker().decide(_req(), spec).kind is DecisionKind.CONFIRM


def test_t3_confirm_after_taint() -> None:
    broker = PermissionBroker()
    spec = _spec(tier=PermissionTier.T3)
    clean = _req(url="https://example.com")
    assert broker.decide(clean, spec).kind is DecisionKind.ALLOW
    tainted = _req(url="https://example.com")
    tainted.turn_tainted = True
    tainted.taint_sources = ["web:example.com"]
    decision = broker.decide(tainted, spec)
    assert decision.kind is DecisionKind.CONFIRM
    assert "tainted" in decision.reason


def test_t3_confirm_on_personal_hygiene() -> None:
    spec = _spec(tier=PermissionTier.T3)
    decision = PermissionBroker().decide(_req(query="transfer $500"), spec)
    assert decision.kind is DecisionKind.CONFIRM


def test_t2_first_use_confirm_then_session_grant_allows() -> None:
    broker = PermissionBroker()
    spec = _spec(tier=PermissionTier.T2, confirm=ConfirmPolicy.FIRST_USE)
    request = _req()
    assert broker.decide(request, spec).kind is DecisionKind.CONFIRM
    broker.confirmer.grant(request.tool, request.args_hash)
    assert broker.decide(request, spec).kind is DecisionKind.ALLOW


def test_path_argument_outside_scope_denied(tmp_path: Path) -> None:
    guard = PathScopeGuard(tmp_path)
    broker = PermissionBroker(path_guard=guard)
    spec = _spec(tier=PermissionTier.T1, path_args=frozenset({"path"}))
    decision = broker.decide(_req(path="../secret.txt"), spec)
    assert decision.kind is DecisionKind.DENY
    assert "scope" in decision.reason


def test_url_argument_bad_scheme_denied() -> None:
    broker = PermissionBroker(url_guard=UrlGuard())
    spec = _spec(tier=PermissionTier.T3, url_args=frozenset({"url"}))
    assert broker.decide(_req(url="file:///etc/passwd"), spec).kind is DecisionKind.DENY


def test_session_grant_refused_for_t4_t5() -> None:
    manager = ConfirmationManager()
    manager.grant("t4.tool", "h", tier="T4")
    manager.grant("t5.tool", "h", tier="T5")
    assert not manager.is_granted("t4.tool", "h")
    assert not manager.is_granted("t5.tool", "h")
    manager.grant("t2.tool", "h", tier="T2")
    manager.grant("t3.tool", "h", tier="T3")
    assert manager.is_granted("t2.tool", "h")
    assert manager.is_granted("t3.tool", "h")


def test_t4_confirm_even_with_first_use_policy_and_grant() -> None:
    broker = PermissionBroker()
    spec = _spec(tier=PermissionTier.T4, confirm=ConfirmPolicy.FIRST_USE)
    request = _req()
    # Even a stray grant must not turn a T4 call into an ALLOW.
    broker.confirmer.grant(request.tool, request.args_hash, tier="T2")
    assert broker.decide(request, spec).kind is DecisionKind.CONFIRM


def test_session_grant_is_bound_to_args_hash() -> None:
    manager = ConfirmationManager()
    manager.grant("finance.spend_by_period", hash_args({"period": "2026-08"}), tier="T2")
    assert manager.is_granted("finance.spend_by_period", hash_args({"period": "2026-08"}))
    assert not manager.is_granted("finance.spend_by_period", hash_args({"period": "2026-07"}))


async def test_approval_binding_rejects_changed_args() -> None:
    manager = ConfirmationManager(timeout_s=5)
    request = _confirm_request(args_hash="hash-a")
    task = asyncio.create_task(manager.request(request))
    await asyncio.sleep(0)
    # Approval arrives with a different args_hash -> forced deny.
    manager.respond(request.request_id, True, args_hash="hash-b", turn_id="turn-1")
    assert (await task).approve is False


async def test_approval_binding_rejects_replay_on_other_turn() -> None:
    manager = ConfirmationManager(timeout_s=5)
    request = _confirm_request(turn_id="turn-1", args_hash="hash-a")
    task = asyncio.create_task(manager.request(request))
    await asyncio.sleep(0)
    manager.respond(request.request_id, True, args_hash="hash-a", turn_id="turn-2")
    assert (await task).approve is False


async def test_approval_binding_accepts_exact_match() -> None:
    manager = ConfirmationManager(timeout_s=5)
    request = _confirm_request(turn_id="turn-1", args_hash="hash-a")
    task = asyncio.create_task(manager.request(request))
    await asyncio.sleep(0)
    manager.respond(request.request_id, True, args_hash="hash-a", turn_id="turn-1")
    assert (await task).approve is True


def test_approval_is_invalid_after_request_resolves() -> None:
    manager = ConfirmationManager(timeout_s=5)
    request = _confirm_request()
    assert not manager.approval_is_valid(request.request_id, "hash-a", "turn-1")


def test_confirm_response_times_out_to_deny() -> None:
    manager = ConfirmationManager(timeout_s=0.02)
    request = _confirm_request()

    async def go() -> bool:
        response = await manager.request(request)
        return response.approve

    # No responder is registered, so the request must resolve to deny.
    assert asyncio.run(go()) is False
