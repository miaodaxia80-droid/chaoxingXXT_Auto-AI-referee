"""Add append-only manual intervention resolution audit.

Revision ID: 20260812_0003
Revises: 20260812_0002
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0003"
down_revision: str | Sequence[str] | None = "20260812_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manual_intervention_resolutions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_chapter_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=120), nullable=False),
        sa.Column("source_status", sa.String(length=32), nullable=False),
        sa.Column("source_reason", sa.String(length=160), nullable=True),
        sa.Column("resolved_by", sa.String(length=80), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manual_intervention_resolutions")),
        sa.UniqueConstraint(
            "task_id",
            "chapter_id",
            name=op.f("uq_manual_intervention_resolutions_task_chapter"),
        ),
    )
    op.create_index(
        "ix_manual_intervention_resolutions_resolved_at",
        "manual_intervention_resolutions",
        ["resolved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_manual_intervention_resolutions_resolved_at",
        table_name="manual_intervention_resolutions",
    )
    op.drop_table("manual_intervention_resolutions")
