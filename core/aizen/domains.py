"""Domain vocabulary shared by the router, tools and the agent loop."""

from __future__ import annotations

from enum import StrEnum


class Domain(StrEnum):
    FINANCE = "finance"
    PERSONAL = "personal"
    WEB = "web"
    CHAT = "chat"
    SYSTEM = "system"
    AMBIGUOUS = "ambiguous"


# The toolset a routed domain is allowed to see. The pre-router narrows the
# model's world to these 0-5 tools per turn; adding a tool to a domain here
# exposes it automatically (see `aizen-architecture-v2.md` section 8).
DOMAIN_TOOLSETS: dict[Domain, tuple[str, ...]] = {
    Domain.FINANCE: (
        "finance.describe",
        "finance.current_balances",
        "finance.spend_by_period",
        "finance.affordability",
        "system.calculate",
    ),
    Domain.PERSONAL: ("knowledge.search", "files.read", "memory.recall"),
    Domain.WEB: ("web.search", "web.fetch"),
    Domain.CHAT: (),
    Domain.SYSTEM: ("system.get_current_time",),
    Domain.AMBIGUOUS: (),
}
