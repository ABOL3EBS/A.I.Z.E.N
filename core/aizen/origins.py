"""Origins and the taint predicate — a leaf module with no internal imports.

Kept separate from ``aizen.tools.taint`` so that leaf modules (e.g.
``aizen.facts``) can reference origins without triggering the ``aizen.tools``
package initializer (which would create an import cycle).
"""

from __future__ import annotations

from enum import StrEnum


class Origin(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    MEMORY_EXPLICIT = "MEMORY_EXPLICIT"
    FINANCE_FACT = "FINANCE_FACT"
    LOCAL_CONTENT = "LOCAL_CONTENT"
    WEB = "WEB"


TAINTED_ORIGINS: frozenset[Origin] = frozenset({Origin.WEB, Origin.LOCAL_CONTENT})


def origin_is_tainted(origin: Origin) -> bool:
    return origin in TAINTED_ORIGINS


__all__ = ["Origin", "TAINTED_ORIGINS", "origin_is_tainted"]
