"""Extend page_signals with outlink_count and pagerank.

Revision ID: 0005_page_signals_pagerank
Revises: 0004_authority_signals
Create Date: 2025-12-15 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_page_signals_pagerank"
down_revision = "0004_authority_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "page_signals",
        sa.Column("outlink_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "page_signals",
        sa.Column(
            "pagerank",
            sa.Float().with_variant(postgresql.DOUBLE_PRECISION(), "postgresql"),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("page_signals", "pagerank")
    op.drop_column("page_signals", "outlink_count")
