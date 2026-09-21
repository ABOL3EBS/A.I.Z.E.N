from __future__ import annotations

from pathlib import Path

import pytest
from aizen.storage import Database


def test_migrate_creates_tables(db: Database) -> None:
    assert db.fetchone("SELECT version FROM schema_migrations")  # 1 row
    for table in (
        "documents",
        "chunks",
        "chunks_fts",
        "ingest_jobs",
        "fin_workbooks",
        "memories",
        "memories_fts",
        "conversations",
        "messages",
        "turn_traces",
        "route_log",
        "permission_grants",
        "audit_log",
        "egress_log",
        "settings",
    ):
        assert db.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ), f"missing table {table}"


def test_fts5_syncs_from_chunks(db: Database) -> None:
    db.execute("INSERT INTO documents (path, type) VALUES (?,?)", ("/d.md", "md"))
    db.execute(
        "INSERT INTO chunks (document_id, ordinal, text) VALUES (1, 0, ?)", ("the lazy fox",)
    )
    row = db.fetchone("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'lazy'")
    assert row is not None


def test_wal_mode(db: Database) -> None:
    row = db.fetchone("PRAGMA journal_mode")
    assert row is not None
    assert row[0] == "wal"


def test_transaction_rolls_back_on_error(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction() as cur:
        cur.execute("INSERT INTO settings (key, value) VALUES (?,?)", ("k", "v"))
        raise RuntimeError("boom")
    assert db.fetchone("SELECT value FROM settings WHERE key=?", ("k",)) is None


def test_second_open_does_not_remigrate(tmp_path: Path) -> None:
    path = tmp_path / "aizen.db"
    Database(path, migrate=True).close()
    again = Database(path, migrate=True)
    rows = again.fetchall("SELECT version FROM schema_migrations")
    assert len(rows) == 1
    again.close()


def test_migrate_adds_trace_columns_to_an_older_db(tmp_path: Path) -> None:
    database = Database(tmp_path / "aizen.db", migrate=False)
    database.connect()
    with database.transaction() as cur:
        cur.execute(
            "CREATE TABLE turn_traces ("
            " id INTEGER PRIMARY KEY, turn_id TEXT, spans_json TEXT, tokens_json TEXT,"
            " tainted INTEGER NOT NULL DEFAULT 0, redacted INTEGER NOT NULL DEFAULT 1)"
        )
        cur.execute("INSERT INTO schema_migrations (version) VALUES (2)")
    database.migrate()
    columns = {row["name"] for row in database.fetchall("PRAGMA table_info(turn_traces)")}
    assert {"verify_json", "verified"} <= columns
    assert {
        row["version"] for row in database.fetchall("SELECT version FROM schema_migrations")
    } == {
        2,
        3,
    }
    database.close()
