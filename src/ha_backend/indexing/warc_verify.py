from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from warcio.archiveiterator import ArchiveIterator

from ha_backend.infra_errors import is_storage_infra_error


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class WarcVerificationOptions:
    level: int
    max_decompressed_bytes: int | None = None
    max_records: int | None = None


@dataclass
class WarcFileVerification:
    path: str
    ok: bool
    error_kind: str | None = None
    error: str | None = None
    size_bytes: int | None = None
    mtime_epoch_seconds: int | None = None
    checked_at_utc: str | None = None

    # Level 1 gzip checks (only for *.gz).
    gzip_ok: bool | None = None
    gzip_complete: bool | None = None
    gzip_decompressed_bytes: int | None = None

    # Level 2 WARC parseability.
    warc_ok: bool | None = None
    warc_records: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": bool(self.ok),
            "errorKind": self.error_kind,
            "error": self.error,
            "sizeBytes": self.size_bytes,
            "mtimeEpochSeconds": self.mtime_epoch_seconds,
            "checkedAtUtc": self.checked_at_utc,
            "gzipOk": self.gzip_ok,
            "gzipComplete": self.gzip_complete,
            "gzipDecompressedBytes": self.gzip_decompressed_bytes,
            "warcOk": self.warc_ok,
            "warcRecords": self.warc_records,
        }


@dataclass
class WarcVerificationReport:
    started_at_utc: str
    finished_at_utc: str | None = None
    options: WarcVerificationOptions | None = None
    warcs_total: int = 0
    warcs_checked: int = 0
    warcs_ok: int = 0
    warcs_failed: int = 0
    failures: list[WarcFileVerification] = field(default_factory=list)
    results: list[WarcFileVerification] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "startedAtUtc": self.started_at_utc,
            "finishedAtUtc": self.finished_at_utc,
            "options": self.options.__dict__ if self.options is not None else None,
            "warcsTotal": int(self.warcs_total),
            "warcsChecked": int(self.warcs_checked),
            "warcsOk": int(self.warcs_ok),
            "warcsFailed": int(self.warcs_failed),
            "failures": [f.to_dict() for f in self.failures],
            "results": [r.to_dict() for r in self.results],
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def _classify_error(exc: BaseException) -> str:
    if is_storage_infra_error(exc):
        return "infra_error"
    return "corrupt_or_unreadable"


def _iter_records_for_warc(path: Path) -> Iterator[Any]:
    with path.open("rb") as f:
        yield from ArchiveIterator(f)


def _drain_stream(stream: object, *, chunk_size: int = 1024 * 64) -> None:
    read = getattr(stream, "read", None)
    if read is None:
        return
    while True:
        chunk = read(chunk_size)
        if not chunk:
            break


