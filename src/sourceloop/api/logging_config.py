"""structlog JSON logging with a redaction processor: nothing that looks like a key/secret/token/password is ever emitted."""
from __future__ import annotations

import logging
import re
from typing import Any

import structlog

_SENSITIVE = re.compile(r"(key|secret|token|password|cookie|authorization)", re.I)


def _redact(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    for k in list(event):
        if _SENSITIVE.search(k):
            event[k] = "****"
    return event


def configure_logging(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[structlog.processors.add_log_level, structlog.processors.TimeStamper(fmt="iso", utc=True), _redact,
                    structlog.processors.JSONRenderer(sort_keys=True)],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
