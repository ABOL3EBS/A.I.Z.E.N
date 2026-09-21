"""Turn / tool-call / audit writers (§28, §33): decisions are always recorded."""

from __future__ import annotations

from aizen.storage import Database, ToolCallRecorder, TurnRecorder


def test_turn_recorder_start_and_finish(db: Database) -> None:
    recorder = TurnRecorder(db)
    recorder.start("turn-abc", route_domain="finance", model_profile="light")
    recorder.finish("turn-abc", verified=True, final_state="IDLE", taint_level="CLEAN")
    row = db.fetchone("SELECT * FROM turns WHERE turn_id=?", ("turn-abc",))
    assert row is not None
    assert row["route_domain"] == "finance"
    assert row["verified"] == 1
    assert row["final_state"] == "IDLE"
    assert row["taint_level"] == "CLEAN"
    assert row["ended_at"] is not None


def test_tool_call_recorder_writes_decision_and_audit(db: Database) -> None:
    recorder = ToolCallRecorder(db)
    row_id = recorder.record_decision(
        turn_id="turn-1",
        call_id="call-1",
        tool="system.calculate",
        tier="T0",
        args={"expression": "1 + 1"},
        args_hash="deadbeef",
        decision="ALLOW",
        reason="allowed by default",
        tainted=False,
        taint_sources=[],
    )
    recorder.mark_running(row_id)
    recorder.finish(row_id, status="ok", result_summary="2")

    row = db.fetchone("SELECT * FROM tool_calls WHERE id=?", (row_id,))
    assert row is not None
    assert row["status"] == "ok"
    assert row["decision"] == "ALLOW"
    assert row["tier"] == "T0"
    assert row["args_hash"] == "deadbeef"
    assert row["ended_at"] is not None

    audit = db.fetchall("SELECT * FROM audit_log WHERE tool_call_id=?", ("call-1",))
    assert len(audit) == 1
    assert audit[0]["event"] == "permission"
    assert audit[0]["decision"] == "ALLOW"


def test_tool_call_recorder_redacts_amounts_and_accounts(db: Database) -> None:
    recorder = ToolCallRecorder(db)
    recorder.record_decision(
        turn_id="turn-2",
        call_id="call-2",
        tool="finance.spend_by_period",
        tier="T2",
        args={
            "period": "2026-08",
            "amount": "$1,234.56",
            "account": "12345678901",
        },
        args_hash="h",
        decision="ALLOW",
        reason="allowed by default",
        tainted=True,
        taint_sources=["web:example.com"],
    )
    row = db.fetchone("SELECT * FROM tool_calls WHERE call_id=?", ("call-2",))
    assert row is not None
    stored = row["args_json_redacted"]
    assert "$1,234.56" not in stored
    assert "12345678901" not in stored
    assert "[REDACTED]" in stored
    # non-sensitive args survive
    assert "2026-08" in stored
    assert row["tainted"] == 1
    assert "web:example.com" in row["taint_sources_json"]
