from __future__ import annotations

import difflib
from dataclasses import dataclass
from html import escape
import re
from typing import List, Tuple

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

DIFF_VERSION = "v1"
NORMALIZATION_VERSION = "v1"

MAX_DIFF_LINES = 400
CONTEXT_LINES = 3

_HEADING_RE = re.compile(r"^h[1-6]$")
_NOISE_KEYWORDS = (
    "cookie",
    "consent",
    "banner",
    "subscribe",
    "newsletter",
    "notification",
)


@dataclass
class DiffDocument:
    text: str
    lines: List[str]
    sections: List[Tuple[str, str]]


@dataclass
class DiffResult:
    diff_html: str
    diff_truncated: bool
    added_lines: int
    removed_lines: int
    change_ratio: float


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split()).strip()


def _is_noise_tag(tag: Tag) -> bool:
    tag_id = tag.get("id") or ""
    classes = " ".join(tag.get("class", []))
    haystack = f"{tag_id} {classes}".lower()
    return any(token in haystack for token in _NOISE_KEYWORDS)


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    for tag in soup.find_all(["nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    for tag in soup.find_all(True):
        if _is_noise_tag(tag):
            tag.decompose()


def _extract_sections(root: Tag) -> List[Tuple[str, str]]:
    sections: List[Tuple[str, str]] = []
    current_title: str | None = None
    current_parts: List[str] = []

    for node in root.descendants:
        if isinstance(node, Tag) and _HEADING_RE.match(node.name or ""):
            title = _normalize_whitespace(node.get_text(" ", strip=True))
            if title:
                if current_title is not None:
                    sections.append((current_title, _normalize_whitespace(" ".join(current_parts))))
                current_title = title
                current_parts = []
            continue

        if current_title is None:
            continue

        if isinstance(node, NavigableString):
            if node.parent and _HEADING_RE.match(node.parent.name or ""):
                continue
            text = _normalize_whitespace(str(node))
            if text:
                current_parts.append(text)

    if current_title is not None:
        sections.append((current_title, _normalize_whitespace(" ".join(current_parts))))

    if not sections:
        text = _normalize_whitespace(root.get_text(" ", strip=True))
        if text:
            sections.append(("Content", text))

    return sections


def normalize_html_for_diff(html: str) -> DiffDocument:
    soup = BeautifulSoup(html, "html.parser")
    _strip_noise(soup)

    root = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup

    raw_text = root.get_text(separator="\n", strip=True)
    lines = [
        _normalize_whitespace(line)
        for line in raw_text.splitlines()
        if _normalize_whitespace(line)
    ]

    sections = _extract_sections(root)

    return DiffDocument(text=_normalize_whitespace(raw_text), lines=lines, sections=sections)


def _render_diff_line(line: str) -> str:
    css_class = "ha-diff-context"
    prefix = line[:1]

    if line.startswith("@@"):
        css_class = "ha-diff-hunk"
    elif prefix == "+" and not line.startswith("+++"):
        css_class = "ha-diff-add"
    elif prefix == "-" and not line.startswith("---"):
        css_class = "ha-diff-del"

    safe = escape(line)
    return f'<div class="ha-diff-line {css_class}"><code>{safe}</code></div>'


def compute_diff(doc_a: DiffDocument, doc_b: DiffDocument) -> DiffResult:
    diff_lines = list(
        difflib.unified_diff(
            doc_a.lines,
            doc_b.lines,
            n=CONTEXT_LINES,
            lineterm="",
        )
    )

    diff_truncated = False
    if len(diff_lines) > MAX_DIFF_LINES:
        diff_lines = diff_lines[:MAX_DIFF_LINES]
        diff_truncated = True

    added_lines = sum(
        1
        for line in diff_lines
        if line.startswith("+") and not line.startswith("+++")
    )
    removed_lines = sum(
        1
        for line in diff_lines
        if line.startswith("-") and not line.startswith("---")
    )

    ratio = difflib.SequenceMatcher(None, doc_a.lines, doc_b.lines).ratio()
    change_ratio = 1.0 - ratio

    diff_html = "\n".join(_render_diff_line(line) for line in diff_lines)

    return DiffResult(
        diff_html=diff_html,
        diff_truncated=diff_truncated,
        added_lines=added_lines,
        removed_lines=removed_lines,
        change_ratio=change_ratio,
    )


__all__ = [
    "DIFF_VERSION",
    "NORMALIZATION_VERSION",
    "DiffDocument",
    "DiffResult",
    "normalize_html_for_diff",
    "compute_diff",
]
