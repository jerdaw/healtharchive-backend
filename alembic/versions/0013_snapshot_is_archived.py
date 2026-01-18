"""Add is_archived column to snapshots.

Revision ID: 0013
Revises: 0012_snapshot_changes
Create Date: 2026-01-18

Supports search ranking v3: explicit archived detection flag computed at index time.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable boolean column for archived detection.
    # - NULL = unknown (legacy rows, fall back to heuristics)
    # - True = archived page detected
    # - False = not archived
    op.add_column(
        "snapshots",
        sa.Column("is_archived", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("snapshots", "is_archived")
