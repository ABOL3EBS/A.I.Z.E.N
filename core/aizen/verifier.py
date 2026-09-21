"""Deterministic answer verifier (§31): every number must match a tool fact.

The LLM proposes prose; this module decides. It extracts numeric/date claims
from a draft answer and matches each against the per-turn :class:`FactTable`.
Anything unmatched (and not exempt) fails, which forces a regenerate and, if
that still fails, a deterministic template fallback.

Exemptions are deliberately small and documented:

* numbers the user themselves wrote in the current turn (echo);
* ordinals ("first", "3rd") and list markers ("1.", "2)");
* clock times ("3:00 PM") and year references ("in 2026");
* spelled-out cardinals used with a time/quantity unit ("one month",
  "two accounts"), plus a bare spelled "one"/"a"/"an" article.

Derived numbers (arithmetic the model did itself) are **never** exempted: the
model must call a tool and use the returned fact.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from aizen.facts import Fact, FactKind
from aizen.normalize import normalize_number_words, words_to_number

_CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
}
_CURRENCY_WORDS: dict[str, str] = {
    "dollar": "USD",
    "dollars": "USD",
    "usd": "USD",
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
    "yen": "JPY",
    "jpy": "JPY",
    "rupee": "INR",
    "rupees": "INR",
    "inr": "INR",
}
_MONTHS: dict[str, int] = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_MONTH_NAMES = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO_DATE_RX = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_MONTH_DAY_RX = re.compile(
    rf"\b({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE
)
_DAY_MONTH_RX = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_NAMES})\s+(\d{{4}})\b", re.IGNORECASE
)
_MONEY_SYMBOL_RX = re.compile(r"([-−]?)([$€£¥₹])\s?(\d[\d,]*(?:\.\d+)?)")
_MONEY_WORD_RX = re.compile(
    r"([-−]?)(\d[\d,]*(?:\.\d+)?)\s?"
    r"(dollars?|euros?|pounds?|usd|eur|gbp|yen|jpy|rupees?|inr)\b",
    re.IGNORECASE,
)
_PERCENT_RX = re.compile(r"([-−]?)(\d[\d,]*(?:\.\d+)?)\s?(?:%|percent\b)", re.IGNORECASE)
_CLOCK_RX = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?(?![\d:])", re.IGNORECASE)
_ORDINAL_DIGIT_RX = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE)
_LIST_MARKER_RX = re.compile(r"(?m)^\s*\d+[.)]\s")
_YEAR_RX = re.compile(r"\b(?:in|since|by|during|year|fiscal|fy)\s+(\d{4})\b", re.IGNORECASE)
_BARE_NUMBER_RX = re.compile(r"([-−]?)(\d[\d,]*(?:\.\d+)?)")
_SPELLED_RX = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"billion|and)(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
    r"thousand|million|billion|and))*\b",
    re.IGNORECASE,
)
_SPELLED_UNITS = frozenset(
    {
        "month",
        "months",
        "week",
        "weeks",
        "day",
        "days",
        "year",
        "years",
        "hour",
        "hours",
        "minute",
        "minutes",
        "second",
        "seconds",
        "time",
        "times",
    }
)
_COUNT_NOUNS = _SPELLED_UNITS | frozenset(
    {
        "account",
        "accounts",
        "item",
        "items",
        "transaction",
        "transactions",
        "result",
        "results",
        "row",
        "rows",
        "record",
        "records",
        "file",
        "files",
        "entry",
        "entries",
        "category",
        "categories",
        "page",
        "pages",
        "match",
        "matches",
    }
)


class ClaimKind(StrEnum):
    MONEY = "money"
    NUMBER = "number"
    PERCENT = "percent"
    COUNT = "count"
    DATE = "date"


class Claim(BaseModel):
    text: str
    value: str
    kind: ClaimKind
    currency: str | None = None
    matched_fact_id: str | None = None
    exempt: bool = False
    exempt_reason: str | None = None


class VerifyResult(BaseModel):
    ok: bool
    claims: list[Claim] = []
    unmatched: list[str] = []
    regenerated: bool = False
    fell_back: bool = False

    def to_trace_dict(self, *, redact: bool = True) -> dict[str, Any]:
        claims: list[dict[str, object]] = []
        for claim in self.claims:
            value: object = claim.value
            if redact and claim.kind in (ClaimKind.MONEY, ClaimKind.PERCENT):
                value = "[REDACTED]"
            claims.append(
                {
                    "text": "[REDACTED]" if redact else claim.text,
                    "value": value,
                    "kind": claim.kind.value,
                    "matched_fact_id": claim.matched_fact_id,
                    "exempt": claim.exempt,
                }
            )
        return {
            "ok": self.ok,
            "claims": claims,
            "unmatched": ["[REDACTED]"] * len(self.unmatched) if redact else self.unmatched,
            "regenerated": self.regenerated,
            "fell_back": self.fell_back,
        }


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "").replace("−", "-"))
    except (InvalidOperation, ValueError):
        return None


def _round(value: Decimal, precision: int) -> Decimal:
    quantum = Decimal(1).scaleb(-precision)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _iso(year: str, month: str, day: str) -> str | None:
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return None


def _counts_noun_after(text: str, end: int) -> bool:
    match = re.match(r"\s*([a-zA-Z]+)", text[end:])
    return bool(match and match.group(1).lower() in _COUNT_NOUNS)


def _valid_clock(text: str) -> bool:
    match = re.match(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return False
    hour, minute = int(match.group(1)), int(match.group(2))
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _clock_value(text: str) -> str:
    match = re.match(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return ""
    return str(int(match.group(1)) * 100 + int(match.group(2)))


def _plausible_year(year: str) -> bool:
    return 1900 <= int(year) <= 2099


class _Extractor:
    def __init__(self, text: str) -> None:
        self.text = text
        self._consumed = [False] * len(text)
        self.claims: list[Claim] = []

    def _free(self, start: int, end: int) -> bool:
        return not any(self._consumed[start:end])

    def _consume(self, start: int, end: int) -> None:
        for i in range(start, end):
            self._consumed[i] = True

    def _add(self, start: int, end: int, claim: Claim) -> None:
        self._consume(start, end)
        self.claims.append(claim)

    def _exempt_span(self, start: int, end: int, reason: str, value: str = "") -> None:
        self._consume(start, end)
        self.claims.append(
            Claim(
                text=self.text[start:end],
                value=value,
                kind=ClaimKind.NUMBER,
                exempt=True,
                exempt_reason=reason,
            )
        )

    def run(self) -> list[Claim]:
        self._dates()
        self._exempts()
        self._money()
        self._percent()
        self._bare()
        self._spelled()
        return self.claims

    def _dates(self) -> None:
        for match in _ISO_DATE_RX.finditer(self.text):
            if self._free(*match.span()):
                iso = _iso(*match.groups())
                if iso:
                    self._add(
                        *match.span(), Claim(text=match.group(0), value=iso, kind=ClaimKind.DATE)
                    )
        for match in _MONTH_DAY_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            month_name, day, year = match.groups()
            iso = _iso(year, str(_MONTHS[month_name.lower()]), day)
            if iso:
                self._add(*match.span(), Claim(text=match.group(0), value=iso, kind=ClaimKind.DATE))
        for match in _DAY_MONTH_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            day, month_name, year = match.groups()
            iso = _iso(year, str(_MONTHS[month_name.lower()]), day)
            if iso:
                self._add(*match.span(), Claim(text=match.group(0), value=iso, kind=ClaimKind.DATE))

    def _exempts(self) -> None:
        # Only a plausible clock time (00:00-23:59) is structural; anything
        # else (``99:99``) falls through and becomes a numeric claim.
        for match in _CLOCK_RX.finditer(self.text):
            if self._free(*match.span()) and _valid_clock(match.group(0)):
                self._exempt_span(*match.span(), "clock_time", _clock_value(match.group(0)))
        # An ordinal that directly quantifies a count noun ("3rd account") is a
        # count claim, not a structural label.
        for match in _ORDINAL_DIGIT_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            digits = re.match(r"\d+", match.group(0))
            if digits and _counts_noun_after(self.text, match.end()):
                self._add(
                    *match.span(),
                    Claim(text=match.group(0), value=digits.group(0), kind=ClaimKind.COUNT),
                )
            else:
                self._exempt_span(*match.span(), "ordinal", digits.group(0) if digits else "")
        for match in _LIST_MARKER_RX.finditer(self.text):
            if self._free(*match.span()):
                self._exempt_span(*match.span(), "list_marker")
        # Only a plausible 4-digit year is structural; ``in 1234`` is a claim.
        for match in _YEAR_RX.finditer(self.text):
            if self._free(*match.span()) and _plausible_year(match.group(1)):
                self._exempt_span(*match.span(), "year_reference", str(int(match.group(1))))

    def _money(self) -> None:
        for match in _MONEY_SYMBOL_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            sign, symbol, raw = match.groups()
            value = _to_decimal(sign + raw)
            if value is None:
                continue
            self._add(
                *match.span(),
                Claim(
                    text=match.group(0),
                    value=format(value, "f"),
                    kind=ClaimKind.MONEY,
                    currency=_CURRENCY_SYMBOLS.get(symbol),
                ),
            )
        for match in _MONEY_WORD_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            sign, raw, word = match.groups()
            value = _to_decimal(sign + raw)
            if value is None:
                continue
            self._add(
                *match.span(),
                Claim(
                    text=match.group(0),
                    value=format(value, "f"),
                    kind=ClaimKind.MONEY,
                    currency=_CURRENCY_WORDS.get(word.lower()),
                ),
            )

    def _percent(self) -> None:
        for match in _PERCENT_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            sign, raw = match.groups()
            value = _to_decimal(sign + raw)
            if value is None:
                continue
            self._add(
                *match.span(),
                Claim(text=match.group(0), value=format(value, "f"), kind=ClaimKind.PERCENT),
            )

    def _bare(self) -> None:
        for match in _BARE_NUMBER_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            sign, raw = match.groups()
            value = _to_decimal(sign + raw)
            if value is None:
                continue
            kind = (
                ClaimKind.COUNT
                if "." not in raw and _counts_noun_after(self.text, match.end())
                else ClaimKind.NUMBER
            )
            self._add(
                *match.span(),
                Claim(text=match.group(0), value=format(value, "f"), kind=kind),
            )

    def _spelled(self) -> None:
        for match in _SPELLED_RX.finditer(self.text):
            if not self._free(*match.span()):
                continue
            value = words_to_number(match.group(0))
            if value is None:
                continue
            after = re.match(r"\s*([a-zA-Z]+)", self.text[match.end() :])
            unit = after.group(1).lower() if after else ""
            # The documented whitelist: a bare spelled "one" is structural.
            if value == 1:
                self._exempt_span(*match.span(), "spelled_quantity")
                continue
            if unit in _CURRENCY_WORDS:
                kind, currency = ClaimKind.MONEY, _CURRENCY_WORDS[unit]
            elif unit in _COUNT_NOUNS:
                kind, currency = ClaimKind.COUNT, None
            else:
                kind, currency = ClaimKind.NUMBER, None
            self._add(
                *match.span(),
                Claim(text=match.group(0), value=str(value), kind=kind, currency=currency),
            )


def extract_claims(text: str) -> list[Claim]:
    return _Extractor(text).run()


def extract_numbers(text: str) -> set[Decimal]:
    """Every numeric value mentioned in a user message (echo exemption)."""
    values: set[Decimal] = set()
    normalized = normalize_number_words(text)
    for claim in extract_claims(normalized):
        if claim.kind is ClaimKind.DATE or not claim.value:
            continue
        value = _to_decimal(claim.value)
        if value is not None:
            values.add(value)
    return values


def _kind_compatible(claim_kind: ClaimKind, fact_kind: FactKind) -> bool:
    if claim_kind is ClaimKind.NUMBER:
        return fact_kind in (FactKind.NUMBER, FactKind.COUNT)
    if claim_kind is ClaimKind.COUNT:
        return fact_kind in (FactKind.COUNT, FactKind.NUMBER)
    return claim_kind.value == fact_kind.value


def _claim_matches(claim: Claim, fact: Fact) -> bool:
    if not _kind_compatible(claim.kind, fact.kind):
        return False
    if claim.kind is ClaimKind.MONEY and fact.is_tainted:
        return False
    if (
        claim.kind is ClaimKind.MONEY
        and claim.currency
        and fact.currency
        and claim.currency != fact.currency
    ):
        return False
    if claim.kind is ClaimKind.DATE:
        return fact.canonical[:10] == claim.value[:10]
    claim_value = _to_decimal(claim.value)
    fact_value = fact.decimal_value()
    if claim_value is None or fact_value is None:
        return False
    return _round(claim_value, fact.display_precision) == _round(fact_value, fact.display_precision)


_VALUE_AWARE_EXEMPTIONS = frozenset({"year_reference", "clock_time", "ordinal"})


def verify_answer(
    draft: str,
    facts: list[Fact],
    *,
    user_text: str = "",
    regenerated: bool = False,
    fell_back: bool = False,
) -> VerifyResult:
    """Match every numeric claim in ``draft`` against the turn's facts."""
    user_numbers = extract_numbers(user_text) if user_text else set()
    fact_values = {value for fact in facts if (value := fact.decimal_value()) is not None}
    claims = extract_claims(draft)
    unmatched: list[str] = []
    for claim in claims:
        if claim.exempt:
            # A year/clock/ordinal that coincides with a tool value is not
            # structural: it must match as a normal claim or be rejected.
            if claim.exempt_reason in _VALUE_AWARE_EXEMPTIONS and claim.value:
                value = _to_decimal(claim.value)
                if value is not None and value in fact_values:
                    claim.exempt = False
                    claim.exempt_reason = None
            if claim.exempt:
                continue
        if claim.value and _to_decimal(claim.value) in user_numbers:
            claim.exempt = True
            claim.exempt_reason = "echoed_from_user"
            continue
        for fact in facts:
            if _claim_matches(claim, fact):
                claim.matched_fact_id = fact.fact_id
                break
        else:
            unmatched.append(claim.text)
    return VerifyResult(
        ok=not unmatched,
        claims=claims,
        unmatched=unmatched,
        regenerated=regenerated,
        fell_back=fell_back,
    )


class _SafeFormat(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def allowed_numbers_text(facts: list[Fact]) -> str:
    """The only values the model may state (used for the regenerate attempt)."""
    lines = [f"- {fact.display_text()} ({fact.path}, {fact.kind.value})" for fact in facts]
    if not lines:
        return "No tool values are available. You must not state any number."
    return "Use only these tool-provided values, exactly as written:\n" + "\n".join(lines)


def render_narration(template: str, args: dict[str, object], facts: list[Fact]) -> str:
    mapping: dict[str, str] = {k: str(v) for k, v in args.items()}
    mapping.update({fact.path: fact.display_text() for fact in facts})
    return template.format_map(_SafeFormat(mapping))


__all__ = [
    "Claim",
    "ClaimKind",
    "VerifyResult",
    "allowed_numbers_text",
    "extract_claims",
    "extract_numbers",
    "render_narration",
    "verify_answer",
]
