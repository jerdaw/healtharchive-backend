"""Initial schema for HealthArchive backend.

Creates core tables:
- sources
- archive_jobs
- topics
- snapshots
- snapshot_topics
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sources
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=1000)),
        sa.Column("description", sa.Text()),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_sources_code", "sources", ["code"], unique=True)

    # archive_jobs
    op.create_table(
        "archive_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id")),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("output_dir", sa.String(length=1000), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("config", sa.JSON()),
        sa.Column("crawler_exit_code", sa.Integer()),
        sa.Column("crawler_status", sa.String(length=50)),
        sa.Column("crawler_stage", sa.String(length=255)),
        sa.Column("last_stats_json", sa.JSON()),
        sa.Column(
            "pages_crawled",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "pages_total",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "pages_failed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "warc_file_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "indexed_page_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("final_zim_path", sa.String(length=1000)),
        sa.Column("combined_log_path", sa.String(length=1000)),
        sa.Column("state_file_path", sa.String(length=1000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_archive_jobs_source_id", "archive_jobs", ["source_id"], unique=False
    )
    op.create_index(
        "ix_archive_jobs_status", "archive_jobs", ["status"], unique=False
    )

    # topics
    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_topics_slug", "topics", ["slug"], unique=True)

    # snapshots
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("archive_jobs.id")),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id")),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_url_group", sa.Text()),
        sa.Column(
            "capture_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("mime_type", sa.String(length=255)),
        sa.Column("status_code", sa.Integer()),
        sa.Column("title", sa.Text()),
        sa.Column("snippet", sa.Text()),
        sa.Column("language", sa.String(length=16)),
        sa.Column("warc_path", sa.Text(), nullable=False),
        sa.Column("warc_record_id", sa.String(length=255)),
        sa.Column("raw_snapshot_path", sa.Text()),
        sa.Column("content_hash", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_snapshots_job_id", "snapshots", ["job_id"], unique=False
    )
    op.create_index(
        "ix_snapshots_source_id", "snapshots", ["source_id"], unique=False
    )
    op.create_index(
        "ix_snapshots_url", "snapshots", ["url"], unique=False
    )
    op.create_index(
        "ix_snapshots_normalized_url_group",
        "snapshots",
        ["normalized_url_group"],
        unique=False,
    )

    # snapshot_topics association table
    op.create_table(
        "snapshot_topics",
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshots.id"),
            primary_key=True,
        ),
        sa.Column(
            "topic_id",
            sa.Integer(),
            sa.ForeignKey("topics.id"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("snapshot_topics")
    op.drop_index("ix_snapshots_normalized_url_group", table_name="snapshots")
    op.drop_index("ix_snapshots_url", table_name="snapshots")
    op.drop_index("ix_snapshots_source_id", table_name="snapshots")
    op.drop_index("ix_snapshots_job_id", table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_index("ix_topics_slug", table_name="topics")
    op.drop_table("topics")
    op.drop_index("ix_archive_jobs_status", table_name="archive_jobs")
    op.drop_index("ix_archive_jobs_source_id", table_name="archive_jobs")
    op.drop_table("archive_jobs")
    op.drop_index("ix_sources_code", table_name="sources")
    op.drop_table("sources")
