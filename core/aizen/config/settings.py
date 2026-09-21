"""Application settings and Yaml-loaded config (profiles, routing rules).

Everything is plain data, overridable via AIZEN_* environment variables or a
`.env` file, so profiles and routing rules are config, not code.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    base = os.environ.get("AIZEN_DATA_DIR")
    if base:
        return Path(base).expanduser()
    return Path.home() / "Library" / "Application Support" / "AIZEN"


class Settings(BaseSettings):
    """Process-wide settings for the A.I.Z.E.N. core (Python sidecar)."""

    model_config = SettingsConfigDict(
        env_prefix="AIZEN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_host: str = "127.0.0.1"
    api_port: int = 8787
    # Random per-launch bearer token; the Tauri shell injects it into the
    # webview. Never a fixed default in production.
    api_token: str | None = None
    # §6.2: validate Origin; browsers send it, local CLI clients may omit it.
    api_allowed_origins: list[str] = [
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
    ]
    # §34: heartbeat cadence and the window before a silent client's turn is
    # canceled (WebSocket disconnect -> cancel after 10 s).
    heartbeat_interval_s: float = 5.0
    client_timeout_s: float = 10.0

    # Your content lives in content_dir; app state (db, cache, logs) in data_dir.
    content_dir: Path = Path.home() / "AIZEN"
    data_dir: Path = Field(default_factory=_default_data_dir)

    ollama_base_url: str = Field(
        default="http://127.0.0.1:11434",
        validation_alias=AliasChoices("AIZEN_OLLAMA_BASE_URL", "OLLAMA_BASE_URL"),
    )
    default_profile: str = "light"
    log_level: str = "INFO"

    max_model_calls_per_turn: int = 4
    context_budget_tokens: int = 4096
    confirm_timeout_s: float = 60.0
    # §34: slow first token emits a "warming up" status but never aborts the turn.
    ttft_timeout_s: float = 15.0
    # Explicit opt-in only: file/document bodies may be logged when true.
    debug_content: bool = False

    @property
    def db_path(self) -> Path:
        return self.data_dir / "aizen.db"

    @property
    def memory_dir(self) -> Path:
        return self.content_dir / "memory"

    @property
    def finances_dir(self) -> Path:
        return self.content_dir / "finances"

    @property
    def knowledge_dir(self) -> Path:
        return self.content_dir / "knowledge"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"
