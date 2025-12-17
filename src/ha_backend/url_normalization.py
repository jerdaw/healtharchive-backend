from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_url_for_grouping(url: str) -> str | None:
    """
    Normalize a URL for grouping snapshots into a logical "page".

    Current semantics:
    - Require an explicit scheme + hostname (otherwise return None).
    - Lowercase scheme and hostname.
    - Drop query and fragment.
    - Keep path (defaulting to "/").
    """
    raw = url.strip()
    if not raw:
        return None

    try:
        parts = urlsplit(raw)
    except Exception:
        return None

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if not scheme or not netloc:
        return None

    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


__all__ = ["normalize_url_for_grouping"]

