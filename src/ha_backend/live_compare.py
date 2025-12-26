from __future__ import annotations

import difflib
import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ha_backend.diffing import (
    DIFF_VERSION,
    NORMALIZATION_VERSION,
    DiffDocument,
    compute_diff,
    normalize_html_for_diff,
    normalize_html_for_diff_full_page,
)
from ha_backend.indexing.viewer import find_record_for_snapshot
from ha_backend.models import Snapshot


class LiveCompareError(RuntimeError):
    pass


class LiveFetchBlocked(LiveCompareError):
    pass


class LiveFetchError(LiveCompareError):
    pass


class LiveFetchNotHtml(LiveFetchError):
    pass


class LiveFetchTooLarge(LiveFetchError):
    pass


class LiveCompareTooLarge(LiveCompareError):
    pass


@dataclass
class LiveFetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: Optional[str]
    bytes_read: int
    fetched_at: datetime
    html: str


@dataclass
class CompareStats:
    added_sections: int
    removed_sections: int
    changed_sections: int
    added_lines: int
    removed_lines: int
    change_ratio: float
    high_noise: bool


@dataclass
class CompareResult:
    diff_html: str
    diff_truncated: bool
    diff_version: str
    normalization_version: str
    stats: CompareStats


@dataclass
class CompareRenderInstruction:
    type: str
    line_index_a: Optional[int] = None
    line_index_b: Optional[int] = None


@dataclass
class CompareRenderPayload:
    archived_lines: list[str]
    live_lines: list[str]
    render_instructions: list[CompareRenderInstruction]
    render_truncated: bool
    render_line_limit: int


@dataclass
class CompareTextExtraction:
    requested_mode: str
    used_mode: str
    fallback_applied: bool


_MAX_URL_LEN = 4096


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if hasattr(address, "is_global"):
        return bool(address.is_global)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_host(hostname: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise LiveFetchError("DNS resolution failed for live URL.") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise LiveFetchError("DNS resolution failed for live URL.")

    for ip_value in addresses:
        try:
            ip_addr = ipaddress.ip_address(ip_value)
        except ValueError as exc:
            raise LiveFetchError("Invalid IP returned by DNS for live URL.") from exc
        if not _is_public_ip(ip_addr):
            raise LiveFetchBlocked("Live fetch blocked by safety rules.")


def _normalize_url(raw_url: str) -> str:
    if not raw_url:
        raise LiveFetchBlocked("Live URL is missing.")
    url = raw_url.strip()
    if len(url) > _MAX_URL_LEN:
        raise LiveFetchBlocked("Live URL is too long.")

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise LiveFetchBlocked("Live URL must use http or https.")
    if parsed.username or parsed.password:
        raise LiveFetchBlocked("Live URL credentials are not allowed.")

    hostname = parsed.hostname
    if not hostname:
        raise LiveFetchBlocked("Live URL hostname is missing.")
    hostname = hostname.strip().lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise LiveFetchBlocked("Live URL hostname is not allowed.")

    port = parsed.port
    if port is not None and port not in {80, 443}:
        raise LiveFetchBlocked("Live URL port is not allowed.")

    try:
        ip_addr = ipaddress.ip_address(hostname)
    except ValueError:
        resolved_port = port or (443 if scheme == "https" else 80)
        _resolve_host(hostname, resolved_port)
    else:
        if not _is_public_ip(ip_addr):
            raise LiveFetchBlocked("Live URL hostname is not allowed.")

    normalized = urlunsplit(
        (
            scheme,
            parsed.netloc,
            parsed.path or "",
            parsed.query or "",
            "",
        )
    )
    return normalized


def is_html_mime_type(mime_type: Optional[str]) -> bool:
    if not mime_type:
        return False
    content_type = mime_type.split(";", 1)[0].strip().lower()
    return content_type in {"text/html", "application/xhtml+xml"}


def load_snapshot_html(snapshot: Snapshot, *, max_bytes: Optional[int] = None) -> str:
    record = find_record_for_snapshot(snapshot)
    if record is None:
        raise LiveCompareError("Archived HTML is not available for this snapshot.")
    body_bytes = record.body_bytes
    if max_bytes is not None and len(body_bytes) > max_bytes:
        raise LiveCompareTooLarge("Archived HTML is too large to compare live.")
    try:
        return body_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        raise LiveCompareError("Failed to decode archived HTML.") from exc


def fetch_live_html(
    url: str,
    *,
    timeout_seconds: float,
    max_redirects: int,
    max_bytes: int,
    user_agent: str,
) -> LiveFetchResult:
    requested_url = _normalize_url(url)
    current_url = requested_url

    timeout = httpx.Timeout(timeout_seconds)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=False) as client:
        for _ in range(max_redirects + 1):
            current_url = _normalize_url(current_url)
            try:
                with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise LiveFetchError("Live fetch redirect missing Location header.")
                        current_url = urljoin(current_url, location)
                        continue

                    if response.status_code < 200 or response.status_code >= 300:
                        raise LiveFetchError(
                            f"Live fetch failed with status {response.status_code}."
                        )

                    content_type = response.headers.get("content-type")
                    if not is_html_mime_type(content_type):
                        raise LiveFetchNotHtml("Live URL is not HTML.")

                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            advertised = int(content_length)
                        except ValueError:
                            advertised = None
                        if advertised is not None and advertised > max_bytes:
                            raise LiveFetchTooLarge("Live HTML is too large to compare safely.")

                    bytes_read = 0
                    chunks: list[bytes] = []
                    try:
                        for chunk in response.iter_bytes():
                            bytes_read += len(chunk)
                            if bytes_read > max_bytes:
                                raise LiveFetchTooLarge("Live HTML is too large to compare safely.")
                            chunks.append(chunk)
                    except httpx.HTTPError as exc:
                        raise LiveFetchError("Live fetch failed while reading HTML.") from exc

                    body = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
                    try:
                        html = body.decode(encoding, errors="replace")
                    except LookupError:
                        html = body.decode("utf-8", errors="replace")
                    fetched_at = datetime.now(timezone.utc)

                    return LiveFetchResult(
                        requested_url=requested_url,
                        final_url=current_url,
                        status_code=response.status_code,
                        content_type=content_type,
                        bytes_read=bytes_read,
                        fetched_at=fetched_at,
                        html=html,
                    )
            except httpx.TimeoutException as exc:
                raise LiveFetchError("Live fetch timed out.") from exc
            except httpx.RequestError as exc:
                raise LiveFetchError("Live fetch failed.") from exc

    raise LiveFetchError("Live fetch exceeded redirect limit.")


