"""Conversation persistence and context restore (architecture §33).

The API layer owns the conversation row for a client session: it creates one on
the first turn, stores the user message and the final verified answer, and
rebuilds the model's context window from those rows on later turns. Taint is not
stored on the turn; it is recomputed from ``messages.origin``/``tainted`` so a
tainted answer keeps the conversation tainted while it is still in context (§29).

Only user/assistant messages are restored: intermediate assistant tool-call and
tool-result messages are recorded in ``tool_calls``/``turn_traces`` for audit but
are not replayed to the provider (a tool result without its call would be an
invalid chat template for most small models).
"""

from __future__ import annotations

import json

from aizen.llm.base import Message, MessageRole, ToolCall
from aizen.storage.db import Database

DEFAULT_HISTORY_LIMIT = 20


class ConversationStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, *, title: str | None = None, private: bool = False) -> int:
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO conversations (title, private) VALUES (?, ?)",
                (title, int(private)),
            )
            row = cur.execute("SELECT last_insert_rowid() AS id").fetchone()
        return int(row["id"])

    def exists(self, conversation_id: int) -> bool:
        return (
            self.db.fetchone("SELECT 1 FROM conversations WHERE id=?", (conversation_id,))
            is not None
        )

    def ensure(
        self,
        conversation_id: int | None,
        *,
        title: str | None = None,
        private: bool = False,
    ) -> int:
        if conversation_id is not None and self.exists(conversation_id):
            return conversation_id
        return self.create(title=title, private=private)

    def set_private(self, conversation_id: int, private: bool) -> None:
        with self.db.transaction() as cur:
            cur.execute(
                "UPDATE conversations SET private=? WHERE id=?", (int(private), conversation_id)
            )

    def is_private(self, conversation_id: int) -> bool:
        row = self.db.fetchone("SELECT private FROM conversations WHERE id=?", (conversation_id,))
        return bool(row["private"]) if row is not None else False

    def add_message(
        self,
        conversation_id: int,
        role: MessageRole | str,
        content: str | None,
        *,
        origin: str = "USER",
        tainted: bool = False,
        tool_calls: list[ToolCall] | None = None,
    ) -> int:
        payload = (
            json.dumps([tc.model_dump() for tc in tool_calls], default=str) if tool_calls else None
        )
        with self.db.transaction() as cur:
            cur.execute(
                "INSERT INTO messages"
                " (conversation_id, role, content, tool_calls_json, origin, tainted)"
                " VALUES (?,?,?,?,?,?)",
                (conversation_id, str(role), content, payload, origin, int(tainted)),
            )
            row = cur.execute("SELECT last_insert_rowid() AS id").fetchone()
        return int(row["id"])

    def history(self, conversation_id: int, *, limit: int = DEFAULT_HISTORY_LIMIT) -> list[Message]:
        rows = self.db.fetchall(
            "SELECT role, content, origin, tainted FROM messages"
            " WHERE conversation_id=? AND role IN ('user','assistant') AND content IS NOT NULL"
            " ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        return [
            Message(
                role=MessageRole(row["role"]),
                content=row["content"],
                origin=row["origin"] or "USER",
                tainted=bool(row["tainted"]),
            )
            for row in reversed(rows)
        ]
