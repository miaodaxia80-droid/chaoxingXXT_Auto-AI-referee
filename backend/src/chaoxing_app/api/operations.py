from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from chaoxing_app.api.dependencies import (
    AuthContext,
    get_app_settings,
    get_db,
    require_auth,
    require_csrf,
)
from chaoxing_app.api.operation_schemas import (
    ManualInterventionResponse,
    OperationsHealthResponse,
    ResolveInterventionsRequest,
    ResolveInterventionsResponse,
    ResourceMetricResponse,
    WorkerHealthResponse,
    WorkerRunHealthResponse,
)
from chaoxing_app.infrastructure.db.interventions import (
    list_manual_interventions,
    resolve_manual_interventions,
)
from chaoxing_app.infrastructure.db.models import AccountLease, TaskRun
from chaoxing_app.infrastructure.db.system_settings import SystemSettingsRepository
from chaoxing_app.infrastructure.system_metrics import ResourceMetric, SystemMetricsSampler
from chaoxing_app.settings import AppSettings
from chaoxing_app.worker.service import SupervisorService

router = APIRouter(prefix="/operations", tags=["operations"])


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _resource_response(metric: ResourceMetric) -> ResourceMetricResponse:
    return ResourceMetricResponse(
        value=metric.value,
        unit=metric.unit,
        available=metric.available,
    )


@router.get("/interventions", response_model=list[ManualInterventionResponse])
def get_manual_interventions(
    _context: AuthContext = Depends(require_auth),
    db: Session = Depends(get_db),
    limit: int = Query(default=200, ge=1, le=200),
) -> list[ManualInterventionResponse]:
    return [
        ManualInterventionResponse(
            id=item.id,
            task_id=item.task_id,
            account_id=item.account_id,
            account_label=item.account_label,
            course_title=item.course_title,
            chapter_id=item.chapter_id,
            chapter_title=item.chapter_title,
            status=item.status,
            reason=item.reason,
            occurred_at=item.occurred_at,
        )
        for item in list_manual_interventions(db, limit=limit)
    ]


@router.post("/interventions/resolve", response_model=ResolveInterventionsResponse)
def resolve_interventions(
    payload: ResolveInterventionsRequest,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ResolveInterventionsResponse:
    result = resolve_manual_interventions(
        db,
        item_ids=tuple(payload.ids),
        resolved_by=context.admin.username,
    )
    return ResolveInterventionsResponse(
        requested=result.requested,
        resolved=result.resolved,
        already_resolved=result.already_resolved,
        not_actionable=result.not_actionable,
    )


@router.get("/health", response_model=OperationsHealthResponse)
def operations_health(
    request: Request,
    _context: AuthContext = Depends(require_auth),
    settings: AppSettings = Depends(get_app_settings),
    db: Session = Depends(get_db),
) -> OperationsHealthResponse:
    now = datetime.now(UTC)
    service = cast(SupervisorService | None, request.app.state.supervisor_service)
    sampler = cast(SystemMetricsSampler, request.app.state.system_metrics_sampler)
    worker_settings = SystemSettingsRepository().get(db)
    active_task_ids = list(service.active_task_ids) if service is not None else []
    runs = db.execute(
        select(TaskRun, AccountLease)
        .outerjoin(AccountLease, AccountLease.task_id == TaskRun.task_id)
        .where(TaskRun.finished_at.is_(None))
        .order_by(TaskRun.started_at)
    ).all()
    run_health: list[WorkerRunHealthResponse] = []
    for run, lease in runs:
        heartbeat_age = max((now - _utc(run.heartbeat_at)).total_seconds(), 0.0)
        expires_at = _utc(lease.expires_at) if lease is not None else None
        run_health.append(
            WorkerRunHealthResponse(
                task_id=run.task_id,
                worker_id=run.worker_id,
                process_id=run.process_id,
                heartbeat_at=run.heartbeat_at,
                lease_expires_at=expires_at,
                heartbeat_age_seconds=round(heartbeat_age, 1),
                lease_stale=expires_at is None or expires_at <= now,
            )
        )
    stale_count = sum(run.lease_stale for run in run_health)
    worker_enabled = worker_settings.worker_enabled
    service_expected = settings.environment != "test"
    status = (
        "degraded"
        if stale_count or (service_expected and (service is None or not service.running))
        else "ok"
    )
    return OperationsHealthResponse(
        status=status,
        sampled_at=now,
        uptime_seconds=round(sampler.uptime_seconds, 1),
        cpu=_resource_response(sampler.cpu_percent()),
        memory=_resource_response(sampler.memory_percent()),
        temperature=_resource_response(sampler.temperature_celsius()),
        worker=WorkerHealthResponse(
            enabled=worker_enabled,
            service_running=service.running if service is not None else False,
            configured_capacity=(
                service.max_workers if service is not None else settings.worker_max_workers
            ),
            active_process_count=len(active_task_ids),
            active_task_ids=active_task_ids,
            durable_active_run_count=len(run_health),
            stale_run_count=stale_count,
            runs=run_health,
        ),
    )
