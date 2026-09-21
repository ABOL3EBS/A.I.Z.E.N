from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from aizen.config import Settings
from aizen.storage import Database


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_token="test-token",
        data_dir=tmp_path / "data",
        content_dir=tmp_path / "content",
        ollama_base_url="http://127.0.0.1:1",
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "aizen.db", migrate=True)
    yield database
    database.close()
