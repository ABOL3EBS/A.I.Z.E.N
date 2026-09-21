"""Typed facts (§31): every number an answer may contain must come from here.

The model never does arithmetic, date math or path resolution; tools return
typed facts and the deterministic verifier only accepts numbers that match a
fact registered this turn. Money is ``Decimal`` and dates are ISO-8601; nothing
here depends on the LLM.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from aizen.origins import Origin, origin_is_tainted


class FactKind(StrEnum):
    MONEY = "money"
    NUMBER = "number"
    DATE = "date"
    PERCENT = "percent"
    COUNT = "count"
    TEXT = "text"


def _decimal(value: Decimal | str | int | float) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _canonical(value: Decimal) -> str:
    """Plain (non-exponent) decimal string, e.g. ``200.00`` not ``2E+2``."""
    return format(value, "f")


def _precision(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


class Fact(BaseModel):
    """One deterministic value a tool returned this turn.

    :attr canonical: full-precision canonical form used for matching.
    :attr display: optional human-facing rendering; falls back to ``canonical``.
    :attr display_precision: decimals the value is presented with; equality is
        checked after rounding both sides to this precision.
    """

    model_config = ConfigDict(frozen=False)

    fact_id: str = ""
    path: str
    kind: FactKind = FactKind.TEXT
    canonical: str
    display: str | None = None
    display_precision: int = 0
    currency: str | None = None
    source_ref: str | None = None
    origin: Origin = Origin.SYSTEM
    tainted: bool = False

    @property
    def is_tainted(self) -> bool:
        return self.tainted or origin_is_tainted(self.origin)

    def decimal_value(self) -> Decimal | None:
        if self.kind not in (FactKind.MONEY, FactKind.NUMBER, FactKind.PERCENT, FactKind.COUNT):
            return None
        try:
            return Decimal(self.canonical)
        except (InvalidOperation, ValueError):
            return None

    def display_text(self) -> str:
        return self.display or self.canonical


class FactTable:
    """Per-turn fact registry; assigns stable ids (``f1``, ``f2``, ...)."""

    def __init__(self) -> None:
        self._facts: list[Fact] = []

    def register(self, tool: str, fact: Fact, *, tainted: bool = False) -> Fact:
        assigned = fact.model_copy(
            update={
                "fact_id": f"f{len(self._facts) + 1}",
                "source_ref": fact.source_ref or tool,
                "tainted": fact.tainted or tainted,
            }
        )
        self._facts.append(assigned)
        return assigned

    def register_all(self, tool: str, facts: list[Fact], *, tainted: bool = False) -> list[Fact]:
        return [self.register(tool, fact, tainted=tainted) for fact in facts]

    @property
    def facts(self) -> list[Fact]:
        return list(self._facts)

    def by_id(self, fact_id: str) -> Fact | None:
        return next((f for f in self._facts if f.fact_id == fact_id), None)

    def numeric_facts(self) -> list[Fact]:
        return [f for f in self._facts if f.decimal_value() is not None]

    def __len__(self) -> int:
        return len(self._facts)


def money(
    value: Decimal | str | int | float,
    *,
    currency: str = "USD",
    precision: int = 2,
    path: str = "amount",
    display: str | None = None,
) -> Fact:
    dec = _decimal(value)
    return Fact(
        path=path,
        kind=FactKind.MONEY,
        canonical=_canonical(dec),
        display=display,
        display_precision=precision,
        currency=currency,
    )


def number(
    value: Decimal | str | int | float,
    *,
    precision: int | None = None,
    path: str = "value",
    display: str | None = None,
) -> Fact:
    dec = _decimal(value)
    return Fact(
        path=path,
        kind=FactKind.NUMBER,
        canonical=_canonical(dec),
        display=display,
        display_precision=_precision(dec) if precision is None else precision,
    )


def count(value: int, *, path: str = "count", display: str | None = None) -> Fact:
    return Fact(
        path=path,
        kind=FactKind.COUNT,
        canonical=str(int(value)),
        display=display,
        display_precision=0,
    )


def percent(
    value: Decimal | str | int | float,
    *,
    precision: int | None = None,
    path: str = "percent",
    display: str | None = None,
) -> Fact:
    dec = _decimal(value)
    return Fact(
        path=path,
        kind=FactKind.PERCENT,
        canonical=_canonical(dec),
        display=display,
        display_precision=_precision(dec) if precision is None else precision,
    )


def date_fact(
    value: datetime | date | str,
    *,
    path: str = "date",
    display: str | None = None,
) -> Fact:
    canonical = value.isoformat() if isinstance(value, (datetime, date)) else value
    return Fact(
        path=path,
        kind=FactKind.DATE,
        canonical=canonical,
        display=display,
        display_precision=0,
    )


def text_fact(value: str, *, path: str = "text", display: str | None = None) -> Fact:
    return Fact(path=path, kind=FactKind.TEXT, canonical=value, display=display)


__all__ = [
    "Fact",
    "FactKind",
    "FactTable",
    "count",
    "date_fact",
    "money",
    "number",
    "percent",
    "text_fact",
]
