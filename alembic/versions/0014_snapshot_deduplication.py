"""Add snapshot deduplication fields.

Revision ID: 0014_snapshot_deduplication
Revises: 0013_snapshot_is_archived
Create Date: 2026-02-06

Adds:
- snapshots.deduplicated (bool, default false)
- snapshot_deduplications audit table

This is required by:
- Public API filtering (exclude storage-level duplicates by default)
- CLI dedupe/restore commands
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0014_snapshot_deduplication"
down_revision = "0013_snapshot_is_archived"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Storage-level deduplication flag.
    # False (default) = canonical/unique; True = duplicate of another snapshot.
    op.add_column(
        "snapshots",
        sa.Column(
            "deduplicated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("ix_snapshots_deduplicated", "snapshots", ["deduplicated"], unique=False)

    # Audit log for applied deduplication operations (reversible).
    op.create_table(
        "snapshot_deduplications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column(
            "canonical_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "deduped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "reason",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'same_day_same_hash'"),
        ),
    )
    op.create_index(
        "ix_snapshot_deduplications_snapshot_id",
        "snapshot_deduplications",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_snapshot_deduplications_canonical_snapshot_id",
        "snapshot_deduplications",
        ["canonical_snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_snapshot_deduplications_canonical_snapshot_id",
        table_name="snapshot_deduplications",
    )
    op.drop_index(
        "ix_snapshot_deduplications_snapshot_id",
        table_name="snapshot_deduplications",
    )
    op.drop_table("snapshot_deduplications")

    op.drop_index("ix_snapshots_deduplicated", table_name="snapshots")
    op.drop_column("snapshots", "deduplicated")
