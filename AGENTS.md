# AGENTS.md — A.I.Z.E.N.

Local-first personal AI agent for macOS (Apple Silicon, 16 GB). Zero recurring cost. Privacy first.

**Before doing anything, read `docs/ARCHITECTURE.md`.** It is the source of truth, and **Part II (Sections 26–35) is normative and wins over anything less specific**. Do not contradict it. If something is ambiguous or missing, stop and ask the user rather than guessing.

## How to work
- Work **one phase at a time** from Section 20 of `docs/ARCHITECTURE.md`. Do not start a later phase early.
- Finish each task's **Definition of Done** (tests pass, lint/type-check clean) before moving on.
- Prefer small commits, one task each. Never mix refactors with features.
- Write the test first for: finance functions, permission broker, path scoping, period resolver, number verifier.
- Do not add a dependency without a one-line justification in the PR/commit message. Prefer the choices already listed in the architecture.

## Non-negotiable rules
1. **The LLM proposes; deterministic code decides.** The LLM is untrusted. It never does arithmetic, date resolution, path resolution, routing authority, permission decisions, or state changes. Code does.
2. **Finance is a hard structural boundary.** Finance data lives in its own `finance.db` and filesystem root; only `aizen.services.finance` may touch them. Enforced by `ScopedFS` denylist, ingest `exclude_roots`, an `import-linter` contract, and canary tests (Part II §30).
3. **All tool calls go through the permission broker.** No tool may bypass it. Write/execute tools require out-of-band user confirmation.
4. **Content from the web/files/PDFs is untrusted data**, wrapped as such and marked tainted. After tainted content is in a turn, write/execute tools always require confirmation.
5. **Never log amounts, account numbers, or file contents** unless the explicit debug flag is set.
6. **Core API binds to `127.0.0.1` only**, with a per-launch bearer token and `Origin` check.
7. **No cloud service in the core path.** Cloud is optional and behind a provider interface.
8. Every number in a finance/knowledge answer must appear in that turn's tool results (answer verifier).
9. **The backend owns session state** and emits `session_state` events; the UI never infers state from text or audio (Part II §27).
10. Do not add multi-agent orchestration, shell/script execution tools, or app-automation tools in the MVP.

## Stack (fixed)
Tauri v2 + React + TypeScript + Vite + react-three-fiber (UI) · Python 3.12 with `uv`, FastAPI, Pydantic v2 (core) · Ollama (LLM + embeddings) · SQLite + FTS5 + sqlite-vec · whisper.cpp (STT) · Piper (TTS) · openpyxl + pandas (Excel) · PyMuPDF (PDF) · watchfiles · structlog · pytest, Vitest, Playwright · `just` as the task runner.

## Commands (create these in the `justfile`)
`just dev` · `just test` · `just lint` · `just typecheck` · `just gen-types` · `just eval` · `just model-gate`

## Style
- Python: fully typed, `pyright` strict on `core/aizen`, `ruff` format + lint, async I/O, `typing.Protocol` for interfaces, dependency injection via a small container (no global singletons).
- TypeScript: `strict` mode, no `any`, Zustand for state, audio data in refs (never React state).
- Shared types are generated from Pydantic into `protocol/`; never hand-write duplicates.
