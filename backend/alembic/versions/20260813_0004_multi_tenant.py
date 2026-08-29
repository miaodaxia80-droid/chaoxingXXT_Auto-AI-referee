"""Add WeChat mini-program tenants (app_users) and account ownership.

Revision ID: 20260813_0004
Revises: 20260812_0003
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260813_0004"
down_revision: str | Sequence[str] | None = "20260812_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "ck_web_sessions_single_principal"
_FK_SESSION = "fk_web_sessions_app_user_id_app_users"
_FK_ACCOUNT = "fk_accounts_user_id_app_users"


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("openid", sa.String(length=64), nullable=False),
        sa.Column("nickname", sa.String(length=120), nullable=False),
        sa.Column("avatar_url", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("quotas", sa.JSON(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_users")),
        sa.UniqueConstraint("openid", name=op.f("uq_app_users_openid")),
    )

    with op.batch_alter_table("web_sessions") as batch_op:
        batch_op.alter_column("admin_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("app_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f(_FK_SESSION), "app_users", ["app_user_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(op.f("ix_web_sessions_app_user_id"), ["app_user_id"], unique=False)
        batch_op.create_check_constraint(
            op.f(_CHECK_NAME),
            "(admin_id IS NULL) != (app_user_id IS NULL)",
        )

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f(_FK_ACCOUNT), "app_users", ["user_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index(op.f("ix_accounts_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    # Sessions owned by app users have no admin principal; they cannot survive
    # the NOT NULL restore below and are discarded on downgrade.
    op.execute("DELETE FROM web_sessions WHERE app_user_id IS NOT NULL")

    with op.batch_alter_table("web_sessions") as batch_op:
        batch_op.drop_constraint(op.f(_CHECK_NAME), type_="check")
        batch_op.drop_index(op.f("ix_web_sessions_app_user_id"))
        batch_op.drop_constraint(op.f(_FK_SESSION), type_="foreignkey")
        batch_op.drop_column("app_user_id")
        batch_op.alter_column("admin_id", existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_index(op.f("ix_accounts_user_id"))
        batch_op.drop_constraint(op.f(_FK_ACCOUNT), type_="foreignkey")
        batch_op.drop_column("user_id")

    op.drop_table("app_users")
