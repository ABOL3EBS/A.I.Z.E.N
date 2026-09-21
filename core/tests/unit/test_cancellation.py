from __future__ import annotations

from aizen.agent.cancellation import CancellationToken, token_or_new
from aizen.errors import CancelledError


def test_not_cancelled_by_default() -> None:
    token = CancellationToken()
    assert not token.is_cancelled()
    token.check()


def test_request_cancels() -> None:
    token = CancellationToken()
    token.request_cancel()
    assert token.is_cancelled()
    try:
        token.check()
    except CancelledError:
        return
    raise AssertionError("expected CancelledError")


def test_token_or_new_reuses() -> None:
    token = CancellationToken()
    assert token_or_new(token) is token
    fresh = token_or_new(None)
    assert isinstance(fresh, CancellationToken)
    assert fresh is not token
