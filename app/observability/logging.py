from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

SENSITIVE_KEY_PARTS = ("api_key", "authorization", "cookie", "password", "secret", "token")


def redact_sensitive_fields(
    _logger: object, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in tuple(event_dict):
        if any(part in key.casefold() for part in SENSITIVE_KEY_PARTS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            redact_sensitive_fields,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
