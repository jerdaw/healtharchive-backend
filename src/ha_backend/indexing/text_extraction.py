from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ha_backend.indexing.mapping import normalize_url_for_grouping


def extract_title(html: str) -> Optional[str]:
    """
    Extract a reasonable title from HTML content.
    """
    soup = BeautifulSoup(html, "html.parser")

    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        if title:
            return title

    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)

    return None


def extract_text(html: str) -> str:
    """
    Extract plain text from HTML content.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove obvious non-content elements.
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Drop common layout/boilerplate containers.
    for tag in soup.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    # Prefer main content when available.
    root = soup.find("main") or soup.find(attrs={"role": "main"})
    if root is not None:
        text = root.get_text(separator=" ", strip=True)
        if len(text) >= 200:
            return text

    return soup.get_text(separator=" ", strip=True)


def make_snippet(text: str, max_len: int = 280) -> str:
    """
    Build a short snippet from plain text, respecting word boundaries.
    """
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    snippet = text[: max_len + 1]
    last_space = snippet.rfind(" ")
    if last_space > 0:
        snippet = snippet[:last_space]
    else:
        snippet = snippet[:max_len]
    return snippet.rstrip() + "…"


def detect_language(text: str, headers: Optional[Dict[str, str]] = None) -> str:
    """
    Very lightweight language detection.

    Prefers Content-Language HTTP header if present; otherwise falls back to
    'en' or 'fr' using simple heuristics, with 'und' as a catch-all.
    """
    headers = headers or {}
    lang_header = headers.get("content-language") or headers.get("Content-Language")
    if lang_header:
        primary = lang_header.split(",")[0].strip().lower()
        if primary:
            return primary

    sample = text[:500].lower()
    if any(token in sample for token in [" le ", " la ", " des ", " que ", " une "]):
        return "fr"
    if any(token in sample for token in [" the ", " and ", " of ", " for ", " with "]):
        return "en"

    return "und"


_ASSET_EXTENSIONS = (
    ".7z",
    ".avi",
    ".css",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".svg",
    ".tgz",
    ".txt",
    ".wav",
    ".webm",
    ".webp",
    ".wmv",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
)


def extract_outlink_groups(
    html: str,
    *,
    base_url: str,
    from_group: str | None = None,
    max_links: int = 200,
) -> set[str]:
    """
    Extract a set of normalized_url_group strings for outgoing links found in main content.

    This is used to derive simple authority signals (e.g., inlink counts) without
    introducing a separate crawler or search service.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove obvious non-content elements.
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Drop common layout/boilerplate containers.
    for tag in soup.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    root = soup.find("main") or soup.find(attrs={"role": "main"}) or soup
    groups: set[str] = set()

    for a in root.find_all("a", href=True):
        href_value = a.get("href")
        if not isinstance(href_value, str):
            continue
        href = href_value.strip()
        if not href or href.startswith("#"):
            continue

        href_lower = href.lower()
        if href_lower.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue

        abs_url = urljoin(base_url, href)
        parts = urlsplit(abs_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            continue

        path_lower = (parts.path or "").lower()
        if any(path_lower.endswith(ext) for ext in _ASSET_EXTENSIONS):
            continue

        group = normalize_url_for_grouping(abs_url)
        if group is None:
            continue
        if from_group and group == from_group:
            continue

        groups.add(group)
        if len(groups) >= max_links:
            break

    return groups


__all__ = [
    "extract_title",
    "extract_text",
    "make_snippet",
    "detect_language",
    "extract_outlink_groups",
]
