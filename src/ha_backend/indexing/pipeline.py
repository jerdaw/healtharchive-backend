from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ha_backend.archive_storage import (
    compute_job_storage_stats,
    consolidate_warcs,
    get_job_warcs_dir,
)
from ha_backend.authority import recompute_page_signals
from ha_backend.db import get_session
from ha_backend.indexing.mapping import record_to_snapshot
from ha_backend.indexing.text_extraction import (
    detect_language,
    extract_outlink_groups,
    extract_text,
    extract_title,
    make_snippet,
)
from ha_backend.indexing.warc_discovery import discover_temp_warcs_for_job, discover_warcs_for_job
from ha_backend.indexing.warc_reader import iter_html_records
from ha_backend.indexing.warc_verify import WarcVerificationOptions, verify_warcs
from ha_backend.infra_errors import is_storage_infra_errno
from ha_backend.models import ArchiveJob, Snapshot, SnapshotOutlink

logger = logging.getLogger("healtharchive.indexing")


def _load_job(session: Session, job_id: int) -> ArchiveJob:
    job = session.get(ArchiveJob, job_id)
    if job is None:
        raise ValueError(f"ArchiveJob with id={job_id} does not exist.")
    return job


def index_job(job_id: int) -> int:
    """
    Index a completed ArchiveJob into Snapshot rows.

    Returns:
        0 on success, non-zero on failure.
    """
    with get_session() as session:
        job = _load_job(session, job_id)
        use_postgres_fts = session.get_bind().dialect.name == "postgresql"

        inspector = inspect(session.get_bind())
        has_outlinks = inspector.has_table("snapshot_outlinks")
        has_page_signals = inspector.has_table("page_signals")
        use_authority = has_outlinks and has_page_signals
        has_pages = inspector.has_table("pages")

        if job.source is None:
            raise ValueError(f"ArchiveJob {job_id} has no associated Source; cannot index.")

        if job.status not in ("completed", "index_failed", "indexed"):
            raise ValueError(
                f"ArchiveJob {job_id} is in status {job.status!r}, "
                "expected one of 'completed', 'index_failed', or 'indexed'."
            )

        output_dir = Path(job.output_dir)
        try:
            st = output_dir.stat()
        except OSError as exc:
            if is_storage_infra_errno(exc.errno):
                logger.error(
                    "ArchiveJob %s output_dir is not readable due to storage infra error: %s",
                    job_id,
                    exc,
                )
            else:
                logger.error("ArchiveJob %s output_dir stat failed: %s", job_id, exc)
            job.status = "index_failed"
            return 1
        if not stat.S_ISDIR(st.st_mode):
            logger.error(
                "ArchiveJob %s output_dir does not exist or is not a directory: %s",
                job_id,
                output_dir,
            )
            job.status = "index_failed"
            return 1

        try:
            # Prefer indexing from stable per-job WARCs when possible.
            #
            # If the job only has legacy WARCs under `.tmp*`, consolidate them into
            # `<output_dir>/warcs/` first (via hardlink) so operators can later
            # safely delete `.tmp*` without breaking replay or snapshot viewing.
            output_dir = output_dir.resolve()
            stable_warcs_dir = get_job_warcs_dir(output_dir)
            stable_present = stable_warcs_dir.is_dir() and (
                any(stable_warcs_dir.rglob("*.warc.gz")) or any(stable_warcs_dir.rglob("*.warc"))
            )
            if not stable_present:
                temp_warcs = discover_temp_warcs_for_job(job)
                if temp_warcs:
                    try:
                        result = consolidate_warcs(
                            output_dir=output_dir,
                            source_warc_paths=temp_warcs,
                            allow_copy_fallback=False,
                            dry_run=False,
                        )
                        logger.info(
                            "Consolidated %d WARC(s) into %s (created=%d reused=%d).",
                            len(result.stable_warcs),
                            result.warcs_dir,
                            result.created,
                            result.reused,
                        )
                    except Exception as exc:
                        logger.warning(
                            "WARC consolidation failed for job %s; continuing with legacy `.tmp*` WARCs: %s",
                            job_id,
                            exc,
                        )

            # Discover WARC files for this job.
            warc_paths = discover_warcs_for_job(job)
            job.warc_file_count = len(warc_paths)

            if not warc_paths:
                logger.warning("No WARC files discovered for job %s in %s", job_id, output_dir)
                job.status = "index_failed"
                return 1

            # Phase 4 safety rail: ensure we can at least stat + open/read every
            # discovered WARC before we delete any existing snapshots.
            #
            # Level 0 is always on (cheap). Operators can opt into deeper checks
            # via env vars if they want to gate indexing on gzip/WARC integrity.
            verify_level_raw = os.environ.get("HEALTHARCHIVE_INDEX_WARC_VERIFY_LEVEL", "0")
            try:
                verify_level = int(verify_level_raw)
            except Exception:
                verify_level = 0
            if verify_level not in (0, 1, 2):
                logger.warning(
                    "Invalid HEALTHARCHIVE_INDEX_WARC_VERIFY_LEVEL=%r; expected 0/1/2; using 0.",
                    verify_level_raw,
                )
                verify_level = 0

            verify_max_decompressed_bytes = os.environ.get(
                "HEALTHARCHIVE_INDEX_WARC_VERIFY_MAX_DECOMPRESSED_BYTES"
            )
            max_decompressed_bytes: int | None = None
            if verify_max_decompressed_bytes:
                try:
                    max_decompressed_bytes = int(verify_max_decompressed_bytes)
                    if max_decompressed_bytes <= 0:
                        max_decompressed_bytes = None
                except Exception:
                    max_decompressed_bytes = None

            verify_max_records = os.environ.get("HEALTHARCHIVE_INDEX_WARC_VERIFY_MAX_RECORDS")
            max_records: int | None = None
            if verify_max_records:
                try:
                    max_records = int(verify_max_records)
                    if max_records <= 0:
                        max_records = None
                except Exception:
                    max_records = None

            verify_options = WarcVerificationOptions(
                level=verify_level,
                max_decompressed_bytes=max_decompressed_bytes,
                max_records=max_records,
            )
            verify_report = verify_warcs(warc_paths, options=verify_options)
            if verify_report.warcs_failed:
                sample = ", ".join(f.path for f in verify_report.failures[:3])
                logger.error(
                    "Pre-index WARC verification failed for job %s (level=%s): failed=%d/%d sample=%s",
                    job_id,
                    verify_level,
                    verify_report.warcs_failed,
                    verify_report.warcs_checked,
                    sample,
                )
                job.status = "index_failed"
                return 1

            # Mark job as indexing and clear any prior snapshots for this job to
            # make the operation idempotent.
            logger.info("Starting indexing for job %s (%d WARC file(s))", job_id, len(warc_paths))

            impacted_groups: set[str] = set()
            impacted_page_groups: set[str] = set()
            if has_pages:
                from ha_backend.pages import discover_job_page_groups

                impacted_page_groups.update(discover_job_page_groups(session, job_id=job.id))

            if has_outlinks:
                # Capture the set of groups affected by removing the old outlinks,
                # so PageSignal counts can be kept in sync after re-indexing.
                existing_groups = (
                    session.query(SnapshotOutlink.to_normalized_url_group)
                    .join(Snapshot, Snapshot.id == SnapshotOutlink.snapshot_id)
                    .filter(Snapshot.job_id == job.id)
                    .distinct()
                    .all()
                )
                impacted_groups.update({g for (g,) in existing_groups if g})

                snapshot_ids_subq = session.query(Snapshot.id).filter(Snapshot.job_id == job.id)
                session.query(SnapshotOutlink).filter(
                    SnapshotOutlink.snapshot_id.in_(snapshot_ids_subq)
                ).delete(synchronize_session=False)

            session.query(Snapshot).filter(Snapshot.job_id == job.id).delete(
                synchronize_session=False
            )
            job.indexed_page_count = 0
            job.status = "indexing"

            n_snapshots = 0

            for warc_path in warc_paths:
                for rec in iter_html_records(warc_path):
                    try:
                        # Decode bytes to text; prefer UTF-8 with replacement for robustness.
                        html = rec.body_bytes.decode("utf-8", errors="replace")
                        title = extract_title(html)
                        text = extract_text(html)
                        snippet = make_snippet(text)
                        language = detect_language(text, rec.headers)

                        snapshot = record_to_snapshot(
                            job=job,
                            source=job.source,
                            rec=rec,
                            title=title,
                            snippet=snippet,
                            language=language,
                        )

                        if has_pages:
                            group_key = snapshot.normalized_url_group
                            if not group_key:
                                group_key = rec.url.split("#", 1)[0].split("?", 1)[0]
                            if group_key:
                                impacted_page_groups.add(group_key)

                        if use_postgres_fts:
                            from ha_backend.search import build_search_vector

                            snapshot.search_vector = build_search_vector(
                                title,
                                snippet,
                                rec.url,
                            )

                        if (
                            has_outlinks
                            and rec.status_code is not None
                            and 200 <= rec.status_code < 300
                        ):
                            outlink_groups = extract_outlink_groups(
                                html,
                                base_url=rec.url,
                                from_group=snapshot.normalized_url_group,
                            )
                            if snapshot.normalized_url_group:
                                impacted_groups.add(snapshot.normalized_url_group)
                            for group in outlink_groups:
                                snapshot.outlinks.append(
                                    SnapshotOutlink(to_normalized_url_group=group)
                                )
                            impacted_groups.update(outlink_groups)

                        session.add(snapshot)
                        n_snapshots += 1

                        # Flush periodically to keep memory usage reasonable.
                        if n_snapshots % 500 == 0:
                            session.flush()
                    except Exception as rec_exc:
                        logger.warning(
                            "Skipping record in %s due to parse error: %s",
                            warc_path,
                            rec_exc,
                        )
                        continue

            job.indexed_page_count = n_snapshots
            job.status = "indexed"

            if has_pages and impacted_page_groups:
                from ha_backend.pages import rebuild_pages

                session.flush()
                pages_result = rebuild_pages(
                    session,
                    source_id=job.source_id,
                    groups=tuple(sorted(impacted_page_groups)),
                    delete_missing=True,
                )
                logger.info(
                    "Rebuilt %d page group(s) (deleted %d) for job %s.",
                    pages_result.upserted_groups,
                    pages_result.deleted_groups,
                    job_id,
                )

            if use_authority and impacted_groups:
                session.flush()
                recompute_page_signals(session, groups=tuple(impacted_groups))

            # Best-effort storage accounting (metadata-only; no content reads).
            try:
                temp_dirs = sorted([p for p in output_dir.glob(".tmp*") if p.is_dir()])
                stats = compute_job_storage_stats(
                    output_dir=output_dir,
                    temp_dirs=temp_dirs,
                    stable_warc_paths=warc_paths,
                )
                job.warc_bytes_total = stats.warc_bytes_total
                job.output_bytes_total = stats.output_bytes_total
                job.tmp_bytes_total = stats.tmp_bytes_total
                job.tmp_non_warc_bytes_total = stats.tmp_non_warc_bytes_total
                job.storage_scanned_at = stats.scanned_at
            except Exception as exc:
                logger.warning("Failed to compute storage stats for job %s: %s", job_id, exc)

            logger.info(
                "Indexing for job %s completed successfully with %d snapshot(s).",
                job_id,
                n_snapshots,
            )
            return 0
        except OSError as exc:
            if is_storage_infra_errno(exc.errno):
                logger.error(
                    "Indexing for job %s failed due to storage infra error: %s", job_id, exc
                )
            else:
                logger.error("Indexing for job %s failed due to OS error: %s", job_id, exc)
            job.status = "index_failed"
            return 1
        except Exception as exc:
            logger.error("Indexing for job %s failed: %s", job_id, exc)
            job.status = "index_failed"
            return 1


__all__ = ["index_job"]
