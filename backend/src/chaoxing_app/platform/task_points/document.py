from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from chaoxing_app.platform.errors import PlatformParseError
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points._http import RequestTimeout, TaskPointHTTPClient
from chaoxing_app.platform.task_points.cards import DocumentTaskPoint

_DOCUMENT_URL = "https://mooc1.chaoxing.com/ananas/job/document"
_NODE_ID = re.compile(r"nodeId_([^-&]+)(?=-|&|$)")


def _milliseconds_now() -> int:
    return int(time.time() * 1000)


def parse_document_knowledge_id(other_info: str) -> str:
    match = _NODE_ID.search(other_info)
    if match is None or not match.group(1).strip():
        raise PlatformParseError("document task other_info", "missing nodeId value")
    return match.group(1).strip()


@dataclass(frozen=True, slots=True)
class DocumentCompletionResult:
    accepted: bool
    status_code: int
    knowledge_id: str


class DocumentTaskClient(TaskPointHTTPClient):
    def __init__(
        self,
        *,
        session: requests.Session,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
        timestamp_ms: Callable[[], int] = _milliseconds_now,
    ) -> None:
        super().__init__(session=session, timeout=timeout, tls_verify=tls_verify)
        self._timestamp_ms = timestamp_ms

    def complete(
        self,
        course: Course,
        task: DocumentTaskPoint,
    ) -> DocumentCompletionResult:
        if not task.job_id.strip():
            raise PlatformParseError("document task", "missing job id")
        if not task.jtoken.strip():
            raise PlatformParseError("document task", "missing jtoken")
        knowledge_id = parse_document_knowledge_id(task.other_info)
        response = self._get(
            _DOCUMENT_URL,
            operation="document task completion",
            params={
                "jobid": task.job_id,
                "knowledgeid": knowledge_id,
                "courseid": course.course_id,
                "clazzid": course.clazz_id,
                "jtoken": task.jtoken,
                "_dc": self._timestamp_ms(),
            },
        )
        return DocumentCompletionResult(
            accepted=True,
            status_code=response.status_code,
            knowledge_id=knowledge_id,
        )
