"""Structured JSON logging via structlog (rotating, redacted-friendly).

Every record passes through the redaction gate (Section 18 / §30): amounts,
account numbers, credentials and emails are stripped **before** the record is
rendered. File contents are additionally omitted unless ``debug_content`` is on.
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.typing import EventDict, WrappedLogger

from aizen.observability.redaction import Redactor

_redactor = Redactor()


def redact_event_dict(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: redact every value in the record."""
    redacted = _redactor.redact_mapping(dict(event_dict))
    event_dict.clear()
    if isinstance(redacted, dict):
        event_dict.update(redacted)
    return event_dict


def setup_logging(level: str = "INFO", *, debug_content: bool = False) -> None:
    """Configure structlog-ish JSON logging; quiet uvicorn/httpx noise."""
    global _redactor
    _redactor = Redactor(debug_content=debug_content)
    lvl = getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_event_dict,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    for name in ("uvicorn", "uvicorn.error", "httpx"):
        logging.getLogger(name).setLevel(max(lvl, logging.WARNING))


__all__ = ["redact_event_dict", "setup_logging"]
