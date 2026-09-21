"""Domain-scoped builtin tools."""

from __future__ import annotations

from aizen.tools.base import Tool
from aizen.tools.builtin.system import calculate, get_current_time

BUILTIN_TOOLS: list[Tool] = [get_current_time, calculate]

__all__ = ["BUILTIN_TOOLS"]
