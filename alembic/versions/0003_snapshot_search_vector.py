"""Add Postgres full-text search vector to snapshots.

Revision ID: 0003_snapshot_search_vector
Revises: 0002_cleanup_fields
Create Date: 2025-12-14 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_snapshot_search_vector"
down_revision = "0002_cleanup_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.add_column(
            "snapshots",
            sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        )
        op.create_index(
            "ix_snapshots_search_vector",
            "snapshots",
            ["search_vector"],
            unique=False,
            postgresql_using="gin",
        )
    else:
        # Keep migrations usable for the default SQLite dev DB; the backend will
        # continue to use a DB-agnostic fallback ranking path.
        op.add_column("snapshots", sa.Column("search_vector", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        op.drop_index("ix_snapshots_search_vector", table_name="snapshots")
    op.drop_column("snapshots", "search_vector")

