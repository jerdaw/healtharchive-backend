from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship
from sqlalchemy.sql import func

from .db import Base


class TimestampMixin:
    """
    Common created_at / updated_at columns.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Source(TimestampMixin, Base):
    """
    Logical content source (e.g., Health Canada, PHAC).
    """

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(1000))
    description: Mapped[Optional[str]] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("1"),
    )

    jobs: Mapped[List["ArchiveJob"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
    )
    snapshots: Mapped[List["Snapshot"]] = relationship(
        back_populates="source",
    )

    def __repr__(self) -> str:
        return f"<Source id={self.id!r} code={self.code!r}>"


class ArchiveJob(TimestampMixin, Base):
    """
    Persistent representation of a single archive_tool run.
    """

    __tablename__ = "archive_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id"),
        nullable=True,
        index=True,
    )

    # Must match the --name passed to archive_tool.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Host path used as --output-dir for archive_tool.
    output_dir: Mapped[str] = mapped_column(String(1000), nullable=False)

    # High-level lifecycle status (queued, running, completed, failed, etc.).
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'queued'"),
        index=True,
    )

    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    # Opaque configuration blob used to reconstruct the archive_tool command.
    config: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    crawler_exit_code: Mapped[Optional[int]] = mapped_column(Integer)
    crawler_status: Mapped[Optional[str]] = mapped_column(String(50))
    crawler_stage: Mapped[Optional[str]] = mapped_column(String(255))
    last_stats_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON)

    pages_crawled: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    pages_total: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    pages_failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    warc_file_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    warc_bytes_total: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    indexed_page_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    output_bytes_total: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    tmp_bytes_total: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    tmp_non_warc_bytes_total: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("0"),
    )
    storage_scanned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )

    cleanup_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'none'"),
    )
    cleaned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    final_zim_path: Mapped[Optional[str]] = mapped_column(String(1000))
    combined_log_path: Mapped[Optional[str]] = mapped_column(String(1000))
    state_file_path: Mapped[Optional[str]] = mapped_column(String(1000))

    source: Mapped[Optional[Source]] = relationship(back_populates="jobs")
    snapshots: Mapped[List["Snapshot"]] = relationship(back_populates="job")

    def __repr__(self) -> str:
        return f"<ArchiveJob id={self.id!r} name={self.name!r} status={self.status!r}>"


class Snapshot(TimestampMixin, Base):
    """
    Individual captured page snapshot, derived from WARCs.
    """

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("archive_jobs.id"),
        nullable=True,
        index=True,
    )
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id"),
        nullable=True,
        index=True,
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url_group: Mapped[Optional[str]] = mapped_column(Text)

    capture_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    mime_type: Mapped[Optional[str]] = mapped_column(String(255))
    status_code: Mapped[Optional[int]] = mapped_column(Integer)

    title: Mapped[Optional[str]] = mapped_column(Text)
    snippet: Mapped[Optional[str]] = mapped_column(Text)
    language: Mapped[Optional[str]] = mapped_column(String(16))

    # Postgres FTS tsvector (falls back to TEXT on SQLite).
    search_vector: Mapped[Optional[str]] = deferred(
        mapped_column(
            Text().with_variant(postgresql.TSVECTOR(), "postgresql"),
        )
    )

    warc_path: Mapped[str] = mapped_column(Text, nullable=False)
    warc_record_id: Mapped[Optional[str]] = mapped_column(String(255))
    raw_snapshot_path: Mapped[Optional[str]] = mapped_column(Text)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))

    job: Mapped[Optional[ArchiveJob]] = relationship(back_populates="snapshots")
    source: Mapped[Optional[Source]] = relationship(back_populates="snapshots")
    outlinks: Mapped[List["SnapshotOutlink"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Snapshot id={self.id!r} url={self.url!r}>"


class IssueReport(TimestampMixin, Base):
    """
    Public issue report submitted via the site (metadata errors, broken replay, etc.).
    """

    __tablename__ = "issue_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    original_url: Mapped[Optional[str]] = mapped_column(Text)
    reporter_email: Mapped[Optional[str]] = mapped_column(String(255))
    page_url: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default=text("'new'"),
        index=True,
    )
    internal_notes: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<IssueReport id={self.id!r} category={self.category!r}>"


class UsageMetric(TimestampMixin, Base):
    """
    Aggregated daily usage counts (privacy-preserving).
    """

    __tablename__ = "usage_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )
    event: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    __table_args__ = (
        UniqueConstraint(
            "metric_date",
            "event",
            name="uq_usage_metrics_date_event",
        ),
    )

    def __repr__(self) -> str:
        return f"<UsageMetric date={self.metric_date!r} event={self.event!r} count={self.count!r}>"


class SnapshotChange(TimestampMixin, Base):
    """
    Precomputed change event between two captures of the same page group.
    """

    __tablename__ = "snapshot_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    normalized_url_group: Mapped[Optional[str]] = mapped_column(Text, index=True)

    from_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("snapshots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    to_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
    )

    from_job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("archive_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    to_job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("archive_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    from_capture_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    to_capture_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    change_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    summary: Mapped[Optional[str]] = mapped_column(Text)
    diff_format: Mapped[Optional[str]] = mapped_column(String(20))
    diff_html: Mapped[Optional[str]] = mapped_column(Text)
    diff_truncated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
    )

    added_sections: Mapped[Optional[int]] = mapped_column(Integer)
    removed_sections: Mapped[Optional[int]] = mapped_column(Integer)
    changed_sections: Mapped[Optional[int]] = mapped_column(Integer)
    added_lines: Mapped[Optional[int]] = mapped_column(Integer)
    removed_lines: Mapped[Optional[int]] = mapped_column(Integer)
    change_ratio: Mapped[Optional[float]] = mapped_column(Float)

    high_noise: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("0"),
    )

    diff_version: Mapped[Optional[str]] = mapped_column(String(32))
    normalization_version: Mapped[Optional[str]] = mapped_column(String(32))
    computed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    computed_by: Mapped[Optional[str]] = mapped_column(String(64))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    from_snapshot: Mapped[Optional["Snapshot"]] = relationship(
        foreign_keys=[from_snapshot_id],
        lazy="joined",
    )
    to_snapshot: Mapped[Optional["Snapshot"]] = relationship(
        foreign_keys=[to_snapshot_id],
        lazy="joined",
    )
    source: Mapped[Optional[Source]] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<SnapshotChange id={self.id!r} to_snapshot_id={self.to_snapshot_id!r} "
            f"type={self.change_type!r}>"
        )


class Page(TimestampMixin, Base):
    """
    Canonical "page" concept, grouping multiple Snapshot captures.

    This table is intentionally metadata-only: it does not replace Snapshots or
    WARCs, and exists to support more efficient search/browse operations that
    operate at the page (URL-group) level.
    """

    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    normalized_url_group: Mapped[str] = mapped_column(Text, nullable=False)

    first_capture_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    last_capture_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    snapshot_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )

    # Convenience pointers for browse/search operations.
    #
    # - latest_snapshot_id: latest capture for the page (any status).
    # - latest_ok_snapshot_id: latest capture with status_code NULL or 2xx.
    latest_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    latest_ok_snapshot_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )

    source: Mapped[Source] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "normalized_url_group",
            name="uq_pages_source_group",
        ),
    )


class SnapshotOutlink(TimestampMixin, Base):
    """
    Outgoing link edge from a snapshot's (main) content to another page group.
    """

    __tablename__ = "snapshot_outlinks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_normalized_url_group: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        index=True,
    )

    snapshot: Mapped[Snapshot] = relationship(back_populates="outlinks")

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "to_normalized_url_group",
            name="uq_snapshot_outlinks_snapshot_to",
        ),
    )


class PageSignal(Base):
    """
    Aggregated signals per normalized_url_group, used for relevance ranking.

    Link-based signals derived from SnapshotOutlink edges.
    """

    __tablename__ = "page_signals"

    normalized_url_group: Mapped[str] = mapped_column(Text, primary_key=True)
    inlink_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    outlink_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    pagerank: Mapped[float] = mapped_column(
        # Stored as a scaled PageRank where the mean is ~1.0 across nodes.
        # (i.e. sum(pagerank) ~= number_of_nodes).
        # This makes it easier to use in ranking formulas without tiny numbers.
        Float().with_variant(postgresql.DOUBLE_PRECISION(), "postgresql"),
        nullable=False,
        server_default=text("0"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "Source",
    "ArchiveJob",
    "Snapshot",
    "IssueReport",
    "UsageMetric",
    "SnapshotChange",
    "Page",
    "SnapshotOutlink",
    "PageSignal",
]
