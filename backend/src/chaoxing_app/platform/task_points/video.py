from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import md5
from typing import Final

import requests

from chaoxing_app.platform.captcha import (
    CaptchaSolver,
    ChaoxingCaptchaSolver,
    is_captcha_challenge,
)
from chaoxing_app.platform.errors import (
    PlatformCaptchaError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
    PlatformTransportError,
)
from chaoxing_app.platform.models import Course

_SIGNATURE_SALT: Final = "d_yHJ!$pdA~5"
_VIDEO_REFERER: Final = (
    "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2025-0725-1842"
)
_AUDIO_REFERER: Final = (
    "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2025-0725-1842"
)


class MediaKind(StrEnum):
    VIDEO = "Video"
    AUDIO = "Audio"


@dataclass(frozen=True, slots=True)
class MediaTask:
    job_id: str
    object_id: str
    other_info: str
    name: str = ""
    initial_play_time_seconds: int = 0
    rt: str = ""
    attention_duration: str = ""
    attention_duration_enc: str = ""
    face_capture_enc: str = ""
    allow_audio_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id must not be empty")
        if not self.object_id.strip():
            raise ValueError("object_id must not be empty")
        if self.initial_play_time_seconds < 0:
            raise ValueError("initial_play_time_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    dtoken: str = field(repr=False)
    duration_seconds: int
    kind: MediaKind | None = None


@dataclass(frozen=True, slots=True)
class ProgressReportResult:
    is_passed: bool
    rt: str


def compute_progress_signature(
    *,
    class_id: str,
    user_id: str,
    job_id: str,
    object_id: str,
    playing_time_seconds: int,
    duration_seconds: int,
) -> str:
    if playing_time_seconds < 0 or duration_seconds <= 0:
        raise ValueError("media times must be positive")
    if playing_time_seconds > duration_seconds:
        raise ValueError("playing time cannot exceed duration")
    source = (
        f"[{class_id}][{user_id}][{job_id}][{object_id}]"
        f"[{playing_time_seconds * 1000}][{_SIGNATURE_SALT}]"
        f"[{duration_seconds * 1000}][0_{duration_seconds}]"
    )
    return md5(source.encode("utf-8")).hexdigest()


def resolve_rt_candidates(explicit_rt: str, other_info: str) -> tuple[str, ...]:
    if explicit_rt.strip():
        return (explicit_rt.strip(),)
    marker = "-rt_"
    position = other_info.find(marker)
    if position >= 0 and len(other_info) > position + len(marker):
        value = other_info[position + len(marker)]
        if value == "d":
            return ("0.9",)
        if value == "1":
            return ("1",)
    return ("0.9", "1")


def _milliseconds_now() -> int:
    return int(time.time() * 1000)


class VideoProgressClient:
    def __init__(
        self,
        *,
        session: requests.Session,
        timeout: tuple[float, float] = (5.0, 15.0),
        tls_verify: bool | str = True,
        timestamp_ms: Callable[[], int] = _milliseconds_now,
        captcha_solver: CaptchaSolver | None = None,
        captcha_challenge_retries: int = 1,
    ) -> None:
        if len(timeout) != 2 or any(value <= 0 for value in timeout):
            raise PlatformConfigurationError("request timeouts must be positive")
        if tls_verify is False or (isinstance(tls_verify, str) and not tls_verify.strip()):
            raise PlatformConfigurationError("TLS verification cannot be disabled")
        if not 0 <= captcha_challenge_retries <= 3:
            raise PlatformConfigurationError(
                "captcha challenge retries must be between zero and three"
            )
        self._session = session
        self._timeout = timeout
        self._tls_verify = tls_verify
        self._timestamp_ms = timestamp_ms
        self._captcha_solver = captcha_solver or ChaoxingCaptchaSolver(
            session=session,
            timeout=timeout,
            tls_verify=tls_verify,
        )
        self._captcha_challenge_retries = captcha_challenge_retries

    def fetch_metadata(
        self,
        task: MediaTask,
        *,
        fid: str,
        kind: MediaKind,
    ) -> MediaMetadata:
        response = self._request(
            "GET",
            f"https://mooc1.chaoxing.com/ananas/status/{task.object_id}",
            operation="media metadata",
            params={"k": fid, "flag": "normal"},
            headers=self._headers(kind),
        )
        payload = self._json_object(response, "media metadata")
        if payload.get("status") != "success":
            raise PlatformParseError("media metadata", "platform status is not success")
        dtoken = payload.get("dtoken")
        raw_duration = payload.get("duration")
        if not isinstance(dtoken, str) or not dtoken:
            raise PlatformParseError("media metadata", "missing dtoken")
        if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, str)):
            raise PlatformParseError("media metadata", "invalid duration")
        try:
            duration = int(raw_duration)
        except ValueError as exc:
            raise PlatformParseError("media metadata", "invalid duration") from exc
        if duration <= 0:
            raise PlatformParseError("media metadata", "invalid duration")
        return MediaMetadata(
            dtoken=dtoken,
            duration_seconds=duration,
            kind=self._metadata_kind(payload),
        )

    def report_progress(
        self,
        *,
        course: Course,
        task: MediaTask,
        metadata: MediaMetadata,
        user_id: str,
        playing_time_seconds: int,
        kind: MediaKind,
    ) -> ProgressReportResult:
        signature = compute_progress_signature(
            class_id=course.clazz_id,
            user_id=user_id,
            job_id=task.job_id,
            object_id=task.object_id,
            playing_time_seconds=playing_time_seconds,
            duration_seconds=metadata.duration_seconds,
        )
        params: dict[str, str | int] = {
            "clazzId": course.clazz_id,
            "playingTime": playing_time_seconds,
            "duration": metadata.duration_seconds,
            "clipTime": f"0_{metadata.duration_seconds}",
            "objectId": task.object_id,
            "otherInfo": task.other_info,
            "courseId": course.course_id,
            "jobid": task.job_id,
            "userid": user_id,
            "isdrag": "3",
            "view": "pc",
            "enc": signature,
            "dtype": kind.value,
        }
        optional_values = {
            "videoFaceCaptureEnc": task.face_capture_enc,
            "attDuration": task.attention_duration,
            "attDurationEnc": task.attention_duration_enc,
        }
        params.update({key: value for key, value in optional_values.items() if value})
        url = (
            "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{course.cpi}/{metadata.dtoken}"
        )

        for rt in resolve_rt_candidates(task.rt, task.other_info):
            request_params = {**params, "rt": rt, "_t": self._timestamp_ms()}
            challenge_count = 0
            while True:
                try:
                    response = self._session.request(
                        "GET",
                        url,
                        params=request_params,
                        headers=self._headers(kind),
                        timeout=self._timeout,
                        verify=self._tls_verify,
                    )
                except requests.Timeout as exc:
                    raise PlatformTimeoutError("media progress report") from exc
                except requests.RequestException as exc:
                    raise PlatformTransportError("media progress report") from exc
                if response.status_code == 403:
                    break
                if response.status_code != 200:
                    raise PlatformHTTPError("media progress report", response.status_code)
                if is_captcha_challenge(response):
                    if challenge_count >= self._captcha_challenge_retries:
                        raise PlatformCaptchaError("platform captcha challenge persisted")
                    self._captcha_solver.solve()
                    challenge_count += 1
                    continue
                payload = self._json_object(response, "media progress report")
                is_passed = payload.get("isPassed")
                if not isinstance(is_passed, bool):
                    raise PlatformParseError("media progress report", "missing isPassed flag")
                return ProgressReportResult(is_passed=is_passed, rt=rt)
        raise PlatformHTTPError("media progress report", 403)

    def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> requests.Response:
        try:
            response = self._session.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.Timeout as exc:
            raise PlatformTimeoutError(operation) from exc
        except requests.RequestException as exc:
            raise PlatformTransportError(operation) from exc
        if response.status_code != 200:
            raise PlatformHTTPError(operation, response.status_code)
        return response

    @staticmethod
    def _headers(kind: MediaKind) -> dict[str, str]:
        return {"Referer": _VIDEO_REFERER if kind is MediaKind.VIDEO else _AUDIO_REFERER}

    @staticmethod
    def _metadata_kind(payload: Mapping[str, object]) -> MediaKind | None:
        for key in ("type", "mediaType", "objectType"):
            value = payload.get(key)
            if not isinstance(value, str):
                continue
            normalized = value.strip().casefold()
            if normalized in {"audio", "sound"}:
                return MediaKind.AUDIO
            if normalized in {"video", "movie"}:
                return MediaKind.VIDEO
        filename = payload.get("filename")
        if isinstance(filename, str) and "." in filename:
            extension = filename.rsplit(".", maxsplit=1)[-1].casefold()
            if extension in {"aac", "flac", "m4a", "mp3", "ogg", "wav", "wma"}:
                return MediaKind.AUDIO
            if extension in {"avi", "flv", "mkv", "mov", "mp4", "webm", "wmv"}:
                return MediaKind.VIDEO
        return None

    @staticmethod
    def _json_object(response: requests.Response, resource: str) -> dict[str, object]:
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise PlatformParseError(resource, "response is not valid JSON") from exc
        if not isinstance(payload, dict) or not all(isinstance(key, str) for key in payload):
            raise PlatformParseError(resource, "expected a JSON object")
        return payload
