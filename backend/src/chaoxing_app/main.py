import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from chaoxing_app import __version__
from chaoxing_app.api.router import router
from chaoxing_app.application.course_discovery import CourseDiscoveryService
from chaoxing_app.domain.settings import SchedulerGate
from chaoxing_app.frontend import mount_frontend
from chaoxing_app.infrastructure.db.engine import (
    create_database_engine,
    create_schema,
    make_session_factory,
    require_current_schema,
)
from chaoxing_app.infrastructure.db.events import maintain_event_retention
from chaoxing_app.infrastructure.db.system_settings import (
    DatabaseRunWindowSettingsLoader,
    SystemSettingsRepository,
)
from chaoxing_app.infrastructure.notification_dispatcher import NotificationDispatcher
from chaoxing_app.infrastructure.security.login_rate_limit import LoginRateLimiter
from chaoxing_app.infrastructure.security.passwords import PasswordService
from chaoxing_app.infrastructure.security.secrets import MasterKeyStore, SecretBox
from chaoxing_app.infrastructure.system_metrics import SystemMetricsSampler
from chaoxing_app.infrastructure.wechat import WeChatClient
from chaoxing_app.settings import AppSettings, get_settings
from chaoxing_app.worker.process import SpawnProcessLauncher, WorkerProcessConfig
from chaoxing_app.worker.service import SupervisorService, SupervisorServiceConfig
from chaoxing_app.worker.supervisor import SupervisorConfig, WorkerSupervisor


async def sanitized_validation_error_response(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    detail: list[dict[str, object]] = []
    for error in exc.errors():
        location = [
            item if isinstance(item, (str, int)) else str(item)
            for item in error.get("loc", ())
        ]
        detail.append(
            {
                "type": str(error.get("type", "value_error")),
                "loc": location,
                "msg": str(error.get("msg", "Invalid value")),
            }
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": detail},
    )


def build_supervisor_service(
    *,
    settings: AppSettings,
    engine: Engine,
    notification_dispatcher: NotificationDispatcher | None = None,
) -> SupervisorService:
    lease_duration = timedelta(seconds=settings.worker_lease_duration_seconds)
    launcher = SpawnProcessLauncher(
        config=WorkerProcessConfig(
            database_url=settings.database_url,
            master_key_path=(settings.data_dir / "master.key").resolve(),
            heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
            lease_duration_seconds=settings.worker_lease_duration_seconds,
        )
    )
    supervisor = WorkerSupervisor(
        engine=engine,
        launcher=launcher,
        config=SupervisorConfig(
            max_workers=settings.worker_max_workers,
            lease_duration=lease_duration,
            max_recovery_attempts=settings.worker_max_recovery_attempts,
            recovery_delay=timedelta(seconds=settings.worker_recovery_delay_seconds),
        ),
        scheduler_gate=SchedulerGate(
            DatabaseRunWindowSettingsLoader(make_session_factory(engine))
        ),
    )
    maintenance_tick = 0

    def maintenance() -> None:
        nonlocal maintenance_tick
        if notification_dispatcher is not None:
            notification_dispatcher.dispatch_pending()
        maintenance_tick += 1
        # Retention uses bounded batches and runs roughly once per minute.
        if maintenance_tick < max(int(60 / settings.worker_poll_interval_seconds), 1):
            return
        maintenance_tick = 0
        with make_session_factory(engine).begin() as session:
            retention_days = SystemSettingsRepository().get(session).event_retention_days
            maintain_event_retention(session, retention_days=retention_days)

    return SupervisorService(
        supervisor=supervisor,
        config=SupervisorServiceConfig(
            poll_interval_seconds=settings.worker_poll_interval_seconds,
            shutdown_grace_seconds=settings.worker_shutdown_grace_seconds,
        ),
        maintenance=maintenance,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: AppSettings = app.state.settings
    engine = create_database_engine(settings.database_url)
    supervisor_service: SupervisorService | None = None
    try:
        if settings.environment == "test":
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            create_schema(engine)
        else:
            require_current_schema(engine)
            settings.data_dir.mkdir(parents=True, exist_ok=True)
        master_key = MasterKeyStore(settings.data_dir / "master.key").load_or_create()
        app.state.master_key = master_key
        app.state.secret_box = SecretBox(master_key)
        app.state.login_rate_limiter = LoginRateLimiter(
            master_key,
            max_failures=settings.login_rate_limit_max_failures,
            window_seconds=settings.login_rate_limit_window_seconds,
            block_seconds=settings.login_rate_limit_block_seconds,
            max_buckets=settings.login_rate_limit_max_buckets,
        )
        app.state.login_dummy_password_hash = PasswordService().hash(secrets.token_urlsafe(32))
        app.state.wechat_client = WeChatClient(settings.wechat_appid, settings.wechat_secret)
        app.state.system_metrics_sampler = SystemMetricsSampler()
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        notification_dispatcher = NotificationDispatcher(
            engine=engine,
            session_factory=app.state.session_factory,
            secret_box=app.state.secret_box,
        )
        with app.state.session_factory.begin() as session:
            SystemSettingsRepository().get(session)
        app.state.course_discovery_service = CourseDiscoveryService(
            session_factory=app.state.session_factory,
            secret_box=app.state.secret_box,
        )
        if settings.environment != "test":
            supervisor_service = build_supervisor_service(
                settings=settings,
                engine=engine,
                notification_dispatcher=notification_dispatcher,
            )
            supervisor_service.start()
        app.state.supervisor_service = supervisor_service
        yield
    finally:
        try:
            if supervisor_service is not None:
                supervisor_service.stop()
        finally:
            engine.dispose()


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Chaoxing Console API",
        version=__version__,
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )
    app.state.settings = settings
    app.add_exception_handler(RequestValidationError, sanitized_validation_error_response)
    app.include_router(router, prefix=settings.api_prefix)
    if settings.frontend_dir is not None:
        mount_frontend(
            app,
            directory=str(settings.frontend_dir.resolve()),
            api_prefix=settings.api_prefix,
        )
    return app


app = create_app()
