"""Request context management for correlation IDs."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

# Context variable to store the current request ID
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def generate_request_id() -> str:
    """Generate a new UUIDv4 request ID."""
    return str(uuid.uuid4())


def get_request_id() -> str | None:
    """Get the current request ID from context."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID in context."""
    _request_id_var.set(request_id)


__all__ = ["generate_request_id", "get_request_id", "set_request_id"]
