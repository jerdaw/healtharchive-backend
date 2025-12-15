from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (JSON, Boolean, Column, DateTime, ForeignKey, Integer,
                        String, Table, Text, UniqueConstraint, text)
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
    indexed_page_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
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
    topics: Mapped[List["Topic"]] = relationship(
        secondary="snapshot_topics",
        back_populates="snapshots",
    )
    outlinks: Mapped[List["SnapshotOutlink"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Snapshot id={self.id!r} url={self.url!r}>"


class Topic(TimestampMixin, Base):
    """
    Topic/tag used to annotate snapshots (COVID-19, mpox, etc.).
    """

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)

    snapshots: Mapped[List[Snapshot]] = relationship(
        secondary="snapshot_topics",
        back_populates="topics",
    )

    def __repr__(self) -> str:
        return f"<Topic id={self.id!r} slug={self.slug!r}>"


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

    This is intentionally simple: we currently track only a link-based signal
    (inlink_count) derived from SnapshotOutlink edges.
    """

    __tablename__ = "page_signals"

    normalized_url_group: Mapped[str] = mapped_column(Text, primary_key=True)
    inlink_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


snapshot_topics = Table(
    "snapshot_topics",
    Base.metadata,
    Column("snapshot_id", ForeignKey("snapshots.id"), primary_key=True),
    Column("topic_id", ForeignKey("topics.id"), primary_key=True),
)


__all__ = [
    "Source",
    "ArchiveJob",
    "Snapshot",
    "SnapshotOutlink",
    "Topic",
    "PageSignal",
    "snapshot_topics",
]
