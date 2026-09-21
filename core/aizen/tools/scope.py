"""Deterministic path / URL scope checks for the permission broker (§28.2)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

# Default extension allowlist for user-content files (Section 18 path safety).
DEFAULT_FILE_EXTENSIONS = frozenset(
    {".md", ".txt", ".pdf", ".docx", ".csv", ".xlsx", ".json", ".yaml", ".yml"}
)


def _collapses(root: Path, candidate: Path) -> bool:
    if ".." in candidate.parts:
        return True
    try:
        candidate.resolve().relative_to(root.resolve())
        return False
    except ValueError:
        return True


def _in_hidden_dir(candidate: Path) -> bool:
    return any(part.startswith(".") for part in candidate.parts)


class PathScopeGuard:
    """Enforce that a tool-supplied path stays inside a trusted root.

    ``deny_prefixes`` (resolved, e.g. the finances root) overrides everything:
    no tool can reach them, regardless of the root.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        allow_extensions: frozenset[str] = DEFAULT_FILE_EXTENSIONS,
        deny_prefixes: tuple[Path | str, ...] = (),
    ) -> None:
        self.root = Path(root).resolve()
        self.allow_extensions = allow_extensions
        self.deny = tuple(Path(p).resolve() for p in deny_prefixes)

    def check(self, raw: str) -> bool:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        if _collapses(self.root, candidate):
            return False
        if _in_hidden_dir(candidate):
            return False
        if candidate.suffix.lower() not in self.allow_extensions:
            return False
        real = candidate.resolve()
        if real != self.root and not real.is_relative_to(self.root):
            return False
        for denied in self.deny:
            if real == denied or real.is_relative_to(denied) or denied.is_relative_to(real):
                return False
        return True


class UrlGuard:
    """Minimal URL policy for T3 fetch/search tools (https/http, scheme-only)."""

    def __init__(self, *, allowed_schemes: frozenset[str] = frozenset({"http", "https"})) -> None:
        self.allowed_schemes = allowed_schemes

    def check(self, raw: str) -> bool:
        parsed = urlparse(raw)
        if parsed.scheme:
            if parsed.scheme not in self.allowed_schemes:
                return False
            return bool(parsed.netloc)
        # No scheme: treat the value as a bare host (https assumed).
        return bool(urlparse(f"https://{raw}").netloc)


def personal_hygiene_reasons(text: str) -> list[str]:
    """Return the names of personal/financial strings detected in a query.

    Used for §28.4/§12 query hygiene: code (not the model) decides whether an
    outbound search or fetch may expose personal data.
    """
    reasons: list[str] = []
    lowered = text.lower()
    if any(cc in lowered for cc in ("iban", "bank account", "routing number")):
        reasons.append("bank-reference")
    if "$" in text or "€" in text or "£" in text or " usd" in lowered or " eur " in lowered:
        reasons.append("currency-amount")
    if any(key in lowered for key in ("security number", "passport", "password", "card number")):
        reasons.append("pii-token")
    return reasons
