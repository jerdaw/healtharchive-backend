"""Add per-job storage accounting fields.

Revision ID: 0008_job_storage_fields
Revises: 0007_pg_trgm_fuzzy_search
Create Date: 2025-12-17 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0008_job_storage_fields"
down_revision = "0007_pg_trgm_fuzzy_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "archive_jobs",
        sa.Column(
            "warc_bytes_total",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "archive_jobs",
        sa.Column(
            "output_bytes_total",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "archive_jobs",
        sa.Column(
            "tmp_bytes_total",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "archive_jobs",
        sa.Column(
            "tmp_non_warc_bytes_total",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "archive_jobs",
        sa.Column("storage_scanned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("archive_jobs", "storage_scanned_at")
    op.drop_column("archive_jobs", "tmp_non_warc_bytes_total")
    op.drop_column("archive_jobs", "tmp_bytes_total")
    op.drop_column("archive_jobs", "output_bytes_total")
    op.drop_column("archive_jobs", "warc_bytes_total")

