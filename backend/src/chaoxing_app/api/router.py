from fastapi import APIRouter

from chaoxing_app import __version__
from chaoxing_app.api.accounts import router as accounts_router
from chaoxing_app.api.auth import router as auth_router
from chaoxing_app.api.courses import router as courses_router
from chaoxing_app.api.events import router as events_router
from chaoxing_app.api.integrations import router as integrations_router
from chaoxing_app.api.operations import router as operations_router
from chaoxing_app.api.system_settings import router as system_settings_router
from chaoxing_app.api.tasks import router as tasks_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(accounts_router)
router.include_router(courses_router)
router.include_router(tasks_router)
router.include_router(events_router)
router.include_router(system_settings_router)
router.include_router(integrations_router)
router.include_router(operations_router)


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
