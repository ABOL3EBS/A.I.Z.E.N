"""Selection of a ModelProfile given settings; provider wiring helpers."""

from __future__ import annotations

from aizen.config import ProfileSet, Settings
from aizen.config.model_profiles import ModelProfile


def select_profile(settings: Settings, profiles: ProfileSet) -> ModelProfile:
    return profiles.get(settings.default_profile)


def provider_base_url(profile: ModelProfile, settings: Settings) -> str:
    """Remote profiles carry their own endpoint; local ones use Ollama."""
    return profile.base_url or settings.ollama_base_url
