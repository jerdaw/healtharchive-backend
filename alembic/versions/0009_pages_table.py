"""Add pages table for page-level grouping.

Revision ID: 0009_pages_table
Revises: 0008_job_storage_fields
Create Date: 2025-12-18 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_pages_table"
down_revision = "0008_job_storage_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("normalized_url_group", sa.Text(), nullable=False),
        sa.Column("first_capture_timestamp", sa.DateTime(timezone=True)),
        sa.Column("last_capture_timestamp", sa.DateTime(timezone=True)),
        sa.Column(
            "snapshot_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "latest_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "latest_ok_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshots.id", ondelete="SET NULL"),
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
        sa.UniqueConstraint(
            "source_id",
            "normalized_url_group",
            name="uq_pages_source_group",
        ),
    )

    op.create_index("ix_pages_source_id", "pages", ["source_id"], unique=False)
    op.create_index(
        "ix_pages_normalized_url_group",
        "pages",
        ["normalized_url_group"],
        unique=False,
    )
    op.create_index(
        "ix_pages_last_capture_timestamp",
        "pages",
        ["last_capture_timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pages_last_capture_timestamp", table_name="pages")
    op.drop_index("ix_pages_normalized_url_group", table_name="pages")
    op.drop_index("ix_pages_source_id", table_name="pages")
    op.drop_table("pages")
