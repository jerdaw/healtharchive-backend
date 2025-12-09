"""Add cleanup_status and cleaned_at to archive_jobs.

Revision ID: 0002_cleanup_fields
Revises: 0001_initial_schema
Create Date: 2025-12-09 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0002_cleanup_fields"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "archive_jobs",
        sa.Column(
            "cleanup_status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.add_column(
        "archive_jobs",
        sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("archive_jobs", "cleaned_at")
    op.drop_column("archive_jobs", "cleanup_status")

