from __future__ import annotations

from aizen.observability.redaction import Redactor, is_sensitive_key, redact


def test_redacts_credit_card() -> None:
    assert "4111 1111 1111 1111" not in redact("pay with 4111 1111 1111 1111 now")
    assert "[CARD]" in redact("pay with 4111-1111-1111-1111 now")


def test_redacts_amounts() -> None:
    assert redact("I spent $1,234.56 today") == "I spent [AMOUNT] today"
    assert redact("saw €50.00 in the drawer") == "saw [AMOUNT] in the drawer"


def test_redacts_api_keys() -> None:
    assert "[REDACTED]" in redact("Authorization: Bearer hunter2")


def test_redacts_iban() -> None:
    assert "[ACCOUNT]" in redact("IBAN DE89370400440532013000 present")


def test_plain_text_untouched() -> None:
    assert redact("the quick brown fox") == "the quick brown fox"


def test_currency_redaction_can_be_disabled() -> None:
    r = Redactor(currency=False)
    assert "$1,234.56" in r.redact("spent $1,234.56")


def test_metric_keys_are_not_redacted() -> None:
    payload = {
        "prompt_tokens": 12,
        "completion_tokens": 30,
        "total_tokens": 42,
        "duration_ms": 900,
        "ttft_ms": 250,
        "num_ctx": 8192,
        "seq": 7,
    }
    assert Redactor().redact_mapping(payload) == payload


def test_whole_segment_sensitive_keys_are_redacted() -> None:
    out = Redactor().redact_mapping(
        {
            "account_id": "12345678901",
            "amount": 12,
            "sort_code": "12-34-56",
            "api_key": "sk-live",
            "balance": 5,
            "card_number": "4111111111111111",
        }
    )
    assert out == {
        "account_id": "[REDACTED]",
        "amount": "[REDACTED]",
        "sort_code": "[REDACTED]",
        "api_key": "[REDACTED]",
        "balance": "[REDACTED]",
        "card_number": "[REDACTED]",
    }


def test_unrelated_keys_containing_a_sensitive_substring_are_kept() -> None:
    out = Redactor().redact_mapping({"subtotal_note": "keep", "tokenizer": "bpe"})
    assert out == {"subtotal_note": "keep", "tokenizer": "bpe"}


def test_is_sensitive_key_handles_segments_and_allowlist() -> None:
    assert is_sensitive_key("total_tokens") is False
    assert is_sensitive_key("token") is True
    assert is_sensitive_key("Account Number") is True
    assert is_sensitive_key("grand_total") is True
    assert is_sensitive_key("notes") is False
