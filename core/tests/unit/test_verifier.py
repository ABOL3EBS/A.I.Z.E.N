"""Answer verifier (§31): golden cases and the false-positive corpus.

Every number in a finance/knowledge answer must match a tool fact this turn.
These tests pin the matching rules (rounding, kinds, taint) and guard against
rejecting realistic *correct* answers.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from aizen.facts import Fact, count, date_fact, money, number, percent, text_fact
from aizen.origins import Origin
from aizen.verifier import (
    ClaimKind,
    allowed_numbers_text,
    extract_claims,
    render_narration,
    verify_answer,
)


def _money(value: str, *, tainted: bool = False) -> Fact:
    fact = money(Decimal(value), precision=2, path="total")
    return fact.model_copy(update={"tainted": tainted})


_GOLDEN: list[tuple[str, list[Fact]]] = [
    ("You spent $1,250.5 in total.", [_money("1250.50")]),
    ("The result is 3,000.", [number(Decimal("3000"), path="result")]),
    ("It costs $50.", [_money("50")]),
    ("The delta is -5.", [number(Decimal("-5"))]),
    ("The rate is 8%.", [percent(Decimal("8"))]),
    ("It happened on 2026-08-01.", [date_fact("2026-08-01")]),
    ("It happened on August 1, 2026.", [date_fact("2026-08-01")]),
    ("The meeting is on 1 August 2026.", [date_fact("2026-08-01")]),
    ("You have three accounts.", [count(3, path="accounts")]),
    (
        "There are 3 results.",
        [number(3, path="results").model_copy(update={"origin": Origin.WEB})],
    ),
]


@pytest.mark.parametrize(("draft", "facts"), _GOLDEN)
def test_golden_correct_answers_pass(draft: str, facts: list[Fact]) -> None:
    assert verify_answer(draft, facts).ok is True


def _mutate_first_digit_of_a_matched_claim(draft: str, facts: list[Fact]) -> str | None:
    for claim in verify_answer(draft, facts).claims:
        if claim.exempt or claim.matched_fact_id is None:
            continue
        digit = re.search(r"\d", claim.text)
        if digit is None:
            continue
        start = draft.index(claim.text) + digit.start()
        replacement = "0" if draft[start] != "0" else "1"
        return draft[:start] + replacement + draft[start + 1 :]
    return None


def test_mutating_one_digit_of_every_golden_case_is_rejected() -> None:
    checked = 0
    for draft, facts in _GOLDEN:
        mutated = _mutate_first_digit_of_a_matched_claim(draft, facts)
        if mutated is None:
            continue
        assert verify_answer(mutated, facts).ok is False, (draft, mutated)
        checked += 1
    assert checked >= 5


def test_mutating_a_spelled_golden_case_is_rejected() -> None:
    assert verify_answer("You have four accounts.", [count(3, path="accounts")]).ok is False


def test_derived_number_fails_without_a_fact() -> None:
    result = verify_answer("The total is 42.", [])
    assert result.ok is False
    assert result.unmatched == ["42"]


def test_derived_number_fails_even_with_an_unrelated_fact() -> None:
    result = verify_answer("The total is 42.", [number(Decimal("7"))])
    assert result.ok is False
    assert result.unmatched == ["42"]


def test_tainted_fact_cannot_satisfy_a_money_claim() -> None:
    result = verify_answer("It costs $100.", [_money("100", tainted=True)])
    assert result.ok is False


def test_kind_mismatch_fails() -> None:
    # A percent claim cannot be satisfied by a money fact, even at equal value.
    result = verify_answer("The rate is 8%.", [_money("8")])
    assert result.ok is False


def test_number_echoed_from_user_is_exempt() -> None:
    result = verify_answer("You asked about 500.", [], user_text="What about 500?")
    assert result.ok is True
    echoed = [c for c in result.claims if c.exempt_reason == "echoed_from_user"]
    assert echoed and echoed[0].value == "500"


def test_structural_numbers_are_exempt() -> None:
    draft = "1. First item\n2. Second item\nin 2026 at 3:00 PM"
    result = verify_answer(draft, [])
    assert result.ok is True


def test_claim_kinds_are_classified() -> None:
    kinds = {c.text: c.kind for c in extract_claims("$5 and 8% on 2026-08-01 and 3 accounts")}
    assert kinds["$5"] is ClaimKind.MONEY
    assert kinds["8%"] is ClaimKind.PERCENT
    assert kinds["2026-08-01"] is ClaimKind.DATE
    assert kinds["3"] is ClaimKind.COUNT


def test_render_narration_uses_args_and_facts() -> None:
    text = render_narration(
        "The result of {expression} is {result}.",
        {"expression": "6 * 7"},
        [number(Decimal("42"), path="result")],
    )
    assert text == "The result of 6 * 7 is 42."


def test_allowed_numbers_text_lists_fact_values() -> None:
    text = allowed_numbers_text([number(Decimal("42"), path="result")])
    assert "42" in text and "result" in text


def test_trace_dict_redacts_money_values() -> None:
    result = verify_answer("You spent $1,250.50.", [_money("1250.50")])
    redacted = result.to_trace_dict(redact=True)
    assert redacted["claims"][0]["value"] == "[REDACTED]"
    assert redacted["claims"][0]["text"] == "[REDACTED]"
    plain = result.to_trace_dict(redact=False)
    assert plain["claims"][0]["value"] == "1250.50"


# --- false-positive corpus -------------------------------------------------

_CORPUS: list[tuple[str, list[Fact], str]] = [
    (
        "The result of 20 * 150 is 3,000.",
        [number(Decimal("3000"), path="result")],
        "What is 20 * 150?",
    ),
    ("You spent $1,250.50.", [_money("1250.50")], ""),
    ("Your balance is $0.00.", [_money("0")], ""),
    ("That's an 8.5% increase.", [percent(Decimal("8.5"))], ""),
    ("It happened on 2026-08-01.", [date_fact("2026-08-01")], ""),
    ("It happened on August 1, 2026.", [date_fact("2026-08-01")], ""),
    ("The meeting is on 1 August 2026.", [date_fact("2026-08-01")], ""),
    ("You have three accounts.", [count(3, path="accounts")], ""),
    ("There are 3 accounts.", [count(3, path="accounts")], ""),
    ("I found 12 transactions.", [count(12, path="transactions")], ""),
    ("The temperature is -5 degrees.", [number(Decimal("-5"))], ""),
    ("The total is 0.", [number(Decimal("0"))], ""),
    ("That's 100% correct.", [percent(Decimal("100"))], ""),
    (
        "It is 2026-09-21T14:30 UTC.",
        [date_fact(datetime(2026, 9, 21, 14, 30, tzinfo=UTC))],
        "",
    ),
    ("You have 5 unread items.", [count(5, path="items")], ""),
    ("The exchange rate is 1.08.", [number(Decimal("1.08"))], ""),
    ("Your savings grew by 250.75.", [number(Decimal("250.75"))], ""),
    ("The invoice total is $42.00.", [_money("42")], ""),
    ("1. First point\n2. Second point\n3. Third point", [], ""),
    ("In 2025 we had 3 audits.", [count(3, path="audits")], ""),
    ("It's 3:00 PM.", [], ""),
    ("The year 2026 looks fine.", [], ""),
    ("You asked about 500, and I found $500.00.", [_money("500")], "What about 500?"),
    ("There are no results.", [], ""),
    ("Your portfolio is up 12% to $5,000.00.", [percent(Decimal("12")), _money("5000")], ""),
    ("The value is 1,234.56.", [number(Decimal("1234.56"))], ""),
    ("It weighs 2.5 kg.", [number(Decimal("2.5"))], ""),
    ("You have 1 account.", [count(1, path="accounts")], ""),
    ("The first transaction was $10.00.", [_money("10")], ""),
    ("I looked in 2020 and in 2025.", [], ""),
    (
        "There are 3 results.",
        [number(3, path="results").model_copy(update={"origin": Origin.WEB})],
        "",
    ),
    ("Here is a summary with no figures at all.", [text_fact("x")], ""),
]


def test_false_positive_corpus() -> None:
    rejected = [
        draft
        for draft, facts, user_text in _CORPUS
        if not verify_answer(draft, facts, user_text=user_text).ok
    ]
    print(f"false-positive corpus: {len(rejected)}/{len(_CORPUS)} rejected")
    assert rejected == []
