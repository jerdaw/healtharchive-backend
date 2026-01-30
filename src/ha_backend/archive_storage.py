from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from archive_tool.constants import STATE_FILE_NAME
from archive_tool.utils import find_latest_config_yaml

STABLE_WARCS_DIRNAME = "warcs"
WARC_MANIFEST_FILENAME = "manifest.json"
PROVENANCE_DIRNAME = "provenance"

_WARC_NAME_RE = re.compile(r"^warc-(\d+)\.(?:warc(?:\.gz)?)$")


@dataclass(frozen=True)
class WarcManifestEntry:
    source_path: str
    stable_name: str
    link_type: str
    size_bytes: int
    sha256: str


@dataclass
class ManifestVerificationResult:
    """Result of verifying a WARC manifest against actual files."""

    valid: bool
    manifest_path: Path
    entries_total: int
    entries_verified: int
    missing: list[str]  # stable_name of missing files
    size_mismatches: list[tuple[str, int, int]]  # (stable_name, expected, actual)
    hash_mismatches: list[tuple[str, str, str]]  # (stable_name, expected, actual)
    orphaned: list[str]  # files in warcs/ but not in manifest
    errors: list[str]  # other errors encountered


@dataclass(frozen=True)
class WarcConsolidationResult:
    warcs_dir: Path
    manifest_path: Path
    stable_warcs: list[Path]
    created: int
    reused: int


@dataclass(frozen=True)
class JobStorageStats:
    output_dir: Path
    warc_file_count: int
    warc_bytes_total: int
    output_bytes_total: int
    tmp_bytes_total: int
    tmp_non_warc_bytes_total: int
    scanned_at: datetime


def get_job_warcs_dir(output_dir: Path) -> Path:
    return output_dir / STABLE_WARCS_DIRNAME


def get_job_provenance_dir(output_dir: Path) -> Path:
    return output_dir / PROVENANCE_DIRNAME


def get_job_warc_manifest_path(output_dir: Path) -> Path:
    return get_job_warcs_dir(output_dir) / WARC_MANIFEST_FILENAME


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _load_manifest(manifest_path: Path) -> dict:
    if not manifest_path.is_file():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _dump_manifest(manifest_path: Path, data: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = manifest_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, manifest_path)


def _iter_stable_warc_paths(warcs_dir: Path) -> list[Path]:
    if not warcs_dir.is_dir():
        return []
    warcs: set[Path] = set()
    for ext in (".warc.gz", ".warc"):
        for path in warcs_dir.rglob(f"*{ext}"):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    warcs.add(path.resolve())
            except OSError:
                continue
    return sorted(warcs)


