from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException, Request, status


def _get_expected_admin_token() -> Optional[str]:
    """
    Read the expected admin token from the environment.

    If unset, admin endpoints are effectively open. This is convenient for
    local development but should be configured in production.
    """
    return os.getenv("HEALTHARCHIVE_ADMIN_TOKEN")


async def require_admin(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> None:
    """
    Dependency that enforces a simple token-based admin auth scheme.

    Behaviour:
    - If HEALTHARCHIVE_ENV is "production" or "staging" and
      HEALTHARCHIVE_ADMIN_TOKEN is unset, fail closed with HTTP 500.
    - If HEALTHARCHIVE_ADMIN_TOKEN is unset in other environments, allow all
      requests (dev mode).
    - If set, require the same token via either:
      * Authorization: Bearer <token>
      * X-Admin-Token: <token>
    """
    env = os.getenv("HEALTHARCHIVE_ENV", "development").lower()
    expected = _get_expected_admin_token()
    if env in {"production", "staging"} and not expected:
        # In non-dev environments, require an admin token to be configured.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin token not configured for this environment",
        )

    if not expected:
        # No token configured: treat admin endpoints as open (dev mode).
        return

    presented: Optional[str] = None

    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            presented = parts[1]
    if not presented and x_admin_token:
        presented = x_admin_token

    if not presented or presented != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin token required",
        )


__all__ = ["require_admin"]
