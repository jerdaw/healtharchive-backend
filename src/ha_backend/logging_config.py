from __future__ import annotations

import logging
import os
from typing import Optional


class RequestIdFilter(logging.Filter):
    """
    Inject request ID into log records from context.

    If no request ID is available (e.g., CLI context), uses '-' as placeholder.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from ha_backend.request_context import get_request_id

        record.request_id = get_request_id() or "-"
        return True


def _get_log_level_from_env(env_var: str = "HEALTHARCHIVE_LOG_LEVEL") -> int:
    """
    Resolve the desired log level from an environment variable.

    Defaults to INFO when the variable is unset or invalid.
    """
    value = os.getenv(env_var, "INFO").upper()
    return getattr(logging, value, logging.INFO)


def configure_logging(level: Optional[int] = None) -> None:
    """
    Configure basic logging for the backend.

    This is intentionally simple and can be called multiple times
    without causing duplicate handlers in most common configurations.
    """
    if level is None:
        level = _get_log_level_from_env()

    root_logger = logging.getLogger()

    # If handlers are already configured, just adjust the level and add filter.
    if root_logger.handlers:
        root_logger.setLevel(level)
        # Add request ID filter to existing handlers if not already present
        for handler in root_logger.handlers:
            if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
                handler.addFilter(RequestIdFilter())
        return

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s",
    )

    # Add request ID filter to all handlers
    for handler in root_logger.handlers:
        handler.addFilter(RequestIdFilter())

    # Quiet very noisy loggers a bit by default.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


__all__ = ["configure_logging"]
