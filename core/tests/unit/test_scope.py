"""Path/URL scope guards (§28.2) - deterministic, no model involvement."""

from __future__ import annotations

from pathlib import Path

from aizen.tools.scope import PathScopeGuard, UrlGuard, personal_hygiene_reasons


def test_path_inside_root_allowed(tmp_path: Path) -> None:
    guard = PathScopeGuard(tmp_path)
    (tmp_path / "notes.md").write_text("hi")
    assert guard.check("notes.md")
    assert guard.check(str(tmp_path / "notes.md"))


def test_path_traversal_denied(tmp_path: Path) -> None:
    guard = PathScopeGuard(tmp_path)
    assert not guard.check("../secret.txt")
    assert not guard.check("sub/../../secret.txt")
    assert not guard.check("/etc/passwd")


def test_path_hidden_and_extension_denied(tmp_path: Path) -> None:
    guard = PathScopeGuard(tmp_path)
    assert not guard.check(".ssh/id_rsa")
    assert not guard.check("notes.exe")
    assert not guard.check("script.sh")


def test_path_deny_prefix_overrides_root(tmp_path: Path) -> None:
    finances = tmp_path / "finances"
    finances.mkdir()
    (finances / "ledger.md").write_text("secret")
    guard = PathScopeGuard(tmp_path, deny_prefixes=(finances,))
    assert not guard.check("finances/ledger.md")


def test_url_guard_scheme_and_host() -> None:
    guard = UrlGuard()
    assert guard.check("https://example.com/page")
    assert guard.check("http://example.com")
    assert not guard.check("file:///etc/passwd")
    assert not guard.check("javascript:alert(1)")
    assert not guard.check("")


def test_personal_hygiene_detects_amounts_and_pii() -> None:
    assert "currency-amount" in personal_hygiene_reasons("transfer $500 today")
    assert "bank-reference" in personal_hygiene_reasons("my IBAN is here")
    assert "pii-token" in personal_hygiene_reasons("what is my card number")
    assert personal_hygiene_reasons("what is the weather") == []
