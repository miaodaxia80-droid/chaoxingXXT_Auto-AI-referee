from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chaoxing_app.domain.tasks import ChapterStatus, DesiredTaskState, TaskStatus
from chaoxing_app.infrastructure.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite drops timezones: treat naive values read back as UTC so they can
    be compared with timezone-aware datetimes."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def new_public_id() -> str:
    return str(uuid.uuid4())


def default_user_quotas() -> dict[str, int]:
    """Platform defaults for a WeChat mini-program tenant."""
    return {"max_accounts": 3, "max_active_tasks": 1}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class AdminUser(TimestampMixin, Base):
    __tablename__ = "admin_users"
    __table_args__ = (CheckConstraint("id = 1", name="admin_user_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    sessions: Mapped[list[WebSession]] = relationship(
        back_populates="admin", cascade="all, delete-orphan"
    )


class AppUser(TimestampMixin, Base):
    """A tenant: WeChat mini-program (openid) or local account (username+password)."""

    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    openid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Local username/password login; NULL for WeChat-only users. Their openid
    # column holds a "local:<uuid>" placeholder to keep the unique constraint.
    username: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    nickname: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    avatar_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quotas: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=default_user_quotas, nullable=False
    )
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Entitlements: time-card expiry (tz-aware) and remaining count-card credits.
    # While the plan is active, credits are not consumed.
    plan_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    task_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    sessions: Mapped[list[WebSession]] = relationship(
        back_populates="app_user", cascade="all, delete-orphan"
    )
    accounts: Mapped[list[Account]] = relationship(back_populates="user")
    redeemed_cards: Mapped[list[CardKey]] = relationship(back_populates="redeemed_by_user")


class CardKey(TimestampMixin, Base):
    """One-time redeemable card key; only the SHA-256 digest is persisted (the
    plaintext code is returned exactly once, in the generate response)."""

    __tablename__ = "card_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Display-only tail (e.g. ****-XQ8P); listing never returns the plaintext.
    code_hint: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # "time" | "count"
    value: Mapped[int] = mapped_column(Integer, nullable=False)  # days or credits
    batch: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # unused | used | revoked
    status: Mapped[str] = mapped_column(String(16), default="unused", nullable=False)
    used_by: Mapped[int | None] = mapped_column(
        ForeignKey("app_users.id", name="fk_card_keys_used_by_app_users"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), default="", nullable=False)

    redeemed_by_user: Mapped[AppUser | None] = relationship(back_populates="redeemed_cards")


class WebSession(Base):
    __tablename__ = "web_sessions"
    __table_args__ = (
        CheckConstraint(
            "(admin_id IS NULL) != (app_user_id IS NULL)",
            name="single_principal",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    admin_id: Mapped[int | None] = mapped_column(
        ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    app_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    admin: Mapped[AdminUser | None] = relationship(back_populates="sessions")
    app_user: Mapped[AppUser | None] = relationship(back_populates="sessions")


class SystemSettings(TimestampMixin, Base):
    """The single persisted row of instance-wide operational settings."""

    __tablename__ = "system_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_system_settings_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    worker_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    run_window_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    run_window_start: Mapped[str] = mapped_column(String(5), default="00:00", nullable=False)
    run_window_end: Mapped[str] = mapped_column(String(5), default="00:00", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    event_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)


class IntegrationSetting(TimestampMixin, Base):
    """Encrypted instance-wide answer-provider or notification configuration."""

    __tablename__ = "integration_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    public_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    secret_config_encrypted: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    remark: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    username_hint: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    username_fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    user_agent: Mapped[str] = mapped_column(Text, default="", nullable=False)
    speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    chapter_concurrency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unopened_policy: Mapped[str] = mapped_column(String(32), default="retry", nullable=False)
    lease_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    answer_profile_override: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    user: Mapped[AppUser | None] = relationship(back_populates="accounts")
    secret: Mapped[AccountSecret] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )
    tasks: Mapped[list[StudyTask]] = relationship(back_populates="account")


class AccountSecret(Base):
    __tablename__ = "account_secrets"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    username_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    password_encrypted: Mapped[str | None] = mapped_column(Text)
    cookies_encrypted: Mapped[str | None] = mapped_column(Text)
    secret_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    account: Mapped[Account] = relationship(back_populates="secret")


class StudyTask(TimestampMixin, Base):
    __tablename__ = "study_tasks"
    __table_args__ = (
        Index("ix_study_tasks_claim", "status", "run_after", "priority", "created_at"),
        Index(
            "uq_study_tasks_active_course",
            "account_id",
            "course_id",
            "class_id",
            unique=True,
            sqlite_where=text(
                "status IN ('queued', 'running', 'pause_requested', 'paused', "
                "'cancel_requested', 'recovering')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    course_id: Mapped[str] = mapped_column(String(120), nullable=False)
    class_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    cpi: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    course_title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED.value, nullable=False)
    desired_state: Mapped[str] = mapped_column(
        String(16), default=DesiredTaskState.RUN.value, nullable=False
    )
    pause_origin: Mapped[str | None] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    selected_chapter_ids: Mapped[list[str] | None] = mapped_column(JSON)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    account: Mapped[Account] = relationship(back_populates="tasks")
    chapters: Mapped[list[TaskChapter]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskChapter.position"
    )
    runs: Mapped[list[TaskRun]] = relationship(back_populates="task", cascade="all, delete-orphan")


class TaskChapter(Base):
    __tablename__ = "task_chapters"
    __table_args__ = (
        UniqueConstraint("task_id", "chapter_id", name="uq_task_chapters_task_chapter"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("study_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[str] = mapped_column(String(120), nullable=False)
    chapter_title: Mapped[str] = mapped_column(String(500), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=ChapterStatus.PENDING.value, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[StudyTask] = relationship(back_populates="chapters")


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("study_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    worker_id: Mapped[str] = mapped_column(String(120), nullable=False)
    process_id: Mapped[int | None] = mapped_column(Integer)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_reason: Mapped[str | None] = mapped_column(Text)

    task: Mapped[StudyTask] = relationship(back_populates="runs")


class AccountLease(Base):
    __tablename__ = "account_leases"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("study_tasks.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(120), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_task_id_id", "task_id", "id"),
        Index("ix_events_archived_id", "archived_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("study_tasks.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManualInterventionResolution(Base):
    """Append-only operator acknowledgement for a chapter needing manual work.

    The source identifiers are intentionally stored without cascading foreign keys.
    This keeps the audit fact after a separately authorized task-history deletion.
    Resolving an item never mutates the chapter or task execution record.
    """

    __tablename__ = "manual_intervention_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "chapter_id",
            name="uq_manual_intervention_resolutions_task_chapter",
        ),
        Index("ix_manual_intervention_resolutions_resolved_at", "resolved_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_chapter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(120), nullable=False)
    source_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_reason: Mapped[str | None] = mapped_column(String(160))
    resolved_by: Mapped[str] = mapped_column(String(80), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AnswerCacheEntry(Base):
    __tablename__ = "answer_cache_entries"
    __table_args__ = (Index("ix_answer_cache_expires_at", "expires_at"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider_key: Mapped[str] = mapped_column(String(160), nullable=False)
    answer_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationOutbox(TimestampMixin, Base):
    """Durable terminal-task notification work item."""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("task_id", "channel", name="uq_notification_outbox_task_channel"),
        Index("ix_notification_outbox_dispatch", "status", "available_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_public_id)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("study_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(160))
