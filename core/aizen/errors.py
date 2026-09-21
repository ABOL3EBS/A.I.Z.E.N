"""Error taxonomy (architecture section 20).

Each error carries a user-facing message, a recoverability flag and, where
relevant, the orb-state detail it maps to.
"""

from __future__ import annotations


class AizenError(Exception):
    """Base class for all A.I.Z.E.N. core errors."""

    recoverable: bool = True

    def __init__(self, message: str = "", *, recoverable: bool | None = None) -> None:
        super().__init__(message)
        if recoverable is not None:
            self.recoverable = recoverable


class ProviderUnavailable(AizenError):
    """The model provider (Ollama/LM Studio/...) could not be reached."""

    recoverable = True


class ModelNotLoaded(ProviderUnavailable):
    """The requested model is not loaded or not present on the provider."""

    recoverable = True


class ContextOverflow(AizenError):
    """The assembled context exceeded the model's window."""

    recoverable = False


class ToolTimeout(AizenError):
    recoverable = True


class ToolInvalidArgs(AizenError):
    recoverable = True


class PermissionDenied(AizenError):
    """Enforced by the permission broker, never by the model."""

    recoverable = False


class UserCancelled(AizenError):
    recoverable = True


class CancelledError(AizenError):
    """Cancellation token fired; the turn was aborted."""

    recoverable = True


class VerificationFailed(AizenError):
    """The answer verifier could not match a number in the draft."""

    recoverable = True


class WebFailure(AizenError):
    recoverable = True


class FinanceSchemaMismatch(AizenError):
    recoverable = False


class IngestParseError(AizenError):
    recoverable = True
