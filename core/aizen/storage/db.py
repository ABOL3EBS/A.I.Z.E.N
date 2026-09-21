"""SQLite storage: connection management, migrations, small query helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SCHEMA_VERSION = 3

# Columns added after the initial schema; applied when an older DB is migrated.
_ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "turn_traces": (("verify_json", "TEXT"), ("verified", "INTEGER")),
}


class Database:
    """Thin wrapper around a single SQLite file (WAL, FTS5, foreign keys).

    The core stays dependency-light: no aiosqlite yet. Long-running async work
    should tunnel sync calls through `asyncio.to_thread` in services.
    """

    def __init__(self, path: Path | str, *, migrate: bool = True) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        if migrate:
            self.connect()
            self.migrate()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " id INTEGER PRIMARY KEY,"
            " version INTEGER NOT NULL,"
            " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        self._conn = conn
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        _conn = self._conn
        if _conn is None:
            raise RuntimeError("database connection is closed")
        return _conn

    def migrate(self) -> None:
        applied = {r["version"] for r in self.fetchall("SELECT version FROM schema_migrations")}
        if SCHEMA_VERSION not in applied:
            with self.transaction() as cur:
                cur.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
                self._add_missing_columns(cur)
                cur.execute("INSERT INTO schema_migrations (version) VALUES (?)", (SCHEMA_VERSION,))

    def _add_missing_columns(self, cur: sqlite3.Cursor) -> None:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {row["name"] for row in cur.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in existing:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # --- cursor helpers ---
    def execute(self, sql: str, params: tuple[object, ...] | list[object] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq: Iterator[tuple[object, ...]]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, seq)

    def fetchone(
        self, sql: str, params: tuple[object, ...] | list[object] = ()
    ) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def fetchall(
        self, sql: str, params: tuple[object, ...] | list[object] = ()
    ) -> list[sqlite3.Row]:
        return list(self.execute(sql, params).fetchall())

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        conn = self.conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn.cursor()
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
