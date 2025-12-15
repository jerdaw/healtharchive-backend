from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ha_backend.authority import recompute_page_signals
from ha_backend.db import get_session
from ha_backend.indexing.mapping import record_to_snapshot
from ha_backend.indexing.text_extraction import (detect_language, extract_text,
                                                 extract_outlink_groups,
                                                 extract_title, make_snippet)
from ha_backend.indexing.warc_discovery import discover_warcs_for_job
from ha_backend.indexing.warc_reader import iter_html_records
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

        if job.source is None:
            raise ValueError(
                f"ArchiveJob {job_id} has no associated Source; cannot index."
            )

        if job.status not in ("completed", "index_failed", "indexed"):
            raise ValueError(
                f"ArchiveJob {job_id} is in status {job.status!r}, "
                "expected one of 'completed', 'index_failed', or 'indexed'."
            )

        output_dir = Path(job.output_dir)
        if not output_dir.is_dir():
            raise ValueError(
                f"ArchiveJob {job_id} output_dir does not exist or is not a directory: {output_dir}"
            )

        # Discover WARC files for this job.
        warc_paths = discover_warcs_for_job(job)
        job.warc_file_count = len(warc_paths)

        if not warc_paths:
            logger.warning(
                "No WARC files discovered for job %s in %s", job_id, output_dir
            )
            job.status = "index_failed"
            return 1

        # Mark job as indexing and clear any prior snapshots for this job to
        # make the operation idempotent.
        logger.info(
            "Starting indexing for job %s (%d WARC file(s))", job_id, len(warc_paths)
        )

        impacted_groups: set[str] = set()
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

        try:
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
                        if use_postgres_fts:
                            from ha_backend.search import build_search_vector

                            snapshot.search_vector = build_search_vector(
                                title,
                                snippet,
                                rec.url,
                            )

                            if has_outlinks and rec.status_code is not None and 200 <= rec.status_code < 300:
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

            if use_authority and impacted_groups:
                session.flush()
                recompute_page_signals(session, groups=tuple(impacted_groups))

            logger.info(
                "Indexing for job %s completed successfully with %d snapshot(s).",
                job_id,
                n_snapshots,
            )
            return 0
        except Exception as exc:
            logger.error("Indexing for job %s failed: %s", job_id, exc)
            job.status = "index_failed"
            return 1


__all__ = ["index_job"]