def _compute_section_stats(
    doc_a: Optional[dict[str, str]],
    doc_b: Optional[dict[str, str]],
) -> tuple[int, int, int]:
    if not doc_a and not doc_b:
        return 0, 0, 0

    sections_a = set(doc_a.keys()) if doc_a else set()
    sections_b = set(doc_b.keys()) if doc_b else set()

    added = len(sections_b - sections_a)
    removed = len(sections_a - sections_b)

    common = sections_a & sections_b
    changed = 0
    for title in common:
        if (doc_a or {}).get(title, "") != (doc_b or {}).get(title, ""):
            changed += 1

    return added, removed, changed


def build_compare_documents(
    archived_html: str,
    live_html: str,
    *,
    mode: str = "main",
) -> tuple[DiffDocument, DiffDocument, CompareTextExtraction]:
    requested_mode = mode.lower().strip() if mode else "main"
    if requested_mode not in {"main", "full"}:
        requested_mode = "main"

    if requested_mode == "full":
        doc_a = normalize_html_for_diff_full_page(archived_html)
        doc_b = normalize_html_for_diff_full_page(live_html)
        extraction = CompareTextExtraction(
            requested_mode=requested_mode,
            used_mode="full",
            fallback_applied=False,
        )
        return doc_a, doc_b, extraction

    doc_a = normalize_html_for_diff(archived_html)
    doc_b = normalize_html_for_diff(live_html)

    fallback_applied = False
    used_mode = "main"
    if not doc_a.lines or not doc_b.lines:
        doc_a = normalize_html_for_diff_full_page(archived_html)
        doc_b = normalize_html_for_diff_full_page(live_html)
        fallback_applied = True
        used_mode = "full"

    extraction = CompareTextExtraction(
        requested_mode=requested_mode,
        used_mode=used_mode,
        fallback_applied=fallback_applied,
    )
    return doc_a, doc_b, extraction


