from __future__ import annotations

from dataclasses import dataclass

import requests

from chaoxing_app.platform.errors import PlatformParseError
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points._http import TaskPointHTTPClient
from chaoxing_app.platform.task_points.cards import ReadTaskPoint

_READING_URL = "https://mooc1.chaoxing.com/ananas/job/readv2"


@dataclass(frozen=True, slots=True)
class ReadingCompletionResult:
    accepted: bool
    status_code: int
    message: str


class ReadingTaskClient(TaskPointHTTPClient):
    def complete(
        self,
        course: Course,
        task: ReadTaskPoint,
        *,
        knowledge_id: str,
    ) -> ReadingCompletionResult:
        if not task.job_id.strip():
            raise PlatformParseError("reading task", "missing job id")
        if not task.jtoken.strip():
            raise PlatformParseError("reading task", "missing jtoken")
        if not knowledge_id.strip():
            raise PlatformParseError("reading task", "missing knowledge id")
        response = self._get(
            _READING_URL,
            operation="reading task completion",
            params={
                "jobid": task.job_id,
                "knowledgeid": knowledge_id,
                "jtoken": task.jtoken,
                "courseid": course.course_id,
                "clazzid": course.clazz_id,
            },
        )
        payload = self._response_payload(response)
        raw_status = payload.get("status")
        if not isinstance(raw_status, bool):
            raise PlatformParseError(
                "reading completion response",
                "status must be a boolean",
            )
        raw_message = payload.get("msg", "")
        if not isinstance(raw_message, str):
            raise PlatformParseError(
                "reading completion response",
                "msg must be a string",
            )
        return ReadingCompletionResult(
            accepted=raw_status,
            status_code=response.status_code,
            message=raw_message.strip(),
        )

    @staticmethod
    def _response_payload(response: requests.Response) -> dict[str, object]:
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise PlatformParseError(
                "reading completion response",
                "response is not valid JSON",
            ) from exc
        if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
            raise PlatformParseError(
                "reading completion response",
                "expected a JSON object",
            )
        return payload
