"""Permission broker (architecture Part II §28) + confirmation manager.

The broker is deterministic code: the LLM proposes a tool call, the broker
decides ALLOW | CONFIRM | DENY using the first-match-wins procedure in §28.
Every decision is written to ``audit_log`` and ``tool_calls`` by the caller.

Confirmation is out-of-band: the user approves/denies through the UI, never
through model text. An approval is bound to ``args_hash`` + ``turn_id`` and
times out (deny) after :data:`CONFIRM_TIMEOUT_S`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from aizen.domains import Domain
from aizen.tools.base import PermissionTier, ToolSpec
from aizen.tools.scope import PathScopeGuard, UrlGuard, personal_hygiene_reasons

CONFIRM_TIMEOUT_S = 60

# §28: only T2/T3 may be granted for the whole session; T4/T5 confirm every time.
SESSION_GRANTABLE_TIERS: frozenset[str] = frozenset({"T2", "T3"})


def hash_args(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolCallRequest(BaseModel):
    turn_id: str
    call_id: str
    tool: str
    args: dict[str, Any]
    args_hash: str
    route: Domain = Domain.SYSTEM
    toolset: tuple[str, ...] = ()
    turn_tainted: bool = False
    taint_sources: list[str] = []


class DecisionKind(StrEnum):
    ALLOW = "ALLOW"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


class Decision(BaseModel):
    kind: DecisionKind
    reason: str


class ConfirmRequest(BaseModel):
    request_id: str
    turn_id: str
    call_id: str
    tool: str
    tier: str
    summary: str
    preview: str | None = None
    args_hash: str
    tainted: bool = False
    taint_sources: list[str] = []
    expires_at: str


class ConfirmResponse(BaseModel):
    request_id: str
    approve: bool
    scope: str = "once"  # "once" | "session" (session only for T2/T3)


class ConfirmationManager:
    """Broker-side confirmation state: pending requests + session grants."""

    def __init__(
        self,
        *,
        timeout_s: float = CONFIRM_TIMEOUT_S,
        on_request: Any | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self._on_request = on_request
        self._now = now or time.monotonic
        self._pending: dict[str, asyncio.Event] = {}
        self._pending_requests: dict[str, ConfirmRequest] = {}
        self._results: dict[str, ConfirmResponse] = {}
        # session-scope grants: (tool, args_hash) -> monotonic expiry
        self._grants: dict[tuple[str, str], float] = {}

    async def request(self, cr: ConfirmRequest) -> ConfirmResponse:
        event = asyncio.Event()
        self._pending[cr.request_id] = event
        self._pending_requests[cr.request_id] = cr
        if self._on_request is not None:
            await self._on_request(cr)
        deadline = self._now() + self.timeout_s
        while not event.is_set():
            remaining = deadline - self._now()
            if remaining <= 0:
                event.set()
                self._results[cr.request_id] = ConfirmResponse(
                    request_id=cr.request_id, approve=False, scope="once"
                )
                break
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except TimeoutError:
                continue
        self._pending.pop(cr.request_id, None)
        self._pending_requests.pop(cr.request_id, None)
        return self._results[cr.request_id]

    def approval_is_valid(self, request_id: str, args_hash: str, turn_id: str) -> bool:
        """An approval is bound to the exact ``args_hash`` + ``turn_id`` (§28)."""
        pending = self._pending_requests.get(request_id)
        if pending is None:
            return False
        return pending.args_hash == args_hash and pending.turn_id == turn_id

    def respond(
        self,
        request_id: str,
        approve: bool,
        *,
        scope: str = "once",
        args_hash: str | None = None,
        turn_id: str | None = None,
    ) -> ConfirmResponse:
        """Resolve a pending request; a mismatched binding is forced to deny."""
        if (
            approve
            and args_hash is not None
            and turn_id is not None
            and not self.approval_is_valid(request_id, args_hash, turn_id)
        ):
            approve = False
        self._results[request_id] = ConfirmResponse(
            request_id=request_id, approve=approve, scope=scope
        )
        if event := self._pending.get(request_id):
            event.set()
        return self._results[request_id]

    def reject_all(self, reason: str = "canceled") -> None:
        for request_id in list(self._pending):
            self.respond(request_id, approve=False)

    def has_pending(self) -> bool:
        return bool(self._pending)

    # --- session grants (T2/T3 "session" scope) ---
    def grant(
        self, tool: str, args_hash: str, *, tier: str | None = None, grant_s: float = 3600.0
    ) -> None:
        # §28: session scope is allowed only for T2/T3, never for T4/T5.
        if tier is not None and tier not in SESSION_GRANTABLE_TIERS:
            return
        self._grants[(tool, args_hash)] = self._now() + grant_s

    def is_granted(self, tool: str, args_hash: str) -> bool:
        expiry = self._grants.get((tool, args_hash))
        return expiry is not None and expiry > self._now()


class PermissionBroker:
    """Deterministic §28 decision procedure (first match wins)."""

    def __init__(
        self,
        *,
        path_guard: PathScopeGuard | None = None,
        url_guard: UrlGuard | None = None,
        confirmer: ConfirmationManager | None = None,
    ) -> None:
        self.path_guard = path_guard
        self.url_guard = url_guard
        self.confirmer = confirmer or ConfirmationManager()

    def decide(self, request: ToolCallRequest, spec: ToolSpec) -> Decision:
        tier = spec.permission

        # 1. Tool not in this turn's toolset, or tier T6 -> DENY.
        if request.toolset and request.tool not in request.toolset:
            return Decision(kind=DecisionKind.DENY, reason=f"tool {request.tool} not in toolset")
        if tier is PermissionTier.T6:
            return Decision(kind=DecisionKind.DENY, reason="tier T6 is never exposed")

        # 2. Any path/URL argument fails scope checks -> DENY.
        reason = self._scope_reason(request, spec)
        if reason is not None:
            return Decision(kind=DecisionKind.DENY, reason=reason)

        # 3. T4/T5 -> CONFIRM (always, never cached).
        if tier in (PermissionTier.T4, PermissionTier.T5):
            return Decision(kind=DecisionKind.CONFIRM, reason="tier requires confirmation")

        # 3b. spec-level confirm=ALWAYS -> CONFIRM regardless of tier.
        if spec.confirm.value == "always":
            return Decision(kind=DecisionKind.CONFIRM, reason="tool requires confirmation")

        # 4. T3 and (turn tainted with personal data, or hygiene hit) -> CONFIRM.
        if tier is PermissionTier.T3:
            if request.turn_tainted and request.taint_sources:
                return Decision(kind=DecisionKind.CONFIRM, reason="network after tainted content")
            if personal_hygiene_reasons(json.dumps(request.args, default=str)):
                return Decision(kind=DecisionKind.CONFIRM, reason="query may expose personal data")

        # 5. T2 confirm-first-use and no session grant -> CONFIRM; else ALLOW.
        if (
            tier is PermissionTier.T2
            and spec.confirm.value == "first_use"
            and not self.confirmer.is_granted(request.tool, request.args_hash)
        ):
            return Decision(kind=DecisionKind.CONFIRM, reason="first use of read-sensitive tool")

        # 6. Allowed-by-default session grant overrides a CONFIRM decision.
        if spec.confirm.value == "first_use" and self.confirmer.is_granted(
            request.tool, request.args_hash
        ):
            return Decision(kind=DecisionKind.ALLOW, reason="session grant")

        # T0/T1 and the remainder -> ALLOW.
        return Decision(kind=DecisionKind.ALLOW, reason="allowed by default")

    def _scope_reason(self, request: ToolCallRequest, spec: ToolSpec) -> str | None:
        for arg in spec.path_args:
            value = request.args.get(arg)
            if (
                isinstance(value, str)
                and self.path_guard is not None
                and not self.path_guard.check(value)
            ):
                return f"path argument {arg} is outside the allowed scope"
        for arg in spec.url_args:
            value = request.args.get(arg)
            if (
                isinstance(value, str)
                and self.url_guard is not None
                and not self.url_guard.check(value)
            ):
                return f"url argument {arg} is not allowed"
        return None

    def build_confirm_request(
        self,
        request: ToolCallRequest,
        spec: ToolSpec,
        *,
        summary: str,
        preview: str | None = None,
    ) -> ConfirmRequest:
        expires = datetime.now(UTC) + timedelta(seconds=CONFIRM_TIMEOUT_S)
        return ConfirmRequest(
            request_id=secrets.token_hex(8),
            turn_id=request.turn_id,
            call_id=request.call_id,
            tool=request.tool,
            tier=spec.permission.value,
            summary=summary,
            preview=preview,
            args_hash=request.args_hash,
            tainted=request.turn_tainted,
            taint_sources=list(request.taint_sources),
            expires_at=expires.isoformat(),
        )
