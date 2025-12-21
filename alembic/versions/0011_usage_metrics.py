"""Add usage metrics table.

Revision ID: 0011_usage_metrics
Revises: 0010_issue_reports
Create Date: 2025-12-21 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0011_usage_metrics"
down_revision = "0010_issue_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_metrics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column(
            "count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
            "metric_date",
            "event",
            name="uq_usage_metrics_date_event",
        ),
    )

    op.create_index(
        "ix_usage_metrics_metric_date",
        "usage_metrics",
        ["metric_date"],
        unique=False,
    )
    op.create_index(
        "ix_usage_metrics_event",
        "usage_metrics",
        ["event"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_usage_metrics_event", table_name="usage_metrics")
    op.drop_index("ix_usage_metrics_metric_date", table_name="usage_metrics")
    op.drop_table("usage_metrics")
