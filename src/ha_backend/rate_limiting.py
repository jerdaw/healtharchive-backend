"""
Rate limiting configuration and utilities for the HealthArchive API.

This module provides rate limiting middleware using slowapi to prevent abuse
and ensure fair resource allocation across API consumers.

Rate Limits:
    - General API: 120 requests/minute (default for all endpoints)
    - Search: 60 requests/minute (CPU-intensive queries)
    - Exports: 10 requests/minute (large payload generation)
    - Reports: 5 requests/minute (write operations, spam prevention)

The limits are enforced per client IP address and can be disabled in
development environments via HEALTHARCHIVE_RATE_LIMITING_ENABLED=0.

Error Responses:
    - 429 Too Many Requests: Rate limit exceeded
    - Response includes X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After headers
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from ha_backend.config import get_rate_limiting_enabled

# Limiter instance with IP-based rate limiting
# Uses in-memory storage (no external dependencies like Redis)
limiter = Limiter(
    key_func=get_remote_address,
    enabled=get_rate_limiting_enabled(),
    storage_uri="memory://",
    default_limits=["120/minute"],  # Default limit for all endpoints
)

# Per-endpoint rate limit strings
# These can be applied as decorators on specific routes
RATE_LIMIT_SEARCH = "60/minute"
RATE_LIMIT_EXPORTS = "10/minute"
RATE_LIMIT_REPORTS = "5/minute"
