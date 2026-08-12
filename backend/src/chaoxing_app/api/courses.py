from fastapi import APIRouter, Depends, HTTPException, Request, status

from chaoxing_app.api.course_schemas import (
    ChapterResponse,
    CourseOutlineRequest,
    CourseOutlineResponse,
    CourseResponse,
)
from chaoxing_app.api.dependencies import AuthContext, require_csrf
from chaoxing_app.application.course_discovery import (
    AccountCredentialsMissingError,
    AccountDisabledError,
    AccountNotFoundError,
    CourseDiscoveryService,
)
from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
    PlatformTransportError,
)
from chaoxing_app.platform.models import Course

router = APIRouter(prefix="/accounts/{account_id}/courses", tags=["courses"])


def get_course_service(request: Request) -> CourseDiscoveryService:
    service: CourseDiscoveryService = request.app.state.course_discovery_service
    return service


def _raise_http_error(error: Exception) -> None:
    if isinstance(error, AccountNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    if isinstance(error, (AccountDisabledError, AccountCredentialsMissingError)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, PlatformAuthenticationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Chaoxing authentication failed",
        )
    if isinstance(error, PlatformTimeoutError):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Chaoxing request timed out",
        )
    if isinstance(error, PlatformConfigurationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        )
    if isinstance(error, (PlatformTransportError, PlatformHTTPError, PlatformParseError)):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Chaoxing returned an unusable response",
        )
    raise error


@router.post("/discover", response_model=list[CourseResponse])
def discover_courses(
    account_id: int,
    _context: AuthContext = Depends(require_csrf),
    service: CourseDiscoveryService = Depends(get_course_service),
) -> list[CourseResponse]:
    try:
        courses = service.list_courses(account_id)
    except Exception as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable") from exc
    return [
        CourseResponse(
            course_id=course.course_id,
            class_id=course.clazz_id,
            cpi=course.cpi,
            title=course.title,
            teacher=course.teacher,
            description=course.description,
        )
        for course in courses
    ]


@router.post("/{course_id}/chapters", response_model=CourseOutlineResponse)
def discover_chapters(
    account_id: int,
    course_id: str,
    payload: CourseOutlineRequest,
    _context: AuthContext = Depends(require_csrf),
    service: CourseDiscoveryService = Depends(get_course_service),
) -> CourseOutlineResponse:
    course = Course(
        course_id=course_id,
        clazz_id=payload.class_id,
        cpi=payload.cpi,
        title=payload.title,
    )
    try:
        outline = service.get_course_outline(account_id, course)
    except Exception as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable") from exc
    return CourseOutlineResponse(
        has_locked_chapters=outline.has_locked_chapters,
        chapters=[
            ChapterResponse(
                chapter_id=chapter.chapter_id,
                title=chapter.title,
                position=position,
                job_count=chapter.job_count,
                is_completed=chapter.is_completed,
                requires_unlock=chapter.requires_unlock,
            )
            for position, chapter in enumerate(outline.chapters)
        ],
    )
