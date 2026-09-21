"""Storage backend for the core: SQLite (WAL + FTS5) connections and schema."""

from aizen.storage.conversations import ConversationStore
from aizen.storage.db import SCHEMA_PATH, Database
from aizen.storage.records import ToolCallRecorder, TurnRecorder, TurnTraceRecorder

__all__ = [
    "ConversationStore",
    "Database",
    "SCHEMA_PATH",
    "ToolCallRecorder",
    "TurnRecorder",
    "TurnTraceRecorder",
]


def load_schema() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")
