-- A.I.Z.E.N. core schema (WAL + FTS5) for aizen.db.
-- Source of truth: docs/ARCHITECTURE.md Part II §33 (storage separation).
-- Finance tables live in finance.db (§30) and are removed from this file in the
-- finance-boundary migration; `fin_*` here is transitional.

-- --- Tracking ---
CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- --- Knowledge index ---
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT,
    mtime REAL,
    size INTEGER,
    type TEXT,
    sensitivity TEXT,
    domain TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    indexed_at TEXT,
    parser_version TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    heading_path TEXT,
    page INTEGER,
    token_count INTEGER,
    embed_model TEXT,
    embed_version TEXT,
    UNIQUE (document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content='chunks',
    content_rowid='id',
    tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,          -- packed little-endian float32
    dim INTEGER NOT NULL,
    embed_model TEXT
);

CREATE TABLE IF NOT EXISTS ingest_jobs (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    queued_at TEXT NOT NULL DEFAULT (datetime('now')),
    done_at TEXT
);

-- --- Finance ledger ---
CREATE TABLE IF NOT EXISTS fin_workbooks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT,
    profile_json TEXT,
    profile_version TEXT,
    confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS fin_transactions (
    id INTEGER PRIMARY KEY,
    workbook_id INTEGER NOT NULL REFERENCES fin_workbooks(id) ON DELETE CASCADE,
    sheet TEXT NOT NULL,
    cell_ref TEXT NOT NULL,
    date TEXT,
    amount TEXT NOT NULL,             -- canonical Decimal as string
    currency TEXT,
    category TEXT,
    description TEXT,
    account TEXT
);

CREATE TABLE IF NOT EXISTS fin_balances (
    id INTEGER PRIMARY KEY,
    workbook_id INTEGER NOT NULL REFERENCES fin_workbooks(id) ON DELETE CASCADE,
    sheet TEXT NOT NULL,
    cell_ref TEXT NOT NULL,
    account TEXT,
    balance TEXT NOT NULL,            -- canonical Decimal as string
    as_of TEXT
);

-- --- Memory ---
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='memories', content_rowid='id', tokenize='porter');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    dim INTEGER NOT NULL,
    embed_model TEXT
);

-- --- Conversation + traces ---
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    title TEXT,
    private INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls_json TEXT,
    origin TEXT NOT NULL DEFAULT 'USER',
    tainted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    tainted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- --- Turns and tools (§33) ---
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY,
    turn_id TEXT NOT NULL UNIQUE,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    route_domain TEXT,
    taint_level TEXT NOT NULL DEFAULT 'CLEAN',
    model_profile TEXT,
    verified INTEGER,
    final_state TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY,
    turn_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    tier TEXT,
    args_json_redacted TEXT,
    args_hash TEXT,
    decision TEXT,
    decision_reason TEXT,
    tainted INTEGER NOT NULL DEFAULT 0,
    taint_sources_json TEXT,
    status TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    result_summary TEXT,
    UNIQUE (turn_id, call_id)
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_turn ON tool_calls(turn_id);

CREATE TABLE IF NOT EXISTS turn_traces (
    id INTEGER PRIMARY KEY,
    conversation_id INTEGER REFERENCES conversations(id) ON DELETE SET NULL,
    turn_id TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    spans_json TEXT,
    tokens_json TEXT,
    verify_json TEXT,
    verified INTEGER,
    tainted INTEGER NOT NULL DEFAULT 0,
    redacted INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS route_log (
    id INTEGER PRIMARY KEY,
    turn_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    rule_hits_json TEXT,
    model_overrode INTEGER NOT NULL DEFAULT 0
);

-- --- Security / audit ---
CREATE TABLE IF NOT EXISTS permission_grants (
    id INTEGER PRIMARY KEY,
    tool TEXT NOT NULL,
    scope TEXT,
    args_hash TEXT,
    granted_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    event TEXT NOT NULL,
    tool TEXT,
    tool_call_id TEXT,
    decision TEXT,
    tainted INTEGER NOT NULL DEFAULT 0,
    detail TEXT,
    redacted INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS egress_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    query TEXT,
    redacted INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_gate_reports (
    id INTEGER PRIMARY KEY,
    model TEXT NOT NULL,
    digest TEXT NOT NULL,
    quantization TEXT,
    num_ctx INTEGER,
    passed INTEGER NOT NULL DEFAULT 0,
    report_json TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);