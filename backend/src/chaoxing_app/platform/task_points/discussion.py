from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, quote, urlparse

import requests

from chaoxing_app.platform.errors import (
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
    PlatformTransportError,
)
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points._http import TaskPointHTTPClient
from chaoxing_app.platform.task_points.cards import DiscussionTaskPoint

_CHAPTER_URL = "https://mooc1.chaoxing.com/mooc-ans/bbscircle/chapter"
_TOPIC_URL = re.compile(r'id="topicMainDiv"[^>]*\bdata="(https://groupweb\.chaoxing\.com/[^"]+)"')
_IS_FINISHED = re.compile(r'id="isFinished"[^>]*\bvalue="([^"]*)"')
_TOPIC_IDS = re.compile(r"/bbs/([0-9a-fA-F]{32})/([0-9a-fA-F]{32})/replysList")
_URL_TOKEN = re.compile(r"urlToken\s*:\s*'([0-9a-fA-F]{16,64})'")
_JS_SAFE = "-_.!~*'()"

DEFAULT_REPLY_CONTENT = "已完成本章节的学习并参与讨论。"


class DiscussionReplyStatus(StrEnum):
    POSTED = "posted"
    ALREADY_REPLIED = "already_replied"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DiscussionReplyResult:
    status: DiscussionReplyStatus
    status_code: int
    message: str = ""


def _encode_component(value: str) -> str:
    return quote(value, safe=_JS_SAFE)


def _first(values: list[str] | None) -> str:
    return values[0] if values else ""


def _message(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


class DiscussionTaskClient(TaskPointHTTPClient):
    """Reply to a chapter discussion topic.

    Mirrors the page flow: the card iframe loads ``bbscircle/chapter`` which
    links to the groupweb topic page, and the reply form posts to
    ``/pc/invitation/{uuid}/addReplys`` with the page-level ``urlToken``.
    """

    def reply(
        self,
        course: Course,
        task: DiscussionTaskPoint,
        *,
        knowledge_id: str,
        content: str,
    ) -> DiscussionReplyResult:
        if not task.job_id.strip():
            raise PlatformParseError("discussion task", "missing job id")
        if not task.mid.strip():
            raise PlatformParseError("discussion task", "missing topic id")
        if not content.strip():
            raise PlatformParseError("discussion task", "reply content is empty")

        chapter_page = self._get(
            _CHAPTER_URL,
            operation="discussion chapter page",
            params={
                "mtopicid": task.mid,
                "jobid": task.job_id,
                "isPortal": "false",
                "knowledgeid": knowledge_id,
                "ut": "s",
                "clazzId": course.clazz_id,
                "enc": task.enc,
            },
        )
        finished = _IS_FINISHED.search(chapter_page.text)
        if finished is not None and finished.group(1).strip() == "true":
            return DiscussionReplyResult(DiscussionReplyStatus.ALREADY_REPLIED, 200)

        topic_url = self._topic_url(chapter_page.text)
        topic_page = self._get(topic_url, operation="discussion topic page")
        token = self._url_token(topic_page.text)

        parsed = urlparse(topic_url)
        ids = _TOPIC_IDS.search(parsed.path)
        if ids is None:
            raise PlatformParseError("discussion topic", "missing bbs or topic id")
        bbs_id, topic_uuid = ids.group(1), ids.group(2)
        query = parse_qs(parsed.query)
        return self._post_reply(
            topic_uuid,
            bbs_id=bbs_id,
            course_id=_first(query.get("courseId")) or course.course_id,
            class_id=_first(query.get("classId")) or course.clazz_id,
            content=content,
            url_token=token,
        )

    @staticmethod
    def _topic_url(html: str) -> str:
        match = _TOPIC_URL.search(html)
        if match is None:
            raise PlatformParseError("discussion chapter page", "missing topic link")
        return match.group(1)

    @staticmethod
    def _url_token(html: str) -> str:
        match = _URL_TOKEN.search(html)
        if match is None:
            raise PlatformParseError("discussion topic page", "missing url token")
        return match.group(1)

    def _post_reply(
        self,
        topic_uuid: str,
        *,
        bbs_id: str,
        course_id: str,
        class_id: str,
        content: str,
        url_token: str,
    ) -> DiscussionReplyResult:
        # The topic page pre-encodes the reply text and the form encoder encodes
        # it again; reproduce both layers so the platform stores plain text.
        fields = [
            ("courseId", course_id),
            ("classId", class_id),
            ("replyId", "-1"),
            # Random per-reply idempotency key, mirroring RichTextUitl.randomUUID().
            ("uuid", uuid.uuid4().hex),
            ("topic_content", _encode_component(content)),
            ("files_url", ""),
            ("files_attr", ""),
            ("anonymous", ""),
            ("urlToken", url_token),
            ("bbsid", bbs_id),
        ]
        body = "&".join(
            f"{_encode_component(key)}={_encode_component(value)}" for key, value in fields
        )
        url = f"https://groupweb.chaoxing.com/pc/invitation/{topic_uuid}/addReplys"
        try:
            response = self._session.request(
                "POST",
                url,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.Timeout as exc:
            raise PlatformTimeoutError("discussion reply") from exc
        except requests.RequestException as exc:
            raise PlatformTransportError("discussion reply") from exc
        if response.status_code != 200:
            raise PlatformHTTPError("discussion reply", response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlatformParseError("discussion reply", "response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise PlatformParseError("discussion reply", "response must be an object")
        if payload.get("status") is not True:
            return DiscussionReplyResult(
                DiscussionReplyStatus.REJECTED,
                response.status_code,
                _message(payload.get("msg")),
            )
        if not payload.get("datas"):
            return DiscussionReplyResult(
                DiscussionReplyStatus.PENDING_REVIEW,
                response.status_code,
                _message(payload.get("msg")),
            )
        return DiscussionReplyResult(
            DiscussionReplyStatus.POSTED,
            response.status_code,
            _message(payload.get("msg")),
        )
