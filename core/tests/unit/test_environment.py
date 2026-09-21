"""Environment hardening: supported Python + wheel-only dependencies.

A.I.Z.E.N. must install without a compiler on the user's machine, so every
dependency has to be available as a wheel. This test fails loudly if a source
distribution sneaks in, and pins the interpreter floor from ``pyproject.toml``.
"""

from __future__ import annotations

import sys
import tomllib
from importlib.metadata import distributions
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def test_python_version_is_supported() -> None:
    assert sys.version_info >= (3, 12)


def test_pyproject_requires_python_is_satisfied() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    requires = data["project"]["requires-python"]
    assert requires == ">=3.12"
    assert sys.version_info >= (3, 12)


def test_all_installed_distributions_came_from_wheels() -> None:
    """No dependency was built from an sdist (which would need a toolchain)."""
    missing = sorted(
        (dist.metadata["Name"] or "?")
        for dist in distributions()
        if dist.read_text("WHEEL") is None
    )
    assert missing == []
