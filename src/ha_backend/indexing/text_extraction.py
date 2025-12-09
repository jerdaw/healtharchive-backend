from __future__ import annotations

from typing import Dict, Optional

from bs4 import BeautifulSoup


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
    text = soup.get_text(separator=" ", strip=True)
    return text


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


__all__ = ["extract_title", "extract_text", "make_snippet", "detect_language"]

