"""Rule-based pre-router: utterance -> domain decision (no LLM involved)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, PrivateAttr, model_validator

from aizen.domains import DOMAIN_TOOLSETS, Domain

CONFIG_DIR = Path(__file__).parent


class RoutingRule(BaseModel):
    domain: Domain
    patterns: list[str] = []
    examples: list[str] = []

    _compiled: list[re.Pattern[str]] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _compile(self) -> Self:
        self._compiled = [re.compile(p, re.IGNORECASE) for p in self.patterns]
        return self

    def score(self, text: str) -> int:
        """Number of patterns that matched; 0 when the rule didn't fire."""
        return sum(1 for p in self._compiled if p.search(text))


class RouteDecision(BaseModel):
    domain: Domain
    toolset: tuple[str, ...]
    rule_hits: list[str] = []
    tied: bool = False


class RoutingRules(BaseModel):
    rules: list[RoutingRule]

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        path = path or CONFIG_DIR / "routing_rules.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(rules=[RoutingRule(**r) for r in raw.get("rules", [])])

    def route(self, text: str) -> RouteDecision:
        scored: dict[Domain, int] = {}
        hits: dict[Domain, list[str]] = {}
        for rule in self.rules:
            score = rule.score(text)
            if score:
                scored[rule.domain] = scored.get(rule.domain, 0) + score
                hits.setdefault(rule.domain, [])
                if rule.domain not in hits:
                    hits[rule.domain] = [p for p in rule.patterns]
                hits[rule.domain].extend([p for p in rule.patterns if re.search(p, text, re.I)])
        if not scored:
            return RouteDecision(domain=Domain.CHAT, toolset=DOMAIN_TOOLSETS[Domain.CHAT])
        top_score = max(scored.values())
        winners = [d for d, s in scored.items() if s == top_score]
        if len(winners) > 1:
            toolset = tuple(dict.fromkeys(t for w in winners for t in DOMAIN_TOOLSETS[w]))
            return RouteDecision(
                domain=Domain.AMBIGUOUS,
                toolset=toolset,
                rule_hits=[w.value for w in winners],
                tied=True,
            )
        domain = winners[0]
        return RouteDecision(
            domain=domain,
            toolset=DOMAIN_TOOLSETS[domain],
            rule_hits=hits.get(domain, []),
        )
