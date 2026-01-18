from __future__ import annotations

import re
from typing import Dict, Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from ha_backend.indexing.mapping import normalize_url_for_grouping


# ---------------------------------------------------------------------------
# Boilerplate phrase lists (bilingual EN/FR)
# ---------------------------------------------------------------------------

# Phrases that indicate navigation/UI boilerplate (skip these in snippets).
_BOILERPLATE_PHRASES = (
    # English navigation/UI
    "skip to main content",
    "skip to content",
    "skip to navigation",
    "go to main content",
    "toggle navigation",
    "open menu",
    "close menu",
    "search",
    "menu",
    "sign in",
    "log in",
    "register",
    "subscribe",
    "cookie",
    "cookies",
    "privacy policy",
    "terms of use",
    "terms and conditions",
    "accept all",
    "reject all",
    "manage preferences",
    # French equivalents
    "passer au contenu principal",
    "aller au contenu principal",
    "ouvrir le menu",
    "fermer le menu",
    "recherche",
    "connexion",
    "s'inscrire",
    "témoins",
    "politique de confidentialité",
    "conditions d'utilisation",
    "accepter tout",
    "refuser tout",
)

# Title prefixes that indicate an archived page.
_ARCHIVED_TITLE_PREFIXES = (
    "archived",
    "archived -",
    "archived:",
    "archive",
    "[archived]",
    # French
    "archivé",
    "archivée",
    "archivé -",
    "archivée -",
    "[archivé]",
    "[archivée]",
)

# Text patterns in body that indicate archived content (bilingual).
_ARCHIVED_BODY_PATTERNS = (
    "we have archived this page",
    "this page has been archived",
    "this content has been archived",
    "archived content",
    "no longer being updated",
    "information archivée",
    "cette page a été archivée",
    "nous avons archivé cette page",
    "contenu archivé",
    "n'est plus mis à jour",
)

# ARIA roles that typically contain boilerplate/navigation.
_BOILERPLATE_ARIA_ROLES = ("navigation", "banner", "contentinfo", "search")


# ---------------------------------------------------------------------------
# DOM Cleaning Helpers
# ---------------------------------------------------------------------------


def _clean_soup_for_extraction(soup: BeautifulSoup) -> None:
    """
    Remove non-content elements from a BeautifulSoup tree in place.

    This includes:
    - Script/style/noscript tags
    - Semantic boilerplate tags (nav, header, footer, aside, form)
    - Elements with ARIA roles indicating navigation/boilerplate
    """
    # Remove script/style/noscript.
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    # Remove semantic boilerplate containers.
    for tag in soup.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    # Remove elements with boilerplate ARIA roles.
    for role in _BOILERPLATE_ARIA_ROLES:
        for tag in soup.find_all(attrs={"role": role}):
            tag.decompose()


