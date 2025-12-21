"""Add issue report intake table.

Revision ID: 0010_issue_reports
Revises: 0009_pages_table
Create Date: 2025-12-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_issue_reports"
down_revision = "0009_pages_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issue_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Integer()),
        sa.Column("original_url", sa.Text()),
        sa.Column("reporter_email", sa.String(length=255)),
        sa.Column("page_url", sa.Text()),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column("internal_notes", sa.Text()),
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
        "ix_issue_reports_category",
        "issue_reports",
        ["category"],
        unique=False,
    )
    op.create_index(
        "ix_issue_reports_status",
        "issue_reports",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_issue_reports_snapshot_id",
        "issue_reports",
        ["snapshot_id"],
        unique=False,
    )



def downgrade() -> None:
    op.drop_index("ix_issue_reports_snapshot_id", table_name="issue_reports")
    op.drop_index("ix_issue_reports_status", table_name="issue_reports")
    op.drop_index("ix_issue_reports_category", table_name="issue_reports")
    op.drop_table("issue_reports")
