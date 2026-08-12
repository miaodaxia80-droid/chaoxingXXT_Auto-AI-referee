from __future__ import annotations

from dataclasses import dataclass

from chaoxing_app.platform.models import Chapter, Course
from chaoxing_app.platform.task_points._http import TaskPointHTTPClient

_EMPTY_PAGE_URL = "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax"


@dataclass(frozen=True, slots=True)
class EmptyPageCompletionResult:
    accepted: bool
    status_code: int
    chapter_id: str


class EmptyPageTaskClient(TaskPointHTTPClient):
    def complete(
        self,
        course: Course,
        chapter: Chapter,
    ) -> EmptyPageCompletionResult:
        response = self._get(
            _EMPTY_PAGE_URL,
            operation="empty-page task completion",
            params={
                "courseId": course.course_id,
                "clazzid": course.clazz_id,
                "chapterId": chapter.chapter_id,
                "cpi": course.cpi,
                "verificationcode": "",
                "mooc2": 1,
                "microTopicId": 0,
                "editorPreview": 0,
            },
        )
        return EmptyPageCompletionResult(
            accepted=True,
            status_code=response.status_code,
            chapter_id=chapter.chapter_id,
        )
