"""Enable pg_trgm + trigram indexes for fuzzy/substring search.

Revision ID: 0007_pg_trgm_fuzzy_search
Revises: 0006_drop_topics
Create Date: 2025-12-17 00:00:00
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_pg_trgm_fuzzy_search"
down_revision = "0006_drop_topics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name != "postgresql":
        return

    # pg_trgm powers similarity (%) and greatly improves LIKE/ILIKE performance
    # for substring searches (e.g., partial tokens).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_index(
        "ix_snapshots_title_trgm",
        "snapshots",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_snapshots_snippet_trgm",
        "snapshots",
        ["snippet"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"snippet": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_snapshots_url_trgm",
        "snapshots",
        ["url"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"url": "gin_trgm_ops"},
    )


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name != "postgresql":
        return

    op.drop_index("ix_snapshots_url_trgm", table_name="snapshots")
    op.drop_index("ix_snapshots_snippet_trgm", table_name="snapshots")
    op.drop_index("ix_snapshots_title_trgm", table_name="snapshots")
