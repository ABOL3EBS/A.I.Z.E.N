"""Typed facts (§31): construction, canonical form and the per-turn table."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aizen.facts import (
    Fact,
    FactKind,
    FactTable,
    count,
    date_fact,
    money,
    number,
    percent,
    text_fact,
)


def test_money_uses_decimal_canonical_and_precision() -> None:
    fact = money(Decimal("1250.5"), currency="USD", precision=2)
    assert fact.kind is FactKind.MONEY
    assert fact.canonical == "1250.5"
    assert fact.decimal_value() == Decimal("1250.5")
    assert fact.currency == "USD"
    assert fact.display_precision == 2


def test_number_precision_is_inferred_from_decimals() -> None:
    assert number(Decimal("200.00")).display_precision == 2
    assert number(Decimal("3000")).display_precision == 0


def test_count_and_percent() -> None:
    assert count(3).canonical == "3"
    assert count(3).kind is FactKind.COUNT
    assert percent(Decimal("8.5")).decimal_value() == Decimal("8.5")


def test_date_fact_is_iso_8601() -> None:
    fact = date_fact(datetime(2026, 8, 1, 12, 30, tzinfo=UTC))
    assert fact.kind is FactKind.DATE
    assert fact.canonical.startswith("2026-08-01T12:30")


def test_text_fact_has_no_numeric_value() -> None:
    fact = text_fact("Example")
    assert fact.kind is FactKind.TEXT
    assert fact.decimal_value() is None


def test_fact_table_assigns_ids_sources_and_taint() -> None:
    table = FactTable()
    first = table.register("system.calculate", number(3000))
    second = table.register("web.fetch", text_fact("hi"), tainted=True)
    assert first.fact_id == "f1"
    assert first.source_ref == "system.calculate"
    assert second.fact_id == "f2"
    assert second.tainted is True
    assert table.by_id("f2") is second
    assert len(table.numeric_facts()) == 1
    assert len(table) == 2


def test_register_all_keeps_order() -> None:
    table = FactTable()
    registered = table.register_all("t", [number(1), number(2)])
    assert [f.fact_id for f in registered] == ["f1", "f2"]


def test_fact_taint_follows_origin() -> None:
    from aizen.origins import Origin

    fact = Fact(path="x", kind=FactKind.NUMBER, canonical="1", origin=Origin.WEB)
    assert fact.is_tainted is True
