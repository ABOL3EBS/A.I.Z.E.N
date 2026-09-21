from __future__ import annotations

from decimal import Decimal

import pytest
from aizen.tools.builtin.system import evaluate_decimal


def test_basic_arithmetic() -> None:
    assert evaluate_decimal("2 + 2") == Decimal("4")
    assert evaluate_decimal("(3 + 2) * 40 / 4") == Decimal("50")
    assert evaluate_decimal("7 % 3") == Decimal("1")
    assert evaluate_decimal("-5 + 3") == Decimal("-2")


def test_decimal_precision() -> None:
    assert evaluate_decimal("0.1 + 0.2") == Decimal("0.3")


def test_left_associativity() -> None:
    assert evaluate_decimal("100 / 10 / 2") == Decimal("5")


@pytest.mark.parametrize("expr", ["2 ** 3", "1 / 0", "max(1,2)", "os.exit()", "2..3", ""])
def test_rejected_expressions(expr: str) -> None:
    with pytest.raises(ValueError):
        evaluate_decimal(expr)


def test_out_of_range_rejected() -> None:
    with pytest.raises(ValueError):
        evaluate_decimal("1e30 * 1e30")