def _compute_sha256(path: Path) -> str:
    """Compute the SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def _next_warc_index(existing_names: Iterable[str]) -> int:
    max_idx = 0
    for name in existing_names:
        match = _WARC_NAME_RE.match(name)
        if not match:
            continue
        try:
            max_idx = max(max_idx, int(match.group(1)))
        except ValueError:
            continue
    return max_idx + 1


def _safe_link_or_copy(
    src: Path,
    dest: Path,
    *,
    allow_copy_fallback: bool,
) -> str:
    """
    Create a stable WARC file at dest that is byte-identical to src.

    Prefers hardlinks for zero-disk-overhead deduplication; optionally falls back
    to a copy when hardlinking is not possible (e.g. cross-device).
    """
    if dest.exists():
        src_stat = src.stat()
        dest_stat = dest.stat()
        if (src_stat.st_dev, src_stat.st_ino) == (dest_stat.st_dev, dest_stat.st_ino):
            return "hardlink"
        # Existing file but not a hardlink; keep conservative.
        raise FileExistsError(
            f"Refusing to overwrite existing stable WARC {dest} that does not match source inode {src}"
        )

    try:
        os.link(src, dest)
        return "hardlink"
    except OSError as exc:
        if exc.errno != errno.EXDEV or not allow_copy_fallback:
            raise

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=str(dest.parent),
        prefix=f".{dest.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp_path = Path(tmp.name)
        with src.open("rb") as fsrc:
            shutil.copyfileobj(fsrc, tmp)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp_path, dest)
    shutil.copystat(src, dest, follow_symlinks=True)
    return "copy"


def consolidate_warcs(
    *,
    output_dir: Path,
    source_warc_paths: list[Path],
    allow_copy_fallback: bool = False,
    dry_run: bool = False,
) -> WarcConsolidationResult:
    """
    Consolidate discovered WARC files into a stable per-job warcs/ directory.

    This is intended to decouple the long-lived archive artifacts (WARCs) from
    `.tmp*` crawl working directories so operators can safely delete temp state
    without breaking replay or snapshot viewing.

    Behavior:
    - Creates hardlinks by default (no extra disk usage).
    - Does NOT delete the source WARCs; cleanup is a separate explicit action.
    - Writes/updates a manifest mapping source paths -> stable filenames.
    """
    output_dir = output_dir.resolve()
    warcs_dir = get_job_warcs_dir(output_dir)
    manifest_path = get_job_warc_manifest_path(output_dir)

    manifest = _load_manifest(manifest_path)
    entries: list[dict] = list(manifest.get("entries") or [])
    by_source: dict[str, dict] = {
        str(Path(e.get("source_path", "")).resolve()): e for e in entries if e.get("source_path")
    }

    existing_stable: set[str] = {p.name for p in _iter_stable_warc_paths(warcs_dir)}
    existing_stable.update({str(e.get("stable_name")) for e in entries if e.get("stable_name")})
    next_idx = _next_warc_index(existing_stable)

    created = 0
    reused = 0
    stable_paths: list[Path] = []

    if not dry_run:
        warcs_dir.mkdir(parents=True, exist_ok=True)

    for src in sorted({p.resolve() for p in source_warc_paths}):
        if not src.is_file():
            continue

        source_key = str(src)
        existing = by_source.get(source_key)
        stable_name = (
            str(existing.get("stable_name")) if existing and existing.get("stable_name") else ""
        )
        if stable_name:
            dest = warcs_dir / stable_name
            if dest.exists() and not dest.is_file():
                raise FileExistsError(
                    f"Refusing to use stable WARC path {dest} because it exists and is not a file."
                )
            if dest.is_file():
                reused += 1
                stable_paths.append(dest.resolve())
                by_source[source_key] = {
                    **(existing or {}),
                    "source_path": source_key,
                    "stable_name": stable_name,
                    "size_bytes": int(src.stat().st_size),
                }
                continue
        else:
            stable_name = f"warc-{next_idx:06d}{''.join(src.suffixes) or src.suffix}"
            next_idx += 1
            dest = warcs_dir / stable_name

        link_type = "dry_run"
        sha256_hash = "dry_run"
        if not dry_run:
            link_type = _safe_link_or_copy(
                src,
                dest,
                allow_copy_fallback=allow_copy_fallback,
            )
            sha256_hash = _compute_sha256(dest)

        created += 1
        stable_paths.append(dest.resolve())
        by_source[source_key] = {
            **(existing or {}),
            "source_path": source_key,
            "stable_name": stable_name,
            "link_type": link_type,
            "size_bytes": int(src.stat().st_size),
            "sha256": sha256_hash,
        }

    def _entry_sort_key(entry: dict) -> tuple[int, int, str]:
        stable = str(entry.get("stable_name") or "")
        match = _WARC_NAME_RE.match(stable)
        if match:
            try:
                return (0, int(match.group(1)), stable)
            except ValueError:
                return (0, 0, stable)
        return (1, 0, stable)

    now = _now_utc().isoformat()
    out_manifest = {
        "version": 1,
        "output_dir": str(output_dir),
        "warcs_dir": str(warcs_dir),
        "created_at": manifest.get("created_at") or now,
        "updated_at": now,
        "entries": sorted(by_source.values(), key=_entry_sort_key),
    }

    if not dry_run:
        _dump_manifest(manifest_path, out_manifest)

    return WarcConsolidationResult(
        warcs_dir=warcs_dir,
        manifest_path=manifest_path,
        stable_warcs=sorted(stable_paths),
        created=created,
        reused=reused,
    )


def load_warc_manifest(output_dir: Path) -> dict:
    """
    Load the per-job WARC consolidation manifest (if present).
    """
    output_dir = output_dir.resolve()
    return _load_manifest(get_job_warc_manifest_path(output_dir))


def build_warc_path_mapping(output_dir: Path) -> dict[str, str]:
    """
    Return a mapping of source WARC absolute paths -> stable WARC absolute paths.

    The mapping is derived from the job's `warcs/manifest.json`.
    """
    output_dir = output_dir.resolve()
    warcs_dir = get_job_warcs_dir(output_dir)
    manifest = load_warc_manifest(output_dir)
    mapping: dict[str, str] = {}
    for entry in manifest.get("entries") or []:
        src = entry.get("source_path")
        stable_name = entry.get("stable_name")
        if not src or not stable_name:
            continue
        mapping[str(Path(src).resolve())] = str((warcs_dir / stable_name).resolve())
    return mapping


def compute_tree_bytes(path: Path) -> int:
    """
    Compute physical bytes used under a path, de-duplicating hardlinks (inode-based).
    """
    seen: set[tuple[int, int]] = set()
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            file_path = Path(root) / name
            try:
                st = file_path.stat()
            except OSError:
                continue
            key = (int(st.st_dev), int(st.st_ino))
            if key in seen:
                continue
            seen.add(key)
            total += int(st.st_size)
    return total


def compute_job_storage_stats(
    *,
    output_dir: Path,
    temp_dirs: list[Path],
    stable_warc_paths: list[Path],
    scanned_at: Optional[datetime] = None,
) -> JobStorageStats:
    output_dir = output_dir.resolve()
    scanned_at = scanned_at or _now_utc()

    warc_seen: set[tuple[int, int]] = set()
    warc_bytes_total = 0
    for warc in stable_warc_paths:
        try:
            st = warc.stat()
        except OSError:
            continue
        key = (int(st.st_dev), int(st.st_ino))
        if key in warc_seen:
            continue
        warc_seen.add(key)
        warc_bytes_total += int(st.st_size)

    output_bytes_total = compute_tree_bytes(output_dir)

    tmp_bytes_total = 0
    tmp_non_warc_bytes_total = 0
    warc_suffixes = (".warc", ".warc.gz")
    for temp_dir in temp_dirs:
        if not temp_dir.is_dir():
            continue
        tmp_bytes_total += compute_tree_bytes(temp_dir)

        non_warc_seen: set[tuple[int, int]] = set()
        non_warc_total = 0
        for root, _dirs, files in os.walk(temp_dir, followlinks=False):
            for name in files:
                if name.endswith(warc_suffixes):
                    continue
                file_path = Path(root) / name
                try:
                    st = file_path.stat()
                except OSError:
                    continue
                key = (int(st.st_dev), int(st.st_ino))
                if key in non_warc_seen:
                    continue
                non_warc_seen.add(key)
                non_warc_total += int(st.st_size)
        tmp_non_warc_bytes_total += non_warc_total

    return JobStorageStats(
        output_dir=output_dir,
        warc_file_count=len(stable_warc_paths),
        warc_bytes_total=warc_bytes_total,
        output_bytes_total=output_bytes_total,
        tmp_bytes_total=tmp_bytes_total,
        tmp_non_warc_bytes_total=tmp_non_warc_bytes_total,
        scanned_at=scanned_at,
    )


def snapshot_state_file(output_dir: Path, *, dest_dir: Path, dry_run: bool = False) -> Path | None:
    """
    Preserve `.archive_state.json` under the job's provenance directory.

    Returns the destination path if the file existed (even in dry-run), else None.
    """
    output_dir = output_dir.resolve()
    state_path = output_dir / STATE_FILE_NAME
    if not state_path.is_file():
        return None

    dest_path = dest_dir / "archive_state.json"
    if dry_run:
        return dest_path

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(state_path, dest_path)
    return dest_path


def snapshot_crawl_configs(
    temp_dirs: list[Path],
    *,
    output_dir: Path,
    dest_dir: Path,
    dry_run: bool = False,
) -> list[Path]:
    """
    Preserve crawl configuration YAMLs from `.tmp*` directories into provenance.

    Copies all `collections/crawl-*/crawls/*.yaml` files under each temp dir,
    preserving a stable relative layout under:

      <output_dir>/provenance/crawl_configs/<temp_dir_name>/...
    """
    output_dir = output_dir.resolve()
    dest_dir = dest_dir.resolve()
    copied: list[Path] = []

    if not temp_dirs:
        return copied

    for temp_dir in temp_dirs:
        if not temp_dir.is_dir():
            continue

        # Best-effort: ensure we at least grab the latest crawl YAML for each temp dir.
        latest = find_latest_config_yaml(temp_dir)
        yaml_paths: set[Path] = set()
        if latest is not None:
            yaml_paths.add(latest)

        for yaml_path in temp_dir.glob("collections/crawl-*/crawls/*.yaml"):
            if yaml_path.is_file():
                yaml_paths.add(yaml_path)

        for yaml_path in sorted(yaml_paths):
            try:
                rel = yaml_path.resolve().relative_to(temp_dir.resolve())
            except Exception:
                rel = Path(yaml_path.name)

            dest_path = dest_dir / "crawl_configs" / temp_dir.name / rel
            copied.append(dest_path)
            if dry_run:
                continue

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(yaml_path, dest_path)

    return copied


def verify_warc_manifest(
    output_dir: Path,
    *,
    check_size: bool = True,
    check_hash: bool = False,
) -> ManifestVerificationResult:
    """
    Verify the WARC consolidation manifest against actual files on disk.

    This checks:
    1. Manifest exists and is valid JSON
    2. All entries in manifest have corresponding files on disk
    3. Size matches (if check_size=True)
    4. SHA256 matches (if check_hash=True)
    5. No orphaned WARCs in warcs/ not in manifest (warning only)

    Args:
        output_dir: Job output directory containing warcs/manifest.json
        check_size: Whether to verify file sizes match manifest
        check_hash: Whether to verify SHA256 hashes match manifest (slow)

    Returns:
        ManifestVerificationResult with verification details
    """
    output_dir = output_dir.resolve()
    warcs_dir = get_job_warcs_dir(output_dir)
    manifest_path = get_job_warc_manifest_path(output_dir)

    missing: list[str] = []
    size_mismatches: list[tuple[str, int, int]] = []
    hash_mismatches: list[tuple[str, str, str]] = []
    orphaned: list[str] = []
    errors: list[str] = []

    # Check manifest exists
    if not manifest_path.is_file():
        return ManifestVerificationResult(
            valid=False,
            manifest_path=manifest_path,
            entries_total=0,
            entries_verified=0,
            missing=[],
            size_mismatches=[],
            hash_mismatches=[],
            orphaned=[],
            errors=[f"Manifest not found: {manifest_path}"],
        )

    # Load and parse manifest
    manifest = _load_manifest(manifest_path)
    if not manifest:
        return ManifestVerificationResult(
            valid=False,
            manifest_path=manifest_path,
            entries_total=0,
            entries_verified=0,
            missing=[],
            size_mismatches=[],
            hash_mismatches=[],
            orphaned=[],
            errors=[f"Manifest is empty or invalid JSON: {manifest_path}"],
        )

    entries = manifest.get("entries") or []
    entries_total = len(entries)
    entries_verified = 0
    manifest_stable_names: set[str] = set()

    for entry in entries:
        stable_name = entry.get("stable_name")
        if not stable_name:
            errors.append(f"Entry missing stable_name: {entry}")
            continue

        manifest_stable_names.add(stable_name)
        warc_path = warcs_dir / stable_name

        # Check file exists
        try:
            if not warc_path.is_file():
                missing.append(stable_name)
                continue
        except OSError as exc:
            errors.append(f"OSError checking {stable_name}: {exc}")
            continue

        # Check size
        if check_size:
            expected_size = entry.get("size_bytes")
            if expected_size is not None:
                try:
                    actual_size = warc_path.stat().st_size
                    if actual_size != expected_size:
                        size_mismatches.append((stable_name, expected_size, actual_size))
                        continue
                except OSError as exc:
                    errors.append(f"OSError stating {stable_name}: {exc}")
                    continue

        # Check hash
        if check_hash:
            expected_hash = entry.get("sha256")
            if expected_hash:
                try:
                    actual_hash = _compute_sha256(warc_path)
                    if actual_hash != expected_hash:
                        hash_mismatches.append((stable_name, expected_hash, actual_hash))
                        continue
                except OSError as exc:
                    errors.append(f"OSError hashing {stable_name}: {exc}")
                    continue

        entries_verified += 1

    # Check for orphaned files
    try:
        actual_warcs = _iter_stable_warc_paths(warcs_dir)
        for warc_path in actual_warcs:
            if warc_path.name not in manifest_stable_names:
                orphaned.append(warc_path.name)
    except OSError as exc:
        errors.append(f"OSError scanning warcs directory: {exc}")

    valid = (
        len(missing) == 0
        and len(size_mismatches) == 0
        and len(hash_mismatches) == 0
        and len(errors) == 0
    )

    return ManifestVerificationResult(
        valid=valid,
        manifest_path=manifest_path,
        entries_total=entries_total,
        entries_verified=entries_verified,
        missing=missing,
        size_mismatches=size_mismatches,
        hash_mismatches=hash_mismatches,
        orphaned=orphaned,
        errors=errors,
    )
