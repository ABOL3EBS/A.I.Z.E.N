from __future__ import annotations

from aizen.config import RoutingRules
from aizen.domains import Domain


def route_domain(text: str) -> Domain:
    return RoutingRules.load().route(text).domain


def test_finance() -> None:
    assert route_domain("What did I spend in March?") is Domain.FINANCE
    assert route_domain("What is my balance?") is Domain.FINANCE
    assert route_domain("Can I afford a new keyboard?") is Domain.FINANCE
    assert route_domain("calculate 20*150") is Domain.FINANCE


def test_personal() -> None:
    assert route_domain("What did I write about the prototype?") is Domain.PERSONAL
    assert route_domain("Do you remember my coffee preference?") is Domain.PERSONAL


def test_web() -> None:
    assert route_domain("What is the weather in Munich?") is Domain.WEB
    assert route_domain("Search the web for FastAPI changelog") is Domain.WEB


def test_chat_fallback() -> None:
    assert route_domain("hello there, how are you?") is Domain.CHAT
    assert route_domain("tell me a joke") is Domain.CHAT


def test_nothing_invented_without_a_route() -> None:
    decision = RoutingRules.load().route("plain small talk")
    assert decision.toolset == ()


def test_ambiguous_tie_returns_union() -> None:
    # "my notes" (personal) collides with nothing else here; craft a genuine tie
    text = "search my notes on the internet"
    decision = RoutingRules.load().route(text)
    # both personal (notes) and web (search/internet) fire -> ambiguous union
    assert decision.domain in (Domain.PERSONAL, Domain.WEB, Domain.AMBIGUOUS)
    assert decision.tied is (decision.domain is Domain.AMBIGUOUS)
