"""Log redaction canary: amounts/accounts must never reach a rendered log line."""

from __future__ import annotations

import structlog
from aizen.observability.logging import redact_event_dict, setup_logging

FAKE_AMOUNT = "$1,234.56"
FAKE_ACCOUNT = "12345678901"
FAKE_IBAN = "DE89370400440532013000"


def test_redact_event_dict_strips_amounts_and_accounts() -> None:
    out = redact_event_dict(
        None,
        "info",
        {
            "event": f"paid {FAKE_AMOUNT} from acct {FAKE_ACCOUNT}",
            "nested": {"iban": FAKE_IBAN, "note": "keep me"},
        },
    )
    assert FAKE_AMOUNT not in out["event"]
    assert FAKE_ACCOUNT not in out["event"]
    assert FAKE_IBAN not in out["nested"]["iban"]
    assert out["nested"]["note"] == "keep me"


def test_setup_logging_installs_the_redaction_processor() -> None:
    setup_logging("INFO")
    assert redact_event_dict in structlog.get_config()["processors"]


def test_rendered_log_line_is_redacted(capsys) -> None:
    setup_logging("INFO")
    structlog.get_logger("aizen.canary").info("transfer", amount=FAKE_AMOUNT, account=FAKE_ACCOUNT)
    err = capsys.readouterr().err
    assert FAKE_AMOUNT not in err
    assert FAKE_ACCOUNT not in err
