from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    RECOVERING = "recovering"
    SUCCEEDED = "succeeded"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"
    CANCELED = "canceled"


class DesiredTaskState(StrEnum):
    RUN = "run"
    PAUSE = "pause"
    CANCEL = "cancel"


class ChapterStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ALREADY_COMPLETED = "already_completed"
    UNSUBMITTED = "unsubmitted"
    SKIPPED_NOT_OPEN = "skipped_not_open"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.NEEDS_ATTENTION,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
    }
)

TERMINAL_CHAPTER_STATUSES = frozenset(
    {
        ChapterStatus.SUCCEEDED,
        ChapterStatus.ALREADY_COMPLETED,
        ChapterStatus.UNSUBMITTED,
        ChapterStatus.SKIPPED_NOT_OPEN,
        ChapterStatus.FAILED,
        ChapterStatus.CANCELED,
    }
)

_ALLOWED_TRANSITIONS = MappingProxyType(
    {
        TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELED}),
        TaskStatus.RUNNING: frozenset(
            {
                TaskStatus.PAUSE_REQUESTED,
                TaskStatus.CANCEL_REQUESTED,
                TaskStatus.RECOVERING,
                TaskStatus.SUCCEEDED,
                TaskStatus.NEEDS_ATTENTION,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.PAUSE_REQUESTED: frozenset(
            {
                TaskStatus.RUNNING,
                TaskStatus.PAUSED,
                TaskStatus.CANCEL_REQUESTED,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.PAUSED: frozenset({TaskStatus.QUEUED, TaskStatus.CANCELED}),
        TaskStatus.CANCEL_REQUESTED: frozenset(
            {
                TaskStatus.CANCELED,
                TaskStatus.SUCCEEDED,
                TaskStatus.NEEDS_ATTENTION,
                TaskStatus.FAILED,
            }
        ),
        TaskStatus.RECOVERING: frozenset(
            {TaskStatus.QUEUED, TaskStatus.FAILED, TaskStatus.CANCELED}
        ),
        TaskStatus.SUCCEEDED: frozenset(),
        TaskStatus.NEEDS_ATTENTION: frozenset(),
        TaskStatus.FAILED: frozenset(),
        TaskStatus.CANCELED: frozenset(),
    }
)


class InvalidTaskTransition(ValueError):
    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        super().__init__(f"cannot transition task from {current.value} to {target.value}")
        self.current = current
        self.target = target


def ensure_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTaskTransition(current, target)


def is_terminal_task(status: TaskStatus) -> bool:
    return status in TERMINAL_TASK_STATUSES


@dataclass(frozen=True, slots=True)
class ChapterSummary:
    total: int
    succeeded: int
    needs_attention: int
    canceled: int

    @property
    def terminal_status(self) -> TaskStatus:
        if self.total == 0:
            return TaskStatus.FAILED
        if self.canceled:
            return TaskStatus.CANCELED
        if self.needs_attention:
            return TaskStatus.NEEDS_ATTENTION
        return TaskStatus.SUCCEEDED


def summarize_chapters(statuses: Iterable[ChapterStatus]) -> ChapterSummary:
    values = tuple(statuses)
    non_terminal = [status for status in values if status not in TERMINAL_CHAPTER_STATUSES]
    if non_terminal:
        names = ", ".join(status.value for status in non_terminal)
        raise ValueError(f"cannot summarize non-terminal chapters: {names}")

    succeeded = sum(
        status in {ChapterStatus.SUCCEEDED, ChapterStatus.ALREADY_COMPLETED} for status in values
    )
    canceled = sum(status is ChapterStatus.CANCELED for status in values)
    needs_attention = len(values) - succeeded - canceled
    return ChapterSummary(
        total=len(values),
        succeeded=succeeded,
        needs_attention=needs_attention,
        canceled=canceled,
    )
