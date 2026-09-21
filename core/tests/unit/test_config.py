from __future__ import annotations

from aizen.config import ProfileSet, RoutingRules
from aizen.config.model_profiles import ModelProfile
from aizen.config.settings import Settings

EXPECTED_PROFILES = {"tiny", "light", "standard", "remote"}


def test_profiles_load_defaults() -> None:
    profiles = ProfileSet.load()
    assert set(profiles.profiles) == EXPECTED_PROFILES
    assert profiles.default == "light"
    assert isinstance(profiles.get("light"), ModelProfile)
    assert profiles.get("light").num_ctx >= 4096


def test_unknown_profile_raises() -> None:
    profiles = ProfileSet.load()
    try:
        profiles.get("missing-profile")
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown profile")


def test_settings_defaults() -> None:
    settings = Settings()
    assert settings.api_host == "127.0.0.1"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.default_profile == "light"
    assert settings.max_model_calls_per_turn == 4
    assert str(settings.db_path).endswith("aizen.db")


def test_routing_rules_load() -> None:
    rules = RoutingRules.load()
    domains = {r.domain.value for r in rules.rules}
    assert domains == {"finance", "personal", "web"}
    assert rules.rules[0].patterns
    assert rules.rules[0].examples
