"""Enforce the single local administrator invariant.

Revision ID: 20260812_0002
Revises: 20260811_0001
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260812_0002"
down_revision: str | Sequence[str] | None = "20260811_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT_NAME = "ck_admin_users_admin_user_singleton"


def upgrade() -> None:
    connection = op.get_bind()
    count, minimum_id, maximum_id = connection.execute(
        sa.text("SELECT COUNT(*), MIN(id), MAX(id) FROM admin_users")
    ).one()
    if count > 1 or (count == 1 and (minimum_id != 1 or maximum_id != 1)):
        raise RuntimeError(
            "admin_users violates the single-administrator invariant; "
            "restore a valid backup or repair the rows manually before upgrading"
        )
    existing_checks = sa.inspect(connection).get_check_constraints("admin_users")
    if any(check.get("name") == _CONSTRAINT_NAME for check in existing_checks):
        return
    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.create_check_constraint(op.f(_CONSTRAINT_NAME), "id = 1")


def downgrade() -> None:
    with op.batch_alter_table("admin_users") as batch_op:
        batch_op.drop_constraint(op.f(_CONSTRAINT_NAME), type_="check")
