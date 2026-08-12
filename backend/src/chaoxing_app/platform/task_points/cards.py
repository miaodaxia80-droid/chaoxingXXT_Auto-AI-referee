from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

import requests

from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
    PlatformTransportError,
)
from chaoxing_app.platform.models import Chapter, Course
from chaoxing_app.platform.task_points.video import MediaKind, MediaTask

_MARG_OBJECT_ASSIGNMENT = re.compile(r"\bmArg\s*=\s*(?=\{)")
_CARDS_URL = "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
_CARDS_VERSION = "2025-0424-1038-3"


@dataclass(frozen=True, slots=True)
class JobDefaults:
    ktoken: str = ""
    mt_enc: str = ""
    report_time_interval: int = 60
    defenc: str = ""
    card_id: str = ""
    cpi: str = ""
    qnenc: str = ""
    knowledge_id: str = ""


@dataclass(frozen=True, slots=True)
class VideoTaskPoint:
    media: MediaTask
    mid: str = ""
    aid: str = ""
    kind: MediaKind = MediaKind.VIDEO

    @property
    def job_id(self) -> str:
        return self.media.job_id


@dataclass(frozen=True, slots=True)
class DocumentTaskPoint:
    job_id: str
    object_id: str
    other_info: str
    jtoken: str
    mid: str = ""
    enc: str = ""
    aid: str = ""


@dataclass(frozen=True, slots=True)
class QuizTaskPoint:
    job_id: str
    other_info: str
    mid: str = ""
    enc: str = ""
    aid: str = ""


@dataclass(frozen=True, slots=True)
class ReadTaskPoint:
    job_id: str
    item_id: str
    title: str
    other_info: str
    jtoken: str = ""
    mid: str = ""
    enc: str = ""
    aid: str = ""


@dataclass(frozen=True, slots=True)
class UnsupportedTaskPoint:
    job_id: str
    raw_type: str
    reason: str


type TaskPoint = (
    VideoTaskPoint
    | DocumentTaskPoint
    | QuizTaskPoint
    | ReadTaskPoint
    | UnsupportedTaskPoint
)


@dataclass(frozen=True, slots=True)
class TaskCardPage:
    tasks: tuple[TaskPoint, ...]
    defaults: JobDefaults | None
    not_open: bool
    attachment_count: int
    completed_attachment_count: int
    unresolved_attachment_count: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.tasks and self.defaults is None and not self.not_open


@dataclass(frozen=True, slots=True)
class ChapterTaskBundle:
    tasks: tuple[TaskPoint, ...]
    defaults: JobDefaults | None
    not_open: bool
    attachment_count: int
    completed_attachment_count: int
    unresolved_attachment_count: int = 0


