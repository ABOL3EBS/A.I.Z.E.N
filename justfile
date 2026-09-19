set dotenv-load

# --- A.I.Z.E.N. core developer workflow ---

ver := `uv --version`
PY := uv run

# Set up the environment
setup:
	uv sync --all-groups

# Freeze deps into lockfile
lock:
	uv lock

# Run unit/integration tests
test:
	{{PY}} pytest

# Run tests with coverage
test-cov:
	{{PY}} pytest --cov=aizen --cov-report=term-missing

# Lint
lint:
	{{PY}} ruff check core/aizen core/tests

# Format check
fmt:
	{{PY}} ruff format --check core/aizen core/tests

# Format + lint fix
fix:
	{{PY}} ruff format core/aizen core/tests
	{{PY}} ruff check --fix core/aizen core/tests

# Typecheck
type:
	{{PY}} pyright

# Run the eval/model gate suite
eval:
	{{PY}} pytest core/tests/evals -q

# Start the API server (dev)
serve:
	{{PY}} python -m aizen.api

# Run the model gate battery against a running Ollama
model-gate:
	{{PY}} python -m aizen.evals.model_gate --profile light

.PHONY: setup lock test test-cov lint fmt fix type eval serve model-gate