"""Add snapshot changes table.

Revision ID: 0012_snapshot_changes
Revises: 0011_usage_metrics
Create Date: 2025-12-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0012_snapshot_changes"
down_revision = "0011_usage_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else ""
    bool_false = sa.text("false") if dialect == "postgresql" else sa.text("0")

    op.create_table(
        "snapshot_changes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("normalized_url_group", sa.Text(), nullable=True),
        sa.Column("from_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("to_snapshot_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("from_job_id", sa.Integer(), nullable=True),
        sa.Column("to_job_id", sa.Integer(), nullable=True),
        sa.Column("from_capture_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_capture_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("diff_format", sa.String(length=20), nullable=True),
        sa.Column("diff_html", sa.Text(), nullable=True),
        sa.Column(
            "diff_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=bool_false,
        ),
        sa.Column("added_sections", sa.Integer(), nullable=True),
        sa.Column("removed_sections", sa.Integer(), nullable=True),
        sa.Column("changed_sections", sa.Integer(), nullable=True),
        sa.Column("added_lines", sa.Integer(), nullable=True),
        sa.Column("removed_lines", sa.Integer(), nullable=True),
        sa.Column("change_ratio", sa.Float(), nullable=True),
        sa.Column(
            "high_noise",
            sa.Boolean(),
            nullable=False,
            server_default=bool_false,
        ),
        sa.Column("diff_version", sa.String(length=32), nullable=True),
        sa.Column("normalization_version", sa.String(length=32), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("computed_by", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["from_snapshot_id"],
            ["snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["to_snapshot_id"],
            ["snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_job_id"],
            ["archive_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["to_job_id"],
            ["archive_jobs.id"],
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_snapshot_changes_source_id",
        "snapshot_changes",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_changes_normalized_url_group",
        "snapshot_changes",
        ["normalized_url_group"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_changes_from_snapshot_id",
        "snapshot_changes",
        ["from_snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_changes_to_snapshot_id",
        "snapshot_changes",
        ["to_snapshot_id"],
        unique=True,
    )
    op.create_index(
        "ix_snapshot_changes_from_job_id",
        "snapshot_changes",
        ["from_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_changes_to_job_id",
        "snapshot_changes",
        ["to_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_changes_to_capture_timestamp",
        "snapshot_changes",
        ["to_capture_timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_changes_change_type",
        "snapshot_changes",
        ["change_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_snapshot_changes_change_type", table_name="snapshot_changes")
    op.drop_index(
        "ix_snapshot_changes_to_capture_timestamp", table_name="snapshot_changes"
    )
    op.drop_index("ix_snapshot_changes_to_job_id", table_name="snapshot_changes")
    op.drop_index("ix_snapshot_changes_from_job_id", table_name="snapshot_changes")
    op.drop_index("ix_snapshot_changes_to_snapshot_id", table_name="snapshot_changes")
    op.drop_index(
        "ix_snapshot_changes_from_snapshot_id", table_name="snapshot_changes"
    )
    op.drop_index(
        "ix_snapshot_changes_normalized_url_group", table_name="snapshot_changes"
    )
    op.drop_index("ix_snapshot_changes_source_id", table_name="snapshot_changes")
    op.drop_table("snapshot_changes")