def _find_content_root(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    """
    Find the best content root in a cleaned soup.

    Preference order:
    1. <main> or [role=main]
    2. <article>
    3. Best-scoring candidate container (div, section, article)
    4. Fall back to soup itself
    """
    # Prefer <main> or [role=main].
    main = soup.find("main") or soup.find(attrs={"role": "main"})
    if main is not None:
        text = main.get_text(separator=" ", strip=True)
        if len(text) >= 100:
            return main

    # Prefer <article>.
    article = soup.find("article")
    if article is not None:
        text = article.get_text(separator=" ", strip=True)
        if len(text) >= 100:
            return article

    # Score candidate containers.
    best_candidate = None
    best_score = -1.0

    for tag in soup.find_all(["div", "section", "article"]):
        if not isinstance(tag, Tag):
            continue

        text = tag.get_text(separator=" ", strip=True)
        text_len = len(text)
        if text_len < 100:
            continue

        # Count links and compute link density.
        links = tag.find_all("a")
        link_text_len = sum(len(a.get_text(strip=True)) for a in links)
        link_density = link_text_len / text_len if text_len > 0 else 0

        # Score: text length (positive), link density (negative), punctuation (positive).
        punctuation_count = sum(1 for c in text if c in ".!?;:")
        score = text_len * 0.01 + punctuation_count * 0.5 - link_density * 100

        if score > best_score:
            best_score = score
            best_candidate = tag

    if best_candidate is not None:
        return best_candidate

    return soup


def _is_boilerplate_text(text: str) -> bool:
    """Check if text starts with or is dominated by boilerplate phrases."""
    text_lower = text.lower().strip()[:200]

    for phrase in _BOILERPLATE_PHRASES:
        if text_lower.startswith(phrase):
            return True

    # Check if text is mostly navigation-like (many short lines with little punctuation).
    lines = text_lower.split("\n")
    if len(lines) > 3:
        short_lines = sum(1 for line in lines if len(line.strip()) < 30)
        if short_lines / len(lines) > 0.7:
            # Likely a navigation list.
            return True

    return False


# ---------------------------------------------------------------------------
# Title Extraction
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Text Extraction
# ---------------------------------------------------------------------------


def extract_text(html: str) -> str:
    """
    Extract plain text from HTML content with improved boilerplate removal.
    """
    soup = BeautifulSoup(html, "html.parser")
    _clean_soup_for_extraction(soup)

    root = _find_content_root(soup)
    return root.get_text(separator=" ", strip=True)


def extract_content_text(html: str, max_chars: int = 4096) -> str:
    """
    Extract cleaned main content text for FTS indexing.

    Returns up to max_chars of cleaned text from the main content area.
    This is used for Postgres FTS vectors (4KB default) while the UI snippet
    remains short (~280 chars).
    """
    text = extract_text(html)
    text = " ".join(text.split())  # Normalize whitespace.

    if len(text) <= max_chars:
        return text

    # Truncate at word boundary.
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    if last_space > max_chars * 0.8:
        return truncated[:last_space]
    return truncated


# ---------------------------------------------------------------------------
# Snippet Generation
# ---------------------------------------------------------------------------


def make_snippet(text: str, max_len: int = 280) -> str:
    """
    Build a short snippet from plain text, respecting word boundaries.

    Filters out boilerplate phrases and tries to find meaningful content.
    """
    text = " ".join(text.split())

    # If text starts with boilerplate, try to skip past it.
    if _is_boilerplate_text(text):
        # Try to find first sentence that looks like content.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sentence in sentences:
            if len(sentence) >= 40 and not _is_boilerplate_text(sentence):
                text = sentence
                break

    if len(text) <= max_len:
        return text

    snippet = text[: max_len + 1]
    last_space = snippet.rfind(" ")
    if last_space > 0:
        snippet = snippet[:last_space]
    else:
        snippet = snippet[:max_len]
    return snippet.rstrip() + "…"


# ---------------------------------------------------------------------------
# Language Detection
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Archived Detection (v3 ranking support)
# ---------------------------------------------------------------------------


def detect_is_archived(title: Optional[str], text: str) -> bool:
    """
    Detect whether a page is an archived/historical version.

    Conservative detection based on:
    - Title prefix patterns (e.g., "Archived - ...")
    - Body text patterns indicating archived content

    Returns True if the page appears to be archived, False otherwise.
    """
    # Check title prefix.
    if title:
        title_lower = title.lower().strip()
        for prefix in _ARCHIVED_TITLE_PREFIXES:
            if title_lower.startswith(prefix):
                return True

    # Check body for archived banner phrases.
    text_lower = text.lower()[:2000]  # Check first 2000 chars for banners.
    for pattern in _ARCHIVED_BODY_PATTERNS:
        if pattern in text_lower:
            return True

    return False


# ---------------------------------------------------------------------------
# Outlink Extraction
# ---------------------------------------------------------------------------

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
    _clean_soup_for_extraction(soup)

    root = _find_content_root(soup)
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
    "extract_content_text",
    "make_snippet",
    "detect_language",
    "detect_is_archived",
    "extract_outlink_groups",
]

