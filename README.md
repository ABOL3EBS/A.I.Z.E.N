# A.I.Z.E.N. — Local Personal AI Operating Environment

Local-first personal AI agent runtime. **The model is a small, replaceable language
layer — code does the real work: routing, retrieval, math, permissions. The LLM is
never the trust boundary.**

See `aizen-architecture-v2.md` for the full design. This repository currently contains
the **Python core** (`core/aizen`) — the agent runtime, pre-router, answer verifier,
tool system, finance engine, memory, RAG and the API layer — plus tests and evals.

## Layout

```
core/aizen/
  api/            FastAPI + WebSocket API, token auth
  agent/          agent loop, pre-router, context builder, answer verifier, cancellation
  llm/            LLMProvider protocol; Ollama and OpenAI-compat adapters; profiles
  tools/          ToolSpec/Tool base, registry, permission broker, builtin tools
  services/       memory, knowledge (RAG), finance, web
  finance/        workbook inspector → profiler → ledger → typed query functions
  ingest/         file watcher, ingestion queue, parsers, embedder
  storage/        SQLite (WAL, FTS5) + schema
  config/         settings, model profiles, routing rules
core/tests/       unit + integration + finance goldens
```

## Quick start

```bash
uv sync            # install deps (Python 3.12+)
just test          # run the test suite
just lint && just type
```

## Running the API

```bash
just serve
```

Requires Ollama running on `localhost:11434` with a profile model pulled (see
`core/aizen/config/model_profiles.yaml`). The agent pipeline works without Ollama:
integration tests use a scripted fake provider, and every *code* path (routing,
verification, permissions, math) is deterministic and model-free.

## Design invariants (from the architecture doc)

- **Facts, not prose** — tools return compact structured facts; the answer verifier
  rejects any number in the draft that is not in tool output.
- **Pre-router narrows** — the model only ever sees the toolset of the routed domain
  (2–5 tools), never every tool.
- **Finance is deterministic** — Excel is the source of truth, SQLite the query
  surface, `Decimal` the math, templates the sentence. Finance is excluded from
  general RAG.
- **Tiered permissions — enforced outside the LLM** — the permission broker is the
  gate; confirmation text is rendered from tool-owned summaries, never model text.