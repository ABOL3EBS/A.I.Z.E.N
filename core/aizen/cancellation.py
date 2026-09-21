"""Cooperative cancellation shared by the LLM stream, tools, and the loop.

Top-level module on purpose: the LLM provider layer imports it, and it must not
pull in the `agent` package (which transitively imports the LLM layer).
"""

from __future__ import annotations

import threading

from aizen.errors import CancelledError


class CancellationToken:
    """A single cancel token per turn.

    Thread-safe so the UI thread, tool tasks, TTS queue and playback can all
    check the same flag (architecture section 5.3).
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._lock = threading.Lock()

    def request_cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def check(self) -> None:
        """Raise CancelledError so long-lived loops abort at checkpoints."""
        if self.is_cancelled():
            raise CancelledError("turn cancelled")


def token_or_new(token: CancellationToken | None) -> CancellationToken:
    """Return the caller's token, or a fresh one so loops always have a checkpoint."""
    return token if token is not None else CancellationToken()
