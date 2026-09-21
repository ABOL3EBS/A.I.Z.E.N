"""Pre-router facade: utterance -> RouteDecision (pure rule logic in config)."""

from __future__ import annotations

from aizen.config import RouteDecision, RoutingRules
from aizen.domains import Domain


class RuleRouter:
    """Narrows the visible toolset per turn; chat when nothing matches."""

    def __init__(self, rules: RoutingRules | None = None) -> None:
        self._rules = rules or RoutingRules.load()

    @classmethod
    def load(cls) -> RuleRouter:
        return cls(RoutingRules.load())

    def route(self, text: str) -> RouteDecision:
        return self._rules.route(text)

    @property
    def domains(self) -> set[Domain]:
        return {r.domain for r in self._rules.rules}
