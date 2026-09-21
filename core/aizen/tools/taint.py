"""Taint model (architecture Part II §29): origins, wrappers, per-turn state.

Origins are assigned by code (tool implementations / services), never by the
model. ``WEB`` and ``LOCAL_CONTENT`` are tainted; everything else is clean.
Taint is sticky within a turn and is derived from what actually entered the
model's context window.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from aizen.origins import TAINTED_ORIGINS, Origin, origin_is_tainted

__all__ = [
    "TAINTED_ORIGINS",
    "Origin",
    "TurnTaint",
    "new_nonce",
    "origin_is_tainted",
    "summary_taint",
    "taint_from_messages",
    "wrap_untrusted",
]

# Per-origin label used in the `<untrusted ... origin="...">` wrapper.
_UNTRUSTED_LABEL: dict[Origin, str] = {
    Origin.WEB: "web",
    Origin.LOCAL_CONTENT: "file",
}

# Matches both open and close tags, case-insensitively, with optional
# whitespace after ``<`` and ``/`` (``</Untrusted >``, ``< untrusted>``, ...).
_UNTRUSTED_TAG_RX = re.compile(r"<\s*/?\s*untrusted\b", re.IGNORECASE)


def new_nonce() -> str:
    """Fresh random nonce per turn so injected content cannot forge the close tag."""
    return secrets.token_hex(8)


def wrap_untrusted(text: str, origin: Origin, nonce: str) -> str:
    """Wrap untrusted content so the model treats it as data, never instructions.

    Every ``untrusted`` open/close tag inside the payload — in any case and with
    any inner whitespace — is escaped so forged tags cannot break out of the
    wrapper. Only the single wrapper's own closing tag survives unescaped.
    """
    label = _UNTRUSTED_LABEL.get(origin, "file")
    escaped = _UNTRUSTED_TAG_RX.sub(lambda m: m.group(0).replace("<", "<\\"), text)
    return f'<untrusted id="{nonce}" origin="{label}">{escaped}</untrusted id="{nonce}">'


class TurnTaint:
    """Sticky per-turn taint record derived from context contents.

    :attr origins: every origin that has entered the context this turn.
    :attr sources: human/machine readable references (e.g. "web:example.com").
    """

    def __init__(self, *, initial: Origin = Origin.USER) -> None:
        self._origins: set[Origin] = {initial}
        self.sources: list[str] = []

    def mark(self, origin: Origin, source: str | None = None) -> None:
        self._origins.add(origin)
        if source and source not in self.sources:
            self.sources.append(source)

    @property
    def origins(self) -> frozenset[Origin]:
        return frozenset(self._origins)

    @property
    def is_tainted(self) -> bool:
        return any(origin_is_tainted(o) for o in self._origins)

    @property
    def level(self) -> str:
        return "TAINTED" if self.is_tainted else "CLEAN"

    def absorb(self, other: TurnTaint) -> None:
        """Merge another taint record (e.g. taint still in the context window)."""
        self._origins |= other._origins
        for source in other.sources:
            if source not in self.sources:
                self.sources.append(source)


def _coerce_origin(value: Any) -> Origin | None:
    try:
        return Origin(value)
    except ValueError:
        return None


def taint_from_messages(messages: list[Any]) -> TurnTaint:
    """Derive taint from what is actually in the model's context (§29).

    Applies across turns: recent tainted messages and summaries derived from
    them carry ``tainted=True``/a tainted ``origin``, so the next turn starts
    tainted even before it produces any new tool result.
    """
    taint = TurnTaint(initial=Origin.SYSTEM)
    for msg in messages:
        origin = _coerce_origin(getattr(msg, "origin", None))
        flagged = bool(getattr(msg, "tainted", False))
        if origin is None and not flagged:
            continue
        if origin is None:
            origin = Origin.LOCAL_CONTENT
        if flagged or origin_is_tainted(origin):
            taint.mark(origin, f"{origin.value.lower()}:context")
        else:
            taint.mark(origin)
    return taint


def summary_taint(source: TurnTaint) -> TurnTaint:
    """A summary derived from tainted content is itself tainted (§29)."""
    derived = TurnTaint(initial=Origin.SYSTEM)
    if source.is_tainted:
        derived.mark(Origin.LOCAL_CONTENT, "summary:tainted")
    return derived
