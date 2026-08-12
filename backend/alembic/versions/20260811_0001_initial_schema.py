"""Create the initial application schema.

Revision ID: 20260811_0001
Revises:
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(length=120), nullable=False),
        sa.Column("username_hint", sa.String(length=80), nullable=False),
        sa.Column("username_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("chapter_concurrency", sa.Integer(), nullable=False),
        sa.Column("unopened_policy", sa.String(length=32), nullable=False),
        sa.Column("lease_version", sa.Integer(), nullable=False),
        sa.Column("answer_profile_override", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint(
            "username_fingerprint",
            name=op.f("uq_accounts_username_fingerprint"),
        ),
    )
    op.create_table(
        "answer_cache_entries",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_key", sa.String(length=160), nullable=False),
        sa.Column("answer_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_answer_cache_entries")),
    )
    op.create_index(
        "ix_answer_cache_expires_at",
        "answer_cache_entries",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_answer_cache_entries_question_hash"),
        "answer_cache_entries",
        ["question_hash"],
        unique=False,
    )
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_users")),
        sa.UniqueConstraint("username", name=op.f("uq_admin_users_username")),
    )
    op.create_table(
        "integration_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("public_config", sa.JSON(), nullable=False),
        sa.Column("secret_config_encrypted", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_settings")),
        sa.UniqueConstraint("kind", name=op.f("uq_integration_settings_kind")),
    )
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("worker_enabled", sa.Boolean(), nullable=False),
        sa.Column("run_window_enabled", sa.Boolean(), nullable=False),
        sa.Column("run_window_start", sa.String(length=5), nullable=False),
        sa.Column("run_window_end", sa.String(length=5), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("event_retention_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "id = 1",
            name=op.f("ck_system_settings_ck_system_settings_singleton"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_system_settings")),
    )
    op.create_table(
        "account_secrets",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("username_encrypted", sa.Text(), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("cookies_encrypted", sa.Text(), nullable=True),
        sa.Column("secret_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_account_secrets_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_account_secrets")),
    )
    op.create_table(
        "study_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.String(length=120), nullable=False),
        sa.Column("class_id", sa.String(length=120), nullable=False),
        sa.Column("cpi", sa.String(length=120), nullable=False),
        sa.Column("course_title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False),
        sa.Column("pause_origin", sa.String(length=32), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("selected_chapter_ids", sa.JSON(), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_study_tasks_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_study_tasks")),
    )
    op.create_index(
        op.f("ix_study_tasks_account_id"),
        "study_tasks",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_study_tasks_claim",
        "study_tasks",
        ["status", "run_after", "priority", "created_at"],
        unique=False,
    )
    op.create_index(
        "uq_study_tasks_active_course",
        "study_tasks",
        ["account_id", "course_id", "class_id"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('queued', 'running', 'pause_requested', 'paused', "
            "'cancel_requested', 'recovering')"
        ),
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin_users.id"],
            name=op.f("fk_web_sessions_admin_id_admin_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_sessions")),
        sa.UniqueConstraint(
            "token_digest",
            name=op.f("uq_web_sessions_token_digest"),
        ),
    )
    op.create_index(
        op.f("ix_web_sessions_admin_id"),
        "web_sessions",
        ["admin_id"],
        unique=False,
    )
    op.create_table(
        "account_leases",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=120), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_account_leases_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["study_tasks.id"],
            name=op.f("fk_account_leases_task_id_study_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_account_leases")),
        sa.UniqueConstraint("task_id", name=op.f("uq_account_leases_task_id")),
    )
    op.create_index(
        op.f("ix_account_leases_expires_at"),
        "account_leases",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("chapter_id", sa.String(length=120), nullable=True),
        sa.Column("kind", sa.String(length=120), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_events_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["study_tasks.id"],
            name=op.f("fk_events_task_id_study_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index(
        "ix_events_archived_id",
        "events",
        ["archived_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_events_account_id"),
        "events",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_events_occurred_at"),
        "events",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_events_task_id"),
        "events",
        ["task_id"],
        unique=False,
    )
    op.create_index(
        "ix_events_task_id_id",
        "events",
        ["task_id", "id"],
        unique=False,
    )
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["study_tasks.id"],
            name=op.f("fk_notification_outbox_task_id_study_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_outbox")),
        sa.UniqueConstraint(
            "task_id",
            "channel",
            name=op.f("uq_notification_outbox_task_channel"),
        ),
    )
    op.create_index(
        op.f("ix_notification_outbox_available_at"),
        "notification_outbox",
        ["available_at"],
        unique=False,
    )
    op.create_index(
        "ix_notification_outbox_dispatch",
        "notification_outbox",
        ["status", "available_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_outbox_task_id"),
        "notification_outbox",
        ["task_id"],
        unique=False,
    )
    op.create_table(
        "task_chapters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_id", sa.String(length=120), nullable=False),
        sa.Column("chapter_title", sa.String(length=500), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["study_tasks.id"],
            name=op.f("fk_task_chapters_task_id_study_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_chapters")),
        sa.UniqueConstraint(
            "task_id",
            "chapter_id",
            name=op.f("uq_task_chapters_task_chapter"),
        ),
    )
    op.create_index(
        op.f("ix_task_chapters_task_id"),
        "task_chapters",
        ["task_id"],
        unique=False,
    )
    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["study_tasks.id"],
            name=op.f("fk_task_runs_task_id_study_tasks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_runs")),
    )
    op.create_index(
        op.f("ix_task_runs_task_id"),
        "task_runs",
        ["task_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_runs_task_id"), table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_index(op.f("ix_task_chapters_task_id"), table_name="task_chapters")
    op.drop_table("task_chapters")
    op.drop_index(
        op.f("ix_notification_outbox_task_id"),
        table_name="notification_outbox",
    )
    op.drop_index("ix_notification_outbox_dispatch", table_name="notification_outbox")
    op.drop_index(
        op.f("ix_notification_outbox_available_at"),
        table_name="notification_outbox",
    )
    op.drop_table("notification_outbox")
    op.drop_index("ix_events_task_id_id", table_name="events")
    op.drop_index("ix_events_archived_id", table_name="events")
    op.drop_index(op.f("ix_events_task_id"), table_name="events")
    op.drop_index(op.f("ix_events_occurred_at"), table_name="events")
    op.drop_index(op.f("ix_events_account_id"), table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_account_leases_expires_at"), table_name="account_leases")
    op.drop_table("account_leases")
    op.drop_index(op.f("ix_web_sessions_admin_id"), table_name="web_sessions")
    op.drop_table("web_sessions")
    op.drop_index("uq_study_tasks_active_course", table_name="study_tasks")
    op.drop_index("ix_study_tasks_claim", table_name="study_tasks")
    op.drop_index(op.f("ix_study_tasks_account_id"), table_name="study_tasks")
    op.drop_table("study_tasks")
    op.drop_table("account_secrets")
    op.drop_table("system_settings")
    op.drop_table("integration_settings")
    op.drop_table("admin_users")
    op.drop_index(
        op.f("ix_answer_cache_entries_question_hash"),
        table_name="answer_cache_entries",
    )
    op.drop_index("ix_answer_cache_expires_at", table_name="answer_cache_entries")
    op.drop_table("answer_cache_entries")
    op.drop_table("accounts")
