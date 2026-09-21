"""Model profiles: which Ollama model/params each tier maps to.

Profiles are loaded from `model_profiles.yaml`; changing tiers is config, not
code (addressing architecture section 4.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel

CONFIG_DIR = Path(__file__).parent


class ModelProfile(BaseModel):
    name: str
    class_label: str = ""
    approx_ram_gb: float = 0.0
    use: str = ""
    model: str
    num_ctx: int = 8192
    keep_alive: str = "30m"
    temperature: float = 0.1
    embed_model: str = "nomic-embed-text"
    base_url: str | None = None


class ProfileSet(BaseModel):
    profiles: dict[str, ModelProfile]
    default: str = "light"

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        path = path or CONFIG_DIR / "model_profiles.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            default=raw.get("default", "light"),
            profiles={
                name: ModelProfile(name=name, **data)
                for name, data in (raw.get("profiles") or {}).items()
            },
        )

    def get(self, name: str | None = None) -> ModelProfile:
        key = name or self.default
        if key not in self.profiles:
            raise KeyError(f"Unknown model profile {key!r}; choose from {sorted(self.profiles)}")
        return self.profiles[key]
