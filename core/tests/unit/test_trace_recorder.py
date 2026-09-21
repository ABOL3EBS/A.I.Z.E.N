"""Turn-trace writer (§31/§33): route decisions, traces and redaction canary."""

from __future__ import annotations

from decimal import Decimal

from aizen.facts import money
from aizen.storage import TurnTraceRecorder
from aizen.verifier import verify_answer

FAKE_AMOUNT = "$1,234.56"
FAKE_ACCOUNT = "12345678901"


def test_records_route_decision(db) -> None:
    recorder = TurnTraceRecorder(db)
    recorder.record_route(
        "turn-route", domain="FINANCE", rule_hits=["finance_keyword"], model_overrode=False
    )
    row = db.fetchone("SELECT * FROM route_log WHERE turn_id=?", ("turn-route",))
    assert row is not None
    assert row["domain"] == "FINANCE"
    assert "finance_keyword" in row["rule_hits_json"]
    assert row["model_overrode"] == 0


def test_trace_redacts_amounts_and_accounts_by_default(db) -> None:
    recorder = TurnTraceRecorder(db)
    spans = [
        {
            "name": "tool",
            "tool": "finance.spend",
            "args": {"amount": FAKE_AMOUNT, "account": FAKE_ACCOUNT},
            "note": f"paid {FAKE_AMOUNT}",
        }
    ]
    verdict = verify_answer("You spent $1,234.56.", [money(Decimal("1234.56"), path="total")])
    recorder.record_trace(
        "turn-redacted",
        spans=spans,
        tokens={"prompt": 10, "completion": 4},
        tainted=True,
        verified=False,
        verify=verdict,
        redacted=True,
    )
    row = db.fetchone("SELECT * FROM turn_traces WHERE turn_id=?", ("turn-redacted",))
    assert row is not None
    assert FAKE_AMOUNT not in row["spans_json"]
    assert FAKE_ACCOUNT not in row["spans_json"]
    assert FAKE_AMOUNT not in row["verify_json"]
    assert "[REDACTED]" in row["spans_json"]
    assert row["redacted"] == 1
    assert row["verified"] == 0
    assert row["tainted"] == 1


def test_trace_keeps_content_when_explicitly_unredacted(db) -> None:
    recorder = TurnTraceRecorder(db)
    recorder.record_trace(
        "turn-debug",
        spans=[{"name": "tool", "note": FAKE_AMOUNT, "account": FAKE_ACCOUNT}],
        verified=True,
        redacted=False,
    )
    row = db.fetchone("SELECT * FROM turn_traces WHERE turn_id=?", ("turn-debug",))
    assert row is not None
    assert FAKE_AMOUNT in row["spans_json"]
    assert FAKE_ACCOUNT in row["spans_json"]
    assert row["redacted"] == 0
    assert row["verified"] == 1
