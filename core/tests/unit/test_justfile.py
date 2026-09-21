"""The task runner must work even where the `just` binary is absent.

`scripts/just` shims the handful of recipes AGENTS.md relies on by parsing the
repo `justfile`; these tests pin the recipe surface and the shim itself.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[3]
JUSTFILE = ROOT / "justfile"
SHIM = ROOT / "scripts" / "just"

REQUIRED_RECIPES = frozenset(
    {"dev", "test", "lint", "typecheck", "eval", "model-gate", "gen-types"}
)


def _recipe_names() -> set[str]:
    names: set[str] = set()
    for line in JUSTFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("\t") or not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" in line and ":=" not in line and not line.startswith("set "):
            name = line.split(":", 1)[0].strip()
            if name and " " not in name:
                names.add(name)
    return names


def test_required_recipes_are_present() -> None:
    assert _recipe_names() >= REQUIRED_RECIPES


def test_shim_is_executable() -> None:
    assert SHIM.is_file()
    assert SHIM.stat().st_mode & 0o111


def _load_shim() -> ModuleType:
    loader = SourceFileLoader("aizen_just_shim", str(SHIM))
    spec = importlib.util.spec_from_loader("aizen_just_shim", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shim_parses_the_same_recipes_without_just_installed() -> None:
    assignments, recipes = cast(Any, _load_shim())._parse()
    assert "PY" in assignments
    assert set(recipes) >= REQUIRED_RECIPES


def test_shim_renders_placeholders() -> None:
    rendered = cast(Any, _load_shim())._render("{{PY}} pytest", {"PY": "uv run"})
    assert rendered == "uv run pytest"
