"""Adversarial verifier suite (§31): every wrong draft must be rejected.

Each case is a draft paired with the turn's facts. The draft states at least one
value that no tool returned — including values smuggled through an "exempt"
form (year, clock time, ordinal, spelled quantity, list marker), a currency
mismatch, a derived arithmetic result, or a tainted fact. Any ``ok is True``
here is a false negative and a hole in the "no invented numbers" guarantee.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from aizen.facts import Fact, count, date_fact, money, number, percent
from aizen.origins import Origin
from aizen.verifier import verify_answer


def _money(value: str, *, tainted: bool = False, origin: Origin = Origin.SYSTEM) -> Fact:
    return money(Decimal(value), precision=2, path="total").model_copy(
        update={"tainted": tainted, "origin": origin}
    )


def _count(value: int) -> Fact:
    return count(value, path="count")


_ADVERSARIAL: list[tuple[str, list[Fact]]] = [
    # --- altered / swapped / rounded money values -------------------------
    ("You spent $1,250.60.", [_money("1250.50")]),
    ("You spent $1,251.50.", [_money("1250.50")]),
    ("You spent $1,502.50.", [_money("1250.50")]),
    ("You spent $1,250.50.", [_money("1250.05")]),
    ("You spent $1,250.5.", [_money("1250.60")]),
    ("The total is $1,000.00.", [_money("999.99")]),
    ("Your balance is $99,999.99.", [_money("100000")]),
    ("The balance is $0.01.", [_money("0")]),
    # --- wrong sign -------------------------------------------------------
    ("You received -$50.00.", [_money("50")]),
    ("You received $50.00.", [_money("-50")]),
    ("The delta is -5.", [number(Decimal("5"))]),
    ("The delta is 5.", [number(Decimal("-5"))]),
    # --- wrong currency ---------------------------------------------------
    ("It costs €50.", [_money("50")]),
    ("It costs £50.", [_money("50")]),
    ("It costs 50 dollars.", [_money("60")]),
    ("It costs fifty dollars.", [_money("60")]),
    # --- wrong plain numbers / counts ------------------------------------
    ("The total is 3,100.", [number(Decimal("3000"), path="result")]),
    ("The result is 2999.", [number(Decimal("3000"), path="result")]),
    ("The exchange rate is 1.09.", [number(Decimal("1.08"))]),
    ("You have 3 unread items.", [count(5, path="items")]),
    ("There are 4 results.", [number(Decimal("3"), path="results")]),
    ("You have 3 accounts.", [_count(4)]),
    ("I found 12 transactions.", [count(13, path="transactions")]),
    ("You spent 1,250.51.", [_money("1250.50")]),
    # --- wrong percentages ------------------------------------------------
    ("It is 9%.", [percent(Decimal("8"))]),
    ("It is 8.5%.", [percent(Decimal("8"))]),
    ("That's a 100% increase.", [percent(Decimal("50"))]),
    ("It is 0%.", [percent(Decimal("100"))]),
    # --- wrong dates ------------------------------------------------------
    ("It happened on 2026-08-02.", [date_fact("2026-08-01")]),
    ("It happened on July 1, 2026.", [date_fact("2026-08-01")]),
    ("The meeting is on 1 September 2026.", [date_fact("2026-08-01")]),
    ("It happened on 2026-13-01.", [date_fact("2026-08-01")]),
    # --- derived arithmetic (no fact returned it) -------------------------
    ("The sum is 30.", [number(Decimal("10")), number(Decimal("20"))]),
    ("Your total is $30.00.", [_money("10"), _money("20")]),
    ("The difference is 30.", [number(Decimal("20")), number(Decimal("10"))]),
    ("That is a 50% increase.", [percent(Decimal("25")), percent(Decimal("25"))]),
    ("The projection is 1200.", [number(Decimal("1000")), percent(Decimal("20"))]),
    # --- tainted sources cannot satisfy finance claims --------------------
    ("It costs $100.00.", [_money("100", tainted=True)]),
    ("Your balance is $100.00.", [_money("100", origin=Origin.WEB)]),
    # --- wrong values hidden inside exemption categories ------------------
    ("In 1234 you had it.", [date_fact("2026-08-01")]),
    ("The total in 1999.", [_money("1999")]),
    ("The total is 99:99.", [_money("9999")]),
    ("The total is 3:00.", [_money("300")]),
    ("You have 3rd accounts.", [_count(5)]),
    ("Your total is the 30th.", [_money("30")]),
    ("You have three months.", [_count(5)]),
    ("You have thirty dollars.", [_money("40")]),
    ("1. You spent $600.00.", [_money("500")]),
    ("2. Your balance is $0.00.", [_money("250")]),
]


def test_adversarial_corpus_is_large_enough() -> None:
    assert len(_ADVERSARIAL) >= 40


@pytest.mark.parametrize(("draft", "facts"), _ADVERSARIAL)
def test_wrong_drafts_are_rejected(draft: str, facts: list[Fact]) -> None:
    result = verify_answer(draft, facts)
    assert result.ok is False, f"accepted a wrong draft: {draft!r}"
    assert result.unmatched


def test_false_negative_count_is_reported() -> None:
    accepted = [draft for draft, facts in _ADVERSARIAL if verify_answer(draft, facts).ok]
    print(f"adversarial false negatives: {len(accepted)}/{len(_ADVERSARIAL)}")
    assert accepted == []
