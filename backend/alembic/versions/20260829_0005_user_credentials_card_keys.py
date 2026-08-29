"""Local credentials for app users and card-key entitlements.

Revision ID: 20260829_0005
Revises: 20260813_0004
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_0005"
down_revision: str | Sequence[str] | None = "20260813_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_USED_BY = "fk_card_keys_used_by_app_users"


def upgrade() -> None:
    with op.batch_alter_table("app_users") as batch_op:
        batch_op.add_column(sa.Column("username", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("password_hash", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("task_credits", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_unique_constraint("uq_app_users_username", ["username"])

    op.create_table(
        "card_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_hint", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("batch", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("used_by", sa.Integer(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_keys")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_card_keys_code_hash")),
        sa.ForeignKeyConstraint(
            ["used_by"], ["app_users.id"], name=_FK_USED_BY, ondelete="SET NULL"
        ),
    )
    op.create_index("ix_card_keys_status", "card_keys", ["status"])


def downgrade() -> None:
    op.drop_index("ix_card_keys_status", table_name="card_keys")
    op.drop_table("card_keys")
    with op.batch_alter_table("app_users") as batch_op:
        batch_op.drop_constraint("uq_app_users_username", type_="unique")
        batch_op.drop_column("task_credits")
        batch_op.drop_column("plan_expires_at")
        batch_op.drop_column("password_hash")
        batch_op.drop_column("username")
