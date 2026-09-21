"""Redaction of sensitive values from logs and traces (Section 18 / §30).

Financial values and account numbers are **always** redacted, even when the
explicit ``debug_content`` flag is on; that flag only governs whether raw file
content may be logged. This module is the single gate.
"""

from __future__ import annotations

import re
from typing import Any

# 13-19 digits, optionally grouped -> card numbers.
_CARD_RX = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
# Currency-prefixed amounts: $1,234.56 / €50 / £9.99 / ¥1200
_CURRENCY_RX = re.compile(
    r"[$€£¥]\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|JPY|CHF|AUD|CAD)"
    r"|\d+(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|JPY|CHF|AUD|CAD)\b"
)
_KEY_RX = re.compile(
    r"(?i)(\b(?:api[_-]?key|apikey|secret|token|password|passwd|authorization|bearer|"
    r"private[_-]?key|client[_-]?secret)\b[^\n]{0,80})"
)
_IBAN_RX = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_SSN_RX = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_ACCOUNT_RX = re.compile(
    r"(?i)\b(?:account|acct|routing|sort\s?code)\s*(?:number|no\.?|#)?\s*[:=]?\s*\d[\d -]{4,}"
)
_EMAIL_RX = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Values stored under these keys are redacted wholesale, even when the value is
# a bare number the content patterns would miss (e.g. account=12345678901).
# Matching is on whole key segments (``account_id`` yes, ``subtotal`` no) so we
# never over-redact unrelated fields.
_SENSITIVE_KEY_WORDS = frozenset(
    {
        "account",
        "acct",
        "iban",
        "card",
        "ssn",
        "routing",
        "sort",
        "balance",
        "amount",
        "salary",
        "total",
        "password",
        "passwd",
        "secret",
        "token",
        "api",
        "key",
        "authorization",
        "bearer",
        "private",
    }
)
# Operational counters/timings are not sensitive and must survive redaction so
# traces and logs stay useful (e.g. ``total_tokens`` must not match ``total``).
_METRIC_KEY_ALLOWLIST = frozenset(
    {
        "tokens",
        "token_count",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "context_tokens",
        "budget_tokens",
        "used_tokens",
        "max_tokens",
        "num_ctx",
        "count",
        "duration_ms",
        "elapsed_ms",
        "latency_ms",
        "prompt_ms",
        "ttft_ms",
        "tokens_per_s",
        "seq",
        "spans",
    }
)
_KEY_SEGMENT_RX = re.compile(r"[^a-z0-9]+")


def is_sensitive_key(key: str) -> bool:
    """True when a mapping key names a sensitive value (whole-segment match)."""
    normalized = key.strip().lower()
    if normalized in _METRIC_KEY_ALLOWLIST:
        return False
    return any(part in _SENSITIVE_KEY_WORDS for part in _KEY_SEGMENT_RX.split(normalized) if part)


_OMITTED = "[content omitted]"
_REDACTED = "[REDACTED]"


class Redactor:
    def __init__(
        self,
        *,
        currency: bool = True,
        credentials: bool = True,
        debug_content: bool = False,
    ) -> None:
        self.currency = currency
        self.credentials = credentials
        self.debug_content = debug_content

    def redact(self, text: str) -> str:
        """Always-on redaction: cards, IBANs, SSNs, account refs, amounts, keys, emails."""
        out = _CARD_RX.sub("[CARD]", text)
        out = _IBAN_RX.sub("[ACCOUNT]", out)
        out = _SSN_RX.sub("[SSN]", out)
        out = _ACCOUNT_RX.sub("[ACCOUNT]", out)
        if self.currency:
            out = _CURRENCY_RX.sub("[AMOUNT]", out)
        if self.credentials:
            out = _KEY_RX.sub("[REDACTED]", out)
        out = _EMAIL_RX.sub("[EMAIL]", out)
        return out

    def redact_content(self, text: str) -> str:
        """File/document bodies: omitted unless ``debug_content`` is explicitly set."""
        if not self.debug_content:
            return _OMITTED
        return self.redact(text)

    def redact_mapping(self, data: Any) -> Any:
        if isinstance(data, str):
            return self.redact(data)
        if isinstance(data, dict):
            out: dict[Any, Any] = {}
            for key, value in data.items():
                if isinstance(key, str) and is_sensitive_key(key):
                    out[key] = _REDACTED
                else:
                    out[key] = self.redact_mapping(value)
            return out
        if isinstance(data, list):
            return [self.redact_mapping(v) for v in data]
        return data


_default = Redactor()


def redact(text: str) -> str:
    return _default.redact(text)


def redact_content(text: str) -> str:
    return _default.redact_content(text)


def redact_mapping(data: Any) -> Any:
    return _default.redact_mapping(data)
