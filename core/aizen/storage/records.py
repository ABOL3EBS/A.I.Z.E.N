"""Writers for turns, tool_calls and the audit log (architecture §28/§33).

Financial values and account numbers are redacted before they reach the DB; the
agent loop never logs amounts or file contents (Section 18 / §30).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from aizen.observability.redaction import Redactor
from aizen.storage.db import Database

if TYPE_CHECKING:
    from aizen.verifier import VerifyResult


class TurnRecorder:
    def __init__(self, db: Database) -> None:
        self.db = db

    def start(
        self,
        turn_id: str,
        *,
        conversation_id: int | None = None,
        route_domain: str | None = None,
        model_profile: str | None = None,
    ) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO turns"
                " (turn_id, conversation_id, route_domain, model_profile) VALUES (?,?,?,?)",
                (turn_id, conversation_id, route_domain, model_profile),
            )

    def finish(
        self,
        turn_id: str,
        *,
        verified: bool | None,
        final_state: str,
        taint_level: str,
    ) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "UPDATE turns SET verified=?, final_state=?, taint_level=?,"
                " ended_at=datetime('now') WHERE turn_id=?",
                (None if verified is None else int(verified), final_state, taint_level, turn_id),
            )


class ToolCallRecorder:
    """Records every broker decision to ``tool_calls`` and ``audit_log``."""

    def __init__(self, db: Database, *, redactor: Redactor | None = None) -> None:
        self.db = db
        self.redactor = redactor or Redactor()

    def record_decision(
        self,
        *,
        turn_id: str,
        call_id: str,
        tool: str,
        tier: str,
        args: dict[str, Any],
        args_hash: str,
        decision: str,
        reason: str,
        tainted: bool,
        taint_sources: list[str],
    ) -> int:
        redacted = json.dumps(self.redactor.redact_mapping(args), default=str)
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO tool_calls"
                " (turn_id, call_id, tool, tier, args_json_redacted, args_hash, decision,"
                "  decision_reason, tainted, taint_sources_json, status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    turn_id,
                    call_id,
                    tool,
                    tier,
                    redacted,
                    args_hash,
                    decision,
                    reason,
                    int(tainted),
                    json.dumps(taint_sources),
                    "decided",
                ),
            )
            cur.execute(
                "INSERT INTO audit_log (event, tool, tool_call_id, decision, tainted, detail)"
                " VALUES (?,?,?,?,?,?)",
                ("permission", tool, call_id, decision, int(tainted), reason),
            )
            row = cur.execute(
                "SELECT id FROM tool_calls WHERE turn_id=? AND call_id=?", (turn_id, call_id)
            ).fetchone()
        return int(row["id"])

    def mark_running(self, row_id: int) -> None:
        with self.db.transaction() as cur:
            cur.execute("UPDATE tool_calls SET status='running' WHERE id=?", (row_id,))

    def finish(self, row_id: int, *, status: str, result_summary: str) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "UPDATE tool_calls SET status=?, result_summary=?,"
                " ended_at=datetime('now') WHERE id=?",
                (status, self.redactor.redact(result_summary), row_id),
            )


class TurnTraceRecorder:
    """Writes route decisions and turn traces (§31/§33).

    Traces are redacted by default: amounts, account references and secrets are
    stripped before they reach the DB. Raw claim text is only kept when
    ``redacted=False`` (explicit ``debug_content``).
    """

    def __init__(self, db: Database, *, redactor: Redactor | None = None) -> None:
        self.db = db
        self.redactor = redactor or Redactor()

    def record_route(
        self,
        turn_id: str,
        *,
        domain: str,
        rule_hits: list[str] | tuple[str, ...] = (),
        model_overrode: bool = False,
    ) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO route_log (turn_id, domain, rule_hits_json, model_overrode)"
                " VALUES (?,?,?,?)",
                (turn_id, domain, json.dumps(list(rule_hits)), int(model_overrode)),
            )

    def record_trace(
        self,
        turn_id: str,
        *,
        conversation_id: int | None = None,
        spans: list[dict[str, Any]] | None = None,
        tokens: dict[str, int] | None = None,
        tainted: bool = False,
        verified: bool | None = None,
        verify: VerifyResult | None = None,
        redacted: bool = True,
    ) -> None:
        spans_payload = self._redact(spans or [], redacted)
        tokens_payload = dict(tokens or {})
        verify_payload = verify.to_trace_dict(redact=redacted) if verify is not None else None
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO turn_traces"
                " (conversation_id, turn_id, spans_json, tokens_json, verify_json,"
                "  verified, tainted, redacted) VALUES (?,?,?,?,?,?,?,?)",
                (
                    conversation_id,
                    turn_id,
                    json.dumps(spans_payload, default=str),
                    json.dumps(tokens_payload, default=str),
                    json.dumps(verify_payload, default=str) if verify_payload else None,
                    None if verified is None else int(verified),
                    int(tainted),
                    int(redacted),
                ),
            )

    def _redact(self, payload: Any, redacted: bool) -> Any:
        if not redacted:
            return payload
        return self.redactor.redact_mapping(payload)