def verify_single_warc(path: Path, *, options: WarcVerificationOptions) -> WarcFileVerification:
    checked_at = _dt_to_iso(_utc_now())
    result = WarcFileVerification(path=str(path), ok=False, checked_at_utc=checked_at)

    try:
        st = path.stat()
        result.size_bytes = int(st.st_size)
        result.mtime_epoch_seconds = int(st.st_mtime)
    except Exception as exc:
        result.error_kind = _classify_error(exc)
        result.error = str(exc)
        return result

    try:
        if not path.is_file():
            result.error_kind = "not_a_file"
            result.error = "Path is not a file."
            return result
    except Exception as exc:
        result.error_kind = _classify_error(exc)
        result.error = str(exc)
        return result

    if result.size_bytes is not None and result.size_bytes <= 0:
        result.error_kind = "empty_file"
        result.error = "File size is 0."
        return result

    # Level 0: ensure readable (open + read 1 byte).
    try:
        with path.open("rb") as f:
            f.read(1)
    except Exception as exc:
        result.error_kind = _classify_error(exc)
        result.error = str(exc)
        return result

    if options.level <= 0:
        result.ok = True
        return result

    # Level 1: gzip integrity for *.gz files (stream to EOF to validate CRC/trailer).
    if path.suffix == ".gz":
        result.gzip_ok = False
        decompressed = 0
        complete = True
        try:
            with gzip.open(path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    decompressed += len(chunk)
                    if options.max_decompressed_bytes is not None and decompressed >= int(
                        options.max_decompressed_bytes
                    ):
                        complete = False
                        break
        except Exception as exc:
            result.error_kind = _classify_error(exc)
            result.error = str(exc)
            result.gzip_ok = False
            result.gzip_complete = complete
            result.gzip_decompressed_bytes = decompressed
            return result

        result.gzip_ok = True
        result.gzip_complete = complete
        result.gzip_decompressed_bytes = decompressed

    if options.level <= 1:
        result.ok = True
        return result

    # Level 2: WARC parseability (iterate records; drain bodies without buffering).
    result.warc_ok = False
    record_count = 0
    try:
        for record in _iter_records_for_warc(path):
            record_count += 1
            if options.max_records is not None and record_count >= int(options.max_records):
                break

            try:
                stream = record.content_stream()
            except Exception:
                stream = None
            if stream is not None:
                _drain_stream(stream)
    except Exception as exc:
        result.error_kind = _classify_error(exc)
        result.error = str(exc)
        result.warc_ok = False
        result.warc_records = record_count
        return result

    result.warc_ok = True
    result.warc_records = record_count
    result.ok = True
    return result


def verify_warcs(
    warc_paths: Sequence[Path],
    *,
    options: WarcVerificationOptions,
) -> WarcVerificationReport:
    started = _utc_now()
    report = WarcVerificationReport(started_at_utc=_dt_to_iso(started), options=options)
    report.warcs_total = len(warc_paths)

    for path in warc_paths:
        report.warcs_checked += 1
        res = verify_single_warc(path, options=options)
        report.results.append(res)
        if res.ok:
            report.warcs_ok += 1
        else:
            report.warcs_failed += 1
            report.failures.append(res)

    report.finished_at_utc = _dt_to_iso(_utc_now())
    return report


def filter_warcs_by_mtime(
    warc_paths: Iterable[Path],
    *,
    since_epoch_seconds: int | None = None,
) -> list[Path]:
    paths: list[Path] = []
    cutoff: int | None = int(since_epoch_seconds) if since_epoch_seconds is not None else None
    for path in warc_paths:
        if cutoff is None:
            paths.append(path)
            continue
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            continue
        if mtime >= cutoff:
            paths.append(path)
    return paths


def sort_warcs_by_mtime_desc(warc_paths: Iterable[Path]) -> list[Path]:
    paths = list(warc_paths)

    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except Exception:
            return 0.0

    paths.sort(key=_safe_mtime, reverse=True)
    return paths


def quarantine_warcs(
    warc_paths: Sequence[Path],
    *,
    quarantine_root: Path,
    relative_to: Path | None = None,
) -> list[dict[str, str]]:
    """
    Move WARC files into a quarantine directory.

    Returns a list of dicts (from, to, sha256Before, relativePath, sizeBytes, mtimeEpochSeconds)
    for moved files.
    """
    quarantine_root.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, str]] = []

    base = relative_to.resolve() if relative_to is not None else None

    for path in warc_paths:
        src = path.resolve()
        try:
            st = src.stat()
            size_bytes = str(int(st.st_size))
            mtime_epoch = str(int(st.st_mtime))
        except Exception:
            size_bytes = "0"
            mtime_epoch = "0"

        sha256_before = _sha256_file(src)

        rel = Path(src.name)
        if base is not None:
            try:
                rel = src.relative_to(base)
            except ValueError:
                rel = Path(src.name)

        dest = quarantine_root / rel
        if dest.exists():
            suffix = _utc_now().strftime("%Y%m%dT%H%M%SZ")
            dest = dest.with_name(f"{dest.name}.{suffix}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)

        moved.append(
            {
                "from": str(src),
                "to": str(dest),
                "sha256Before": sha256_before,
                "relativePath": str(rel),
                "sizeBytes": size_bytes,
                "mtimeEpochSeconds": mtime_epoch,
            }
        )

    return moved
