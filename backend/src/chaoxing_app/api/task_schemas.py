from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TaskChapterCreateRequest(BaseModel):
    chapter_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=500)
    position: int = Field(ge=0, le=100_000)

    @field_validator("chapter_id", "title")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class StudyTaskCreateRequest(BaseModel):
    account_id: int = Field(gt=0)
    course_id: str = Field(min_length=1, max_length=120)
    class_id: str = Field(min_length=1, max_length=120)
    cpi: str = Field(min_length=1, max_length=120)
    course_title: str = Field(min_length=1, max_length=300)
    priority: int = Field(default=0, ge=-100, le=100)
    run_after: datetime | None = None
    chapters: list[TaskChapterCreateRequest] = Field(default_factory=list, max_length=10_000)

    @field_validator("course_id", "class_id", "cpi", "course_title")
    @classmethod
    def strip_course_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("run_after")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("run_after must include a timezone")
        return value.astimezone(UTC)

    @field_validator("chapters")
    @classmethod
    def require_unique_chapters(
        cls, chapters: list[TaskChapterCreateRequest]
    ) -> list[TaskChapterCreateRequest]:
        ids = [chapter.chapter_id for chapter in chapters]
        if len(ids) != len(set(ids)):
            raise ValueError("chapter_id values must be unique")
        positions = [chapter.position for chapter in chapters]
        if len(positions) != len(set(positions)):
            raise ValueError("chapter positions must be unique")
        return chapters


class TaskChapterResponse(BaseModel):
    chapter_id: str
    title: str
    position: int
    status: str
    attempts: int
    last_error: str | None
    started_at: datetime | None
    finished_at: datetime | None


class StudyTaskResponse(BaseModel):
    id: str
    account_id: int
    account_label: str
    course_id: str
    class_id: str
    cpi: str
    course_title: str
    status: str
    desired_state: str
    priority: int
    selected_chapter_ids: list[str] | None
    chapter_total: int
    chapter_succeeded: int
    chapter_needs_attention: int
    created_at: datetime
    updated_at: datetime
    run_after: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error: str | None


class StudyTaskDetailResponse(StudyTaskResponse):
    chapters: list[TaskChapterResponse]


class BulkStudyTaskCreateRequest(BaseModel):
    tasks: list[StudyTaskCreateRequest] = Field(min_length=1, max_length=100)


class BulkTaskCreateItemResult(BaseModel):
    course_id: str
    class_id: str
    status: Literal["created", "failed"]
    task: StudyTaskDetailResponse | None = None
    error_code: str | None = None
    error: str | None = None


class BulkTaskCreateResponse(BaseModel):
    total: int
    created: int
    failed: int
    results: list[BulkTaskCreateItemResult]


class BulkTaskActionRequest(BaseModel):
    action: Literal["pause", "resume", "cancel"]
    task_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("task_ids")
    @classmethod
    def require_unique_task_ids(cls, task_ids: list[str]) -> list[str]:
        normalized = [task_id.strip() for task_id in task_ids]
        if any(not task_id for task_id in normalized):
            raise ValueError("task_ids must not contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("task_ids must be unique")
        return normalized


class BulkTaskActionItemResult(BaseModel):
    task_id: str
    status: Literal["succeeded", "failed"]
    task: StudyTaskDetailResponse | None = None
    error_code: str | None = None
    error: str | None = None


class BulkTaskActionResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[BulkTaskActionItemResult]


class BulkTaskDeleteRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("task_ids")
    @classmethod
    def require_unique_task_ids(cls, task_ids: list[str]) -> list[str]:
        return BulkTaskActionRequest.require_unique_task_ids(task_ids)


class BulkTaskDeleteItemResult(BaseModel):
    task_id: str
    status: Literal["deleted", "failed"]
    error_code: str | None = None
    error: str | None = None


class BulkTaskDeleteResponse(BaseModel):
    total: int
    deleted: int
    failed: int
    results: list[BulkTaskDeleteItemResult]


class TaskHistoryCleanupResponse(BaseModel):
    deleted: int
