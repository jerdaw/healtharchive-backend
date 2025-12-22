"""Add lightweight authority signals for search ranking.

Revision ID: 0004_authority_signals
Revises: 0003_snapshot_search_vector
Create Date: 2025-12-14 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_authority_signals"
down_revision = "0003_snapshot_search_vector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshot_outlinks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("to_normalized_url_group", sa.Text(), nullable=False),
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
            "snapshot_id",
            "to_normalized_url_group",
            name="uq_snapshot_outlinks_snapshot_to",
        ),
    )
    op.create_index(
        "ix_snapshot_outlinks_snapshot_id",
        "snapshot_outlinks",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_outlinks_to_normalized_url_group",
        "snapshot_outlinks",
        ["to_normalized_url_group"],
        unique=False,
    )

    op.create_table(
        "page_signals",
        sa.Column("normalized_url_group", sa.Text(), primary_key=True),
        sa.Column(
            "inlink_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("page_signals")
    op.drop_index(
        "ix_snapshot_outlinks_to_normalized_url_group",
        table_name="snapshot_outlinks",
    )
    op.drop_index("ix_snapshot_outlinks_snapshot_id", table_name="snapshot_outlinks")
    op.drop_table("snapshot_outlinks")