def _assigned_json_object(html: str) -> dict[str, object] | None:
    # Current card pages initialize mArg with a scalar before assigning the
    # actual object. Only accept an object that starts at this assignment.
    assignment = _MARG_OBJECT_ASSIGNMENT.search(html)
    if assignment is None:
        return None
    start = assignment.end()

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(html)):
        character = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload: object = json.loads(html[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise PlatformParseError("task cards", "mArg is invalid JSON") from exc
                if not isinstance(payload, dict) or not all(
                    isinstance(key, str) for key in payload
                ):
                    raise PlatformParseError("task cards", "mArg must be a JSON object")
                return payload
    raise PlatformParseError("task cards", "mArg object is incomplete")


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return value
    return {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _integer(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _clean_other_info(value: object) -> str:
    return _text(value).split("&", 1)[0]


def _parse_defaults(value: object) -> JobDefaults | None:
    defaults = _mapping(value)
    if not defaults:
        return None
    interval = max(_integer(defaults.get("reportTimeInterval"), 60), 1)
    return JobDefaults(
        ktoken=_text(defaults.get("ktoken")),
        mt_enc=_text(defaults.get("mtEnc")),
        report_time_interval=interval,
        defenc=_text(defaults.get("defenc")),
        card_id=_text(defaults.get("cardid")),
        cpi=_text(defaults.get("cpi")),
        qnenc=_text(defaults.get("qnenc")),
        knowledge_id=_text(defaults.get("knowledgeid")),
    )


def _unsupported(card: Mapping[str, object], raw_type: str, reason: str) -> UnsupportedTaskPoint:
    return UnsupportedTaskPoint(
        job_id=_text(card.get("jobid")),
        raw_type=raw_type or "unknown",
        reason=reason,
    )


def _media_kind(
    card: Mapping[str, object], properties: Mapping[str, object]
) -> MediaKind | None:
    declared_values = (
        properties.get("mediaType"),
        properties.get("type"),
        properties.get("module"),
        card.get("mediaType"),
    )
    for value in declared_values:
        declared = _text(value).casefold()
        if declared in {"audio", "sound"}:
            return MediaKind.AUDIO
        if declared in {"video", "movie"}:
            return MediaKind.VIDEO

    name = _text(properties.get("filename")) or _text(properties.get("name"))
    extension = name.rsplit(".", maxsplit=1)[-1].casefold() if "." in name else ""
    if extension in {"aac", "flac", "m4a", "mp3", "ogg", "wav", "wma"}:
        return MediaKind.AUDIO
    if extension in {"avi", "flv", "mkv", "mov", "mp4", "webm", "wmv"}:
        return MediaKind.VIDEO
    return None


def _parse_task(card: Mapping[str, object]) -> TaskPoint | None:
    raw_type = _text(card.get("type"))
    properties = _mapping(card.get("property"))
    job_id = _text(card.get("jobid"))
    other_info = _clean_other_info(card.get("otherInfo"))

    if card.get("job") is None:
        if raw_type != "read" or properties.get("read") is True:
            return None
        if not job_id:
            return _unsupported(card, raw_type, "missing job id")
        return ReadTaskPoint(
            job_id=job_id,
            item_id=_text(properties.get("id")),
            title=_text(properties.get("title")),
            other_info=other_info,
            jtoken=_text(card.get("jtoken")),
            mid=_text(card.get("mid")),
            enc=_text(card.get("enc")),
            aid=_text(card.get("aid")),
        )

    if not job_id:
        return _unsupported(card, raw_type, "missing job id")
    if raw_type == "video":
        object_id = _text(card.get("objectId"))
        mid = _text(card.get("mid"))
        if not object_id or not mid:
            return _unsupported(card, raw_type, "media is not fully transcoded")
        resolved_kind = _media_kind(card, properties)
        return VideoTaskPoint(
            media=MediaTask(
                job_id=job_id,
                object_id=object_id,
                other_info=other_info,
                name=_text(properties.get("name")),
                initial_play_time_seconds=max(_integer(card.get("playTime")) // 1000, 0),
                rt=_text(properties.get("rt")),
                attention_duration=_text(card.get("attDuration")),
                attention_duration_enc=_text(card.get("attDurationEnc")),
                face_capture_enc=_text(card.get("videoFaceCaptureEnc")),
                allow_audio_fallback=resolved_kind is None,
            ),
            mid=mid,
            aid=_text(card.get("aid")),
            kind=resolved_kind or MediaKind.VIDEO,
        )
    if raw_type == "document":
        return DocumentTaskPoint(
            job_id=job_id,
            object_id=_text(properties.get("objectid")),
            other_info=other_info,
            jtoken=_text(card.get("jtoken")),
            mid=_text(card.get("mid")),
            enc=_text(card.get("enc")),
            aid=_text(card.get("aid")),
        )
    if raw_type == "workid":
        return QuizTaskPoint(
            job_id=job_id,
            other_info=other_info,
            mid=_text(card.get("mid")),
            enc=_text(card.get("enc")),
            aid=_text(card.get("aid")),
        )
    return _unsupported(card, raw_type, "unsupported task type")


def parse_task_card_page(html: str) -> TaskCardPage:
    if "章节未开放" in html:
        return TaskCardPage(
            tasks=(),
            defaults=None,
            not_open=True,
            attachment_count=0,
            completed_attachment_count=0,
        )
    payload = _assigned_json_object(html)
    if payload is None:
        return TaskCardPage(
            tasks=(),
            defaults=None,
            not_open=False,
            attachment_count=0,
            completed_attachment_count=0,
        )
    raw_attachments = payload.get("attachments", [])
    if not isinstance(raw_attachments, list):
        raise PlatformParseError("task cards", "attachments must be a list")
    tasks: list[TaskPoint] = []
    completed = 0
    unresolved = 0
    attachment_count = 0
    for raw_card in raw_attachments:
        card = _mapping(raw_card)
        if not card:
            raise PlatformParseError("task cards", "attachment must be an object")
        raw_type = _text(card.get("type"))
        properties = _mapping(card.get("property"))
        is_task = card.get("job") is not None or (
            raw_type == "read" and properties.get("read") is not True
        )
        if not is_task:
            continue
        attachment_count += 1
        if card.get("isPassed") is True:
            completed += 1
            continue
        task = _parse_task(card)
        if task is not None:
            tasks.append(task)
        elif card.get("job") is not None:
            unresolved += 1
    return TaskCardPage(
        tasks=tuple(tasks),
        defaults=_parse_defaults(payload.get("defaults")),
        not_open=False,
        attachment_count=attachment_count,
        completed_attachment_count=completed,
        unresolved_attachment_count=unresolved,
    )


class ChapterTaskClient:
    def __init__(
        self,
        *,
        session: requests.Session,
        timeout: tuple[float, float] = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        if len(timeout) != 2 or any(value <= 0 for value in timeout):
            raise PlatformConfigurationError("request timeouts must be positive")
        if tls_verify is False or (isinstance(tls_verify, str) and not tls_verify.strip()):
            raise PlatformConfigurationError("TLS verification cannot be disabled")
        self._session = session
        self._timeout = timeout
        self._tls_verify = tls_verify

    def fetch(self, course: Course, chapter: Chapter) -> ChapterTaskBundle:
        declared_count = max(1, min(chapter.job_count or 1, 7))
        probe_count = min(7, declared_count + 1)
        tasks: list[TaskPoint] = []
        defaults: JobDefaults | None = None
        attachment_count = 0
        completed_count = 0
        unresolved_count = 0
        consecutive_empty = 0

        for card_number in range(probe_count):
            page = self._fetch_page(course, chapter, card_number)
            if page.not_open:
                return ChapterTaskBundle((), page.defaults, True, 0, 0)
            if page.is_empty:
                consecutive_empty += 1
                if card_number >= declared_count - 1 or consecutive_empty >= 2:
                    break
                continue
            consecutive_empty = 0
            tasks.extend(page.tasks)
            defaults = page.defaults or defaults
            attachment_count += page.attachment_count
            completed_count += page.completed_attachment_count
            unresolved_count += page.unresolved_attachment_count

        return ChapterTaskBundle(
            tasks=tuple(tasks),
            defaults=defaults,
            not_open=False,
            attachment_count=attachment_count,
            completed_attachment_count=completed_count,
            unresolved_attachment_count=unresolved_count,
        )

    def _fetch_page(self, course: Course, chapter: Chapter, card_number: int) -> TaskCardPage:
        params: dict[str, str | int] = {
            "clazzid": course.clazz_id,
            "courseid": course.course_id,
            "knowledgeid": chapter.chapter_id,
            "ut": "s",
            "cpi": course.cpi,
            "v": _CARDS_VERSION,
            "mooc2": 1,
            "num": card_number,
        }
        try:
            response = self._session.request(
                "GET",
                _CARDS_URL,
                params=params,
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.Timeout as exc:
            raise PlatformTimeoutError("chapter task cards") from exc
        except requests.RequestException as exc:
            raise PlatformTransportError("chapter task cards") from exc
        if response.status_code != 200:
            raise PlatformHTTPError("chapter task cards", response.status_code)
        if "passport2.chaoxing.com" in response.url or "用户登录" in response.text:
            raise PlatformAuthenticationError("platform session is not authenticated")
        return parse_task_card_page(response.text)
