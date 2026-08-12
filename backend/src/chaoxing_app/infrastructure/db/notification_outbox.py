from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import Connection, insert

from chaoxing_app.domain.integrations import NotificationChannelKind
from chaoxing_app.infrastructure.db.models import NotificationOutbox


def enqueue_terminal_notifications(
    connection: Connection,
    *,
    task_id: str,
    config_snapshot: object,
    occurred_at: datetime,
) -> None:
    """Insert one durable work item per enabled channel in the task snapshot."""

    if not isinstance(config_snapshot, Mapping):
        return
    raw_channels = config_snapshot.get("notifications")
    if not isinstance(raw_channels, list):
        return
    for raw_channel in raw_channels:
        if not isinstance(raw_channel, Mapping):
            continue
        channel_value = raw_channel.get("channel")
        enabled = raw_channel.get("enabled")
        revision = raw_channel.get("config_revision")
        if (
            not isinstance(channel_value, str)
            or not isinstance(enabled, bool)
            or not enabled
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            continue
        try:
            channel = NotificationChannelKind(channel_value)
        except ValueError:
            continue
        connection.execute(
            insert(NotificationOutbox)
            .values(
                task_id=task_id,
                channel=channel.value,
                config_revision=revision,
                status="pending",
                attempts=0,
                available_at=occurred_at,
                last_error=None,
            )
            .prefix_with("OR IGNORE")
        )


__all__ = ["enqueue_terminal_notifications"]