def _build_render_instructions(
    lines_a: list[str],
    lines_b: list[str],
) -> list[CompareRenderInstruction]:
    matcher = difflib.SequenceMatcher(isjunk=None, a=lines_a, b=lines_b, autojunk=False)
    instructions: list[CompareRenderInstruction] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(j2 - j1):
                instructions.append(
                    CompareRenderInstruction(type="unchanged", line_index_b=j1 + offset)
                )
        elif tag == "delete":
            for offset in range(i2 - i1):
                instructions.append(
                    CompareRenderInstruction(type="removed", line_index_a=i1 + offset)
                )
        elif tag == "insert":
            for offset in range(j2 - j1):
                instructions.append(
                    CompareRenderInstruction(type="added", line_index_b=j1 + offset)
                )
        elif tag == "replace":
            removed_count = i2 - i1
            added_count = j2 - j1
            paired = min(removed_count, added_count)
            for offset in range(paired):
                instructions.append(
                    CompareRenderInstruction(
                        type="replace",
                        line_index_a=i1 + offset,
                        line_index_b=j1 + offset,
                    )
                )
            for offset in range(paired, removed_count):
                instructions.append(
                    CompareRenderInstruction(type="removed", line_index_a=i1 + offset)
                )
            for offset in range(paired, added_count):
                instructions.append(
                    CompareRenderInstruction(type="added", line_index_b=j1 + offset)
                )
    return instructions


def build_compare_render_payload(
    doc_a: DiffDocument,
    doc_b: DiffDocument,
    *,
    max_lines: int,
) -> CompareRenderPayload:
    archived_lines = doc_a.lines
    live_lines = doc_b.lines
    render_truncated = False

    if len(archived_lines) > max_lines or len(live_lines) > max_lines:
        render_truncated = True
        archived_lines = archived_lines[:max_lines]
        live_lines = live_lines[:max_lines]

    render_instructions = _build_render_instructions(archived_lines, live_lines)

    return CompareRenderPayload(
        archived_lines=archived_lines,
        live_lines=live_lines,
        render_instructions=render_instructions,
        render_truncated=render_truncated,
        render_line_limit=max_lines,
    )


def compute_live_compare(archived_html: str, live_html: str) -> CompareResult:
    doc_a, doc_b, _extraction = build_compare_documents(archived_html, live_html)
    return compute_live_compare_from_docs(doc_a, doc_b)


def compute_live_compare_from_docs(doc_a: DiffDocument, doc_b: DiffDocument) -> CompareResult:
    diff = compute_diff(doc_a, doc_b)

    section_map_a = {title: text for title, text in doc_a.sections}
    section_map_b = {title: text for title, text in doc_b.sections}
    added_sections, removed_sections, changed_sections = _compute_section_stats(
        section_map_a, section_map_b
    )

    high_noise = diff.change_ratio >= 0.6 or (
        len(doc_b.lines) > 0
        and (diff.added_lines + diff.removed_lines) / max(len(doc_b.lines), 1) > 0.7
    )

    stats = CompareStats(
        added_sections=added_sections,
        removed_sections=removed_sections,
        changed_sections=changed_sections,
        added_lines=diff.added_lines,
        removed_lines=diff.removed_lines,
        change_ratio=diff.change_ratio,
        high_noise=high_noise,
    )

    return CompareResult(
        diff_html=diff.diff_html,
        diff_truncated=diff.diff_truncated,
        diff_version=DIFF_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        stats=stats,
    )


def summarize_live_compare(stats: CompareStats) -> str:
    parts: list[str] = []
    if stats.changed_sections:
        parts.append(f"{stats.changed_sections} sections changed")
    if stats.added_sections:
        parts.append(f"{stats.added_sections} added")
    if stats.removed_sections:
        parts.append(f"{stats.removed_sections} removed")

    if not parts and (stats.added_lines or stats.removed_lines):
        parts.append(f"{stats.added_lines} lines added; {stats.removed_lines} removed")

    if not parts:
        parts.append("Archived text updated")

    summary = "; ".join(parts)
    if stats.high_noise:
        summary = f"{summary} (high-noise change)"
    return summary
