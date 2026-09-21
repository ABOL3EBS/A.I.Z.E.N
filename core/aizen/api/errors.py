"""Typed error -> wire ``error`` event mapping (architecture §23 + §34).

Errors are typed, never swallowed silently, and always mapped to a state and a
user-facing message. Messages must never include financial values or file
contents, so this module only ever emits fixed text plus the exception class.
"""

from __future__ import annotations

from aizen.api.protocol import ErrorEvent
from aizen.errors import (
    CancelledError,
    ContextOverflow,
    FinanceSchemaMismatch,
    IngestParseError,
    ModelNotLoaded,
    PermissionDenied,
    ProviderUnavailable,
    ToolInvalidArgs,
    ToolTimeout,
    UserCancelled,
    VerificationFailed,
    WebFailure,
)

START_OLLAMA_HINT = "Start Ollama, then retry."


def error_event(exc: BaseException, *, model: str | None = None) -> ErrorEvent:
    """Map a core exception to the §34 user-facing error event."""
    if isinstance(exc, ModelNotLoaded):
        pull = f"ollama pull {model}" if model else "ollama pull <model>"
        return ErrorEvent(
            code="model_not_pulled",
            message=f"Model {model or ''} isn't installed.".replace("  ", " ").strip(),
            recoverable=True,
            hint=pull,
        )
    if isinstance(exc, ProviderUnavailable):
        return ErrorEvent(
            code="provider_unavailable",
            message="Model server isn't running.",
            recoverable=True,
            hint=START_OLLAMA_HINT,
        )
    if isinstance(exc, ContextOverflow):
        return ErrorEvent(
            code="context_overflow",
            message="That request is too large for the model's context window.",
            recoverable=False,
            hint="Start a new conversation or narrow the request.",
        )
    if isinstance(exc, ToolTimeout):
        return ErrorEvent(
            code="tool_timeout",
            message="That took too long.",
            recoverable=True,
        )
    if isinstance(exc, ToolInvalidArgs):
        return ErrorEvent(
            code="tool_invalid_args",
            message="The model proposed arguments that didn't validate.",
            recoverable=True,
        )
    if isinstance(exc, PermissionDenied):
        return ErrorEvent(
            code="permission_denied",
            message="That action isn't permitted.",
            recoverable=False,
        )
    if isinstance(exc, VerificationFailed):
        return ErrorEvent(
            code="verification_failed",
            message="I couldn't verify that answer against the tool results.",
            recoverable=True,
        )
    if isinstance(exc, (UserCancelled, CancelledError)):
        return ErrorEvent(code="cancelled", message="Canceled.", recoverable=True)
    if isinstance(exc, WebFailure):
        return ErrorEvent(
            code="web_failure",
            message="I couldn't reach the web right now.",
            recoverable=True,
        )
    if isinstance(exc, FinanceSchemaMismatch):
        return ErrorEvent(
            code="finance_schema_mismatch",
            message="I couldn't read the finance workbook; its layout changed.",
            recoverable=False,
            hint="Re-confirm the column mapping.",
        )
    if isinstance(exc, IngestParseError):
        return ErrorEvent(
            code="ingest_parse_error",
            message="I couldn't parse that document.",
            recoverable=True,
        )
    return ErrorEvent(
        code="internal_error",
        message="Something went wrong in the core.",
        recoverable=True,
    )


def busy_error() -> ErrorEvent:
    """A second ``user_text`` while a turn is already running (§26: one turn)."""
    return ErrorEvent(
        code="busy",
        message="A turn is already running.",
        recoverable=True,
        hint="Wait for it to finish or send cancel_turn.",
    )


__all__ = ["START_OLLAMA_HINT", "busy_error", "error_event"]
