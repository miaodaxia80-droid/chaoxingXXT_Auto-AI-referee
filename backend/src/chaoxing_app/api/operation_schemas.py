from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ManualInterventionResponse(BaseModel):
    id: int
    task_id: str
    account_id: int
    account_label: str
    course_title: str
    chapter_id: str
    chapter_title: str
    status: str
    reason: str | None
    occurred_at: datetime


class ResolveInterventionsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class ResolveInterventionsResponse(BaseModel):
    requested: int
    resolved: int
    already_resolved: int
    not_actionable: int


class ResourceMetricResponse(BaseModel):
    value: float | None
    unit: str
    available: bool


class WorkerRunHealthResponse(BaseModel):
    task_id: str
    worker_id: str
    process_id: int | None
    heartbeat_at: datetime
    lease_expires_at: datetime | None
    heartbeat_age_seconds: float
    lease_stale: bool


class WorkerHealthResponse(BaseModel):
    enabled: bool
    service_running: bool
    configured_capacity: int
    active_process_count: int
    active_task_ids: list[str]
    durable_active_run_count: int
    stale_run_count: int
    runs: list[WorkerRunHealthResponse]


class OperationsHealthResponse(BaseModel):
    status: str
    sampled_at: datetime
    uptime_seconds: float
    cpu: ResourceMetricResponse
    memory: ResourceMetricResponse
    temperature: ResourceMetricResponse
    worker: WorkerHealthResponse
