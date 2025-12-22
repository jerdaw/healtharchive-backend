"""Drop topics and snapshot_topics tables.

Revision ID: 0006_drop_topics
Revises: 0005_page_signals_pagerank
Create Date: 2025-12-15 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_drop_topics"
down_revision = "0005_page_signals_pagerank"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("snapshot_topics")
    op.drop_table("topics")


def downgrade() -> None:
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
