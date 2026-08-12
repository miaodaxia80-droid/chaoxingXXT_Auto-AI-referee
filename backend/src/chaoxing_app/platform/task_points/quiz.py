from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

import requests
from bs4 import BeautifulSoup, Tag

from chaoxing_app.platform.errors import (
    PlatformAuthenticationError,
    PlatformConfigurationError,
    PlatformHTTPError,
    PlatformParseError,
    PlatformTimeoutError,
    PlatformTransportError,
)
from chaoxing_app.platform.models import Course
from chaoxing_app.platform.task_points._http import RequestTimeout, TaskPointHTTPClient
from chaoxing_app.platform.task_points.cards import JobDefaults, QuizTaskPoint

_QUIZ_PAGE_URL: Final = "https://mooc1.chaoxing.com/mooc-ans/api/work"
_QUIZ_SUBMISSION_URL: Final = (
    "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew"
)
_LOGIN_HOST: Final = "passport2.chaoxing.com"
_LOGIN_MARKER: Final = "\u7528\u6237\u767b\u5f55"
_QUESTION_SELECTOR: Final = ".singleQuesId, [data-question-id]"
_ANSWER_FIELD = re.compile(r"^answer(?!wqbid)", re.IGNORECASE)
_OPTION_PREFIX = re.compile(r"^\s*([A-Za-z])(?:[.\s:()\u3001\uff0e\uff1a\uff09]|$)")
_ANSWER_SEPARATOR = re.compile(r"[\n\r,;|\uff0c\uff1b]+")


class QuizQuestionType(StrEnum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    COMPLETION = "completion"
    JUDGEMENT = "judgement"
    SHORT_ANSWER = "short_answer"
    UNKNOWN = "unknown"


_TYPE_CODE_TO_KIND: Final[dict[str, QuizQuestionType]] = {
    "0": QuizQuestionType.SINGLE,
    "1": QuizQuestionType.MULTIPLE,
    "2": QuizQuestionType.COMPLETION,
    "3": QuizQuestionType.JUDGEMENT,
    "4": QuizQuestionType.SHORT_ANSWER,
}
_TYPE_ALIAS_TO_KIND: Final[dict[str, QuizQuestionType]] = {
    "single": QuizQuestionType.SINGLE,
    "singlechoice": QuizQuestionType.SINGLE,
    "multiple": QuizQuestionType.MULTIPLE,
    "multiplechoice": QuizQuestionType.MULTIPLE,
    "completion": QuizQuestionType.COMPLETION,
    "fill": QuizQuestionType.COMPLETION,
    "fillblank": QuizQuestionType.COMPLETION,
    "judgement": QuizQuestionType.JUDGEMENT,
    "judgment": QuizQuestionType.JUDGEMENT,
    "truefalse": QuizQuestionType.JUDGEMENT,
    "shortanswer": QuizQuestionType.SHORT_ANSWER,
    "short_answer": QuizQuestionType.SHORT_ANSWER,
}


class AnswerProviderCapability(StrEnum):
    YANXI = "yanxi"
    LIKE = "like"
    TIKU_ADAPTER = "tiku_adapter"
    OPENAI_COMPATIBLE = "openai_compatible"
    SILICONFLOW_ENSEMBLE = "siliconflow_ensemble"


ProviderCapability = AnswerProviderCapability
SUPPORTED_PROVIDER_CAPABILITIES: Final = frozenset(AnswerProviderCapability)


class QuizSubmissionMode(StrEnum):
    AUTO = "auto"
    SAVE_ONLY = "save_only"
    SUBMIT = "submit"


SubmissionMode = QuizSubmissionMode


class QuizSubmissionStatus(StrEnum):
    SUBMITTED = "submitted"
    SAVED = "saved"
    UNSUBMITTED = "unsubmitted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class QuizQuestion:
    question_id: str
    title: str
    question_type: QuizQuestionType
    type_code: str
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        question_id = self.question_id.strip()
        title = self.title.strip()
        type_code = self.type_code.strip()
        if not question_id:
            raise ValueError("question_id must not be empty")
        if not title:
            raise ValueError("title must not be empty")
        if not type_code:
            raise ValueError("type_code must not be empty")
        object.__setattr__(self, "question_id", question_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "type_code", type_code)
        object.__setattr__(self, "options", tuple(option.strip() for option in self.options))

    @property
    def id(self) -> str:
        return self.question_id

    @property
    def answer_field_name(self) -> str:
        return f"answer{self.question_id}"

    @property
    def answer_type_field_name(self) -> str:
        return f"answertype{self.question_id}"


@dataclass(frozen=True, slots=True)
class QuizForm:
    fields: Mapping[str, str] = field(repr=False)
    questions: tuple[QuizQuestion, ...]

    def __post_init__(self) -> None:
        normalized_fields = {
            str(key).strip(): str(value)
            for key, value in self.fields.items()
            if str(key).strip()
        }
        object.__setattr__(self, "fields", MappingProxyType(normalized_fields))
        object.__setattr__(self, "questions", tuple(self.questions))
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question identifiers must be unique")

    @property
    def answerwqbid(self) -> str:
        if not self.questions:
            return ""
        return ",".join(question.question_id for question in self.questions) + ","


QuizPage = QuizForm

type ProviderAnswerValue = str | Sequence[str] | None


@dataclass(frozen=True, slots=True)
class ProviderAnswer:
    value: str | tuple[str, ...]
    source: str = "provider"


class AnswerProvider(Protocol):
    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer | ProviderAnswerValue: ...


type ProviderCallback = Callable[
    [QuizQuestion, str],
    ProviderAnswer | ProviderAnswerValue,
]


class CallbackAnswerProvider:
    capability: AnswerProviderCapability

    def __init__(self, callback: ProviderCallback | None = None) -> None:
        self._callback = callback

    @property
    def configured(self) -> bool:
        return self._callback is not None

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer | ProviderAnswerValue:
        if self._callback is None:
            return None
        return self._callback(question, course_context)


class YanxiAnswerProvider(CallbackAnswerProvider):
    capability = AnswerProviderCapability.YANXI


class LikeAnswerProvider(CallbackAnswerProvider):
    capability = AnswerProviderCapability.LIKE


class TikuAdapterAnswerProvider(CallbackAnswerProvider):
    capability = AnswerProviderCapability.TIKU_ADAPTER


class OpenAICompatibleAnswerProvider(CallbackAnswerProvider):
    capability = AnswerProviderCapability.OPENAI_COMPATIBLE


class SiliconFlowEnsembleAnswerProvider(CallbackAnswerProvider):
    capability = AnswerProviderCapability.SILICONFLOW_ENSEMBLE


YanxiProvider = YanxiAnswerProvider
LikeProvider = LikeAnswerProvider
TikuAdapterProvider = TikuAdapterAnswerProvider
OpenAICompatibleProvider = OpenAICompatibleAnswerProvider
SiliconFlowEnsembleProvider = SiliconFlowEnsembleAnswerProvider


class UnconfiguredAnswerProvider:
    configured: Final = False

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> None:
        del question, course_context
        return None


@dataclass(frozen=True, slots=True)
class QuizAnswerResolution:
    answers: Mapping[str, str]
    unanswered_question_ids: tuple[str, ...]
    provider_error_count: int
    provider_configured: bool
    total_questions: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "answers", MappingProxyType(dict(self.answers)))
        object.__setattr__(
            self,
            "unanswered_question_ids",
            tuple(self.unanswered_question_ids),
        )

    @property
    def answered_count(self) -> int:
        return len(self.answers)

    @property
    def coverage(self) -> float:
        if self.total_questions <= 0:
            return 0.0
        return self.answered_count / self.total_questions


@dataclass(frozen=True, slots=True)
class QuizSubmissionResult:
    status: QuizSubmissionStatus
    accepted: bool
    coverage: float
    answered_count: int
    total_questions: int
    status_code: int | None = None
    reason: str = ""
    provider_error_count: int = 0

    @property
    def submitted(self) -> bool:
        return self.accepted and self.status is QuizSubmissionStatus.SUBMITTED

    @property
    def saved(self) -> bool:
        return self.accepted and self.status in {
            QuizSubmissionStatus.SAVED,
            QuizSubmissionStatus.UNSUBMITTED,
        } and self.status_code is not None

    @property
    def unsubmitted(self) -> bool:
        return not self.submitted

    @property
    def needs_attention(self) -> bool:
        return not self.submitted


def _question_type(raw_type: object, raw_code: object = "") -> tuple[QuizQuestionType, str]:
    type_text = str(raw_type).strip() if raw_type is not None else ""
    code_text = str(raw_code).strip() if raw_code is not None else ""
    if not code_text and type_text in _TYPE_CODE_TO_KIND:
        code_text = type_text
    if code_text in _TYPE_CODE_TO_KIND:
        return _TYPE_CODE_TO_KIND[code_text], code_text
    normalized_type = re.sub(r"[\s_-]+", "", type_text.casefold())
    question_type = _TYPE_ALIAS_TO_KIND.get(normalized_type, QuizQuestionType.UNKNOWN)
    if not code_text:
        code_text = next(
            (code for code, kind in _TYPE_CODE_TO_KIND.items() if kind is question_type),
            type_text or "unknown",
        )
    return question_type, code_text


def _tag_attribute(tag: Tag | None, *names: str) -> str:
    if tag is None:
        return ""
    for name in names:
        value = tag.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tag_text(tag: Tag | None) -> str:
    if tag is None:
        return ""
    text = tag.get_text(" ", strip=True)
    if text:
        return re.sub(r"\s+", " ", text).strip()
    alternatives: list[str] = []
    for image in tag.find_all("img"):
        alternative = image.get("alt")
        if isinstance(alternative, str) and alternative.strip():
            alternatives.append(alternative.strip())
    return " ".join(alternatives)


def _html_question(element: Tag, index: int) -> QuizQuestion:
    resource = f"quiz question {index}"
    question_id = _tag_attribute(element, "data", "data-question-id", "data-id")
    if not question_id:
        raise PlatformParseError(resource, "missing question id")

    type_element = element.select_one(".TiMu, [data-question-type], [data-type]")
    raw_type = _tag_attribute(type_element, "data", "data-question-type", "data-type")
    if not raw_type:
        raw_type = _tag_attribute(element, "data-question-type", "data-type")
    question_type, type_code = _question_type(raw_type)

    title_element = element.select_one(
        ".Zy_TItle, .question-title, [data-question-title], .stem"
    )
    title = _tag_text(title_element)
    if not title:
        raise PlatformParseError(resource, "missing question title")

    options: list[str] = []
    option_elements = element.select("ul li")
    if not option_elements:
        option_elements = element.select("[data-option], .option")
    for option in option_elements:
        value = _tag_attribute(option, "aria-label", "data-option") or _tag_text(option)
        value = re.sub(r"\s+", " ", value).strip()
        if value.endswith("\u9009\u62e9"):
            value = value[: -len("\u9009\u62e9")].rstrip()
        if value:
            options.append(value)

    return QuizQuestion(
        question_id=question_id,
        title=title,
        question_type=question_type,
        type_code=type_code,
        options=tuple(options),
    )


def _html_form_fields(form: Tag) -> dict[str, str]:
    fields: dict[str, str] = {}
    for element in form.select("input[name], textarea[name], select[name]"):
        name = _tag_attribute(element, "name")
        if not name or _ANSWER_FIELD.match(name):
            continue
        if element.name == "textarea":
            value = element.get_text()
        elif element.name == "select":
            selected = element.select_one("option[selected]") or element.select_one("option")
            value = _tag_attribute(selected, "value") or _tag_text(selected)
        else:
            value = _tag_attribute(element, "value")
        fields[name] = value
    return fields


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return value
    return {}


def _json_text(data: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return ""


def _json_options(value: object) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(
            f"{key}. {item}".strip()
            for key, item in value.items()
            if isinstance(item, (str, int)) and not isinstance(item, bool)
        )
    if not isinstance(value, list):
        return ()
    options: list[str] = []
    for item in value:
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            options.append(str(item).strip())
            continue
        option = _mapping(item)
        text = _json_text(option, "text", "title", "content", "value")
        label = _json_text(option, "label", "key", "code")
        if text:
            options.append(f"{label}. {text}".strip(". ") if label else text)
    return tuple(options)


def _json_question(value: object, index: int) -> QuizQuestion:
    data = _mapping(value)
    resource = f"quiz question {index}"
    if not data:
        raise PlatformParseError(resource, "question must be an object")
    question_id = _json_text(data, "id", "questionId", "question_id", "qid", "data")
    title = _json_text(data, "title", "question", "content", "stem")
    if not question_id:
        raise PlatformParseError(resource, "missing question id")
    if not title:
        raise PlatformParseError(resource, "missing question title")
    raw_type = data.get("type", data.get("questionType", data.get("question_type", "")))
    raw_code = data.get("typeCode", data.get("type_code", data.get("answertype", "")))
    question_type, type_code = _question_type(raw_type, raw_code)
    options = _json_options(data.get("options", data.get("choices", ())))
    return QuizQuestion(question_id, title, question_type, type_code, options)


def _json_form(payload: object) -> QuizForm | None:
    data = _mapping(payload)
    if not data:
        return None
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list):
        nested = _mapping(data.get("data"))
        raw_questions = nested.get("questions")
        if isinstance(raw_questions, list):
            data = nested
    if not isinstance(raw_questions, list):
        return None

    raw_fields = _mapping(
        data.get("formData", data.get("form_data", data.get("fields", data.get("form", {}))))
    )
    fields = {
        key: str(value)
        for key, value in raw_fields.items()
        if isinstance(value, (str, int, float)) and not isinstance(value, bool)
    }
    questions = tuple(
        _json_question(question, index)
        for index, question in enumerate(raw_questions, start=1)
    )
    return QuizForm(fields=fields, questions=questions)


def parse_quiz_form(content: str) -> QuizForm:
    stripped = content.strip()
    if stripped.startswith(("{", "[")):
        try:
            payload: object = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise PlatformParseError("quiz page", "response is not valid JSON") from exc
        parsed = _json_form(payload)
        if parsed is None:
            raise PlatformParseError("quiz page", "JSON has no question payload")
        return parsed

    soup = BeautifulSoup(content, "html.parser")
    form = soup.find("form")
    if isinstance(form, Tag):
        questions = tuple(
            _html_question(element, index)
            for index, element in enumerate(form.select(_QUESTION_SELECTOR), start=1)
        )
        return QuizForm(fields=_html_form_fields(form), questions=questions)

    for script in soup.select('script[type="application/json"]'):
        try:
            payload = json.loads(script.get_text())
        except json.JSONDecodeError:
            continue
        parsed = _json_form(payload)
        if parsed is not None:
            return parsed
    raise PlatformParseError("quiz page", "missing quiz form")


parse_quiz_page = parse_quiz_form


def _option_code(question: QuizQuestion, index: int) -> str:
    match = _OPTION_PREFIX.match(question.options[index])
    if match is not None:
        return match.group(1).upper()
    if index < 26:
        return chr(ord("A") + index)
    return str(index + 1)


def _option_text(value: str) -> str:
    return _OPTION_PREFIX.sub("", value, count=1).strip().casefold()


def _answer_parts(value: str | Sequence[str]) -> tuple[str, ...]:
    values = (value,) if isinstance(value, str) else value
    parts: list[str] = []
    for item in values:
        if not isinstance(item, str):
            return ()
        parts.extend(part.strip() for part in _ANSWER_SEPARATOR.split(item) if part.strip())
    return tuple(parts)


def _match_option(question: QuizQuestion, value: str) -> str | None:
    candidate = value.strip()
    valid_codes = tuple(_option_code(question, index) for index in range(len(question.options)))
    if len(candidate) == 1 and candidate.upper() in valid_codes:
        return candidate.upper()
    normalized = _option_text(candidate)
    for index, option in enumerate(question.options):
        if normalized and normalized == _option_text(option):
            return valid_codes[index]
    return None


def normalize_quiz_answer(
    question: QuizQuestion,
    value: ProviderAnswer | ProviderAnswerValue,
) -> str | None:
    if isinstance(value, ProviderAnswer):
        value = value.value
    if value is None:
        return None
    if not isinstance(value, (str, Sequence)):
        return None

    if question.question_type is QuizQuestionType.JUDGEMENT:
        if not isinstance(value, str):
            parts = _answer_parts(value)
            if len(parts) != 1:
                return None
            value = parts[0]
        normalized = re.sub(r"\s+", "", value).casefold()
        if normalized in {"true", "1", "yes", "correct", "\u5bf9", "\u6b63\u786e"}:
            return "true"
        if normalized in {"false", "0", "no", "incorrect", "\u9519", "\u9519\u8bef"}:
            return "false"
        return None

    if question.question_type in {
        QuizQuestionType.COMPLETION,
        QuizQuestionType.SHORT_ANSWER,
    }:
        if isinstance(value, str):
            normalized = value.strip()
        else:
            normalized = "\n".join(item.strip() for item in value if isinstance(item, str)).strip()
        return normalized or None

    if question.question_type is QuizQuestionType.SINGLE:
        parts = _answer_parts(value)
        if len(parts) != 1:
            return None
        return _match_option(question, parts[0])

    if question.question_type is QuizQuestionType.MULTIPLE:
        parts = _answer_parts(value)
        if len(parts) == 1 and len(parts[0]) > 1:
            compact = parts[0].replace(" ", "").upper()
            valid_codes = {
                _option_code(question, index) for index in range(len(question.options))
            }
            if compact and set(compact) <= valid_codes:
                parts = tuple(compact)
        codes = {_match_option(question, part) for part in parts}
        if None in codes or not codes:
            return None
        order = {
            _option_code(question, index): index for index in range(len(question.options))
        }
        return "".join(
            sorted((code for code in codes if code is not None), key=lambda code: order[code])
        )

    return None


def _provider_configured(provider: AnswerProvider | None) -> bool:
    if provider is None:
        return False
    configured = getattr(provider, "configured", True)
    return configured if isinstance(configured, bool) else True


def resolve_quiz_answers(
    form: QuizForm,
    provider: AnswerProvider | None,
    *,
    course_context: str = "",
) -> QuizAnswerResolution:
    configured = _provider_configured(provider)
    if provider is None or not configured:
        return QuizAnswerResolution(
            answers={},
            unanswered_question_ids=tuple(q.question_id for q in form.questions),
            provider_error_count=0,
            provider_configured=False,
            total_questions=len(form.questions),
        )

    answers: dict[str, str] = {}
    unanswered: list[str] = []
    provider_errors = 0
    for question in form.questions:
        try:
            raw_answer = provider.answer(question, course_context=course_context)
        except Exception:
            provider_errors += 1
            unanswered.append(question.question_id)
            continue
        answer = normalize_quiz_answer(question, raw_answer)
        if answer is None:
            unanswered.append(question.question_id)
            continue
        answers[question.question_id] = answer
    return QuizAnswerResolution(
        answers=answers,
        unanswered_question_ids=tuple(unanswered),
        provider_error_count=provider_errors,
        provider_configured=True,
        total_questions=len(form.questions),
    )


def build_quiz_fetch_params(
    course: Course,
    task: QuizTaskPoint,
    *,
    knowledge_id: str,
    ktoken: str = "",
    cpi: str = "",
) -> dict[str, str]:
    if not task.job_id.strip():
        raise PlatformParseError("quiz task", "missing job id")
    if not knowledge_id.strip():
        raise PlatformParseError("quiz task", "missing knowledge id")
    return {
        "api": "1",
        "workId": task.job_id.removeprefix("work-"),
        "jobid": task.job_id,
        "originJobId": task.job_id,
        "needRedirect": "true",
        "skipHeader": "true",
        "knowledgeid": knowledge_id.strip(),
        "ktoken": ktoken.strip(),
        "cpi": cpi.strip() or course.cpi,
        "ut": "s",
        "clazzId": course.clazz_id,
        "type": "",
        "enc": task.enc,
        "mooc2": "1",
        "courseid": course.course_id,
    }


def build_quiz_submission_data(
    form: QuizForm,
    answers: Mapping[str, str],
    *,
    submit: bool,
) -> dict[str, str]:
    data = dict(form.fields)
    data["answerwqbid"] = form.answerwqbid
    data["pyFlag"] = "" if submit else "1"
    for question in form.questions:
        data[question.answer_field_name] = answers.get(question.question_id, "").strip()
        data[question.answer_type_field_name] = question.type_code
    return data


class QuizTaskClient(TaskPointHTTPClient):
    def __init__(
        self,
        *,
        session: requests.Session,
        provider: AnswerProvider | None = None,
        submit_threshold: float = 0.8,
        mode: QuizSubmissionMode = QuizSubmissionMode.AUTO,
        timeout: RequestTimeout = (5.0, 15.0),
        tls_verify: bool | str = True,
    ) -> None:
        super().__init__(session=session, timeout=timeout, tls_verify=tls_verify)
        self._provider = provider
        self._submit_threshold = self._validate_threshold(submit_threshold)
        self._mode = self._coerce_mode(mode)

    def fetch(
        self,
        course: Course,
        task: QuizTaskPoint,
        *,
        knowledge_id: str = "",
        ktoken: str = "",
        defaults: JobDefaults | None = None,
    ) -> QuizForm:
        resolved_knowledge_id = knowledge_id or (defaults.knowledge_id if defaults else "")
        resolved_ktoken = ktoken or (defaults.ktoken if defaults else "")
        resolved_cpi = defaults.cpi if defaults else ""
        response = self._get(
            _QUIZ_PAGE_URL,
            operation="quiz question page",
            params=build_quiz_fetch_params(
                course,
                task,
                knowledge_id=resolved_knowledge_id,
                ktoken=resolved_ktoken,
                cpi=resolved_cpi,
            ),
        )
        return parse_quiz_form(response.text)

    fetch_questions = fetch

    def complete(
        self,
        course: Course,
        task: QuizTaskPoint,
        *,
        knowledge_id: str = "",
        ktoken: str = "",
        defaults: JobDefaults | None = None,
        provider: AnswerProvider | None = None,
        course_context: str = "",
        mode: QuizSubmissionMode | None = None,
        submit_threshold: float | None = None,
    ) -> QuizSubmissionResult:
        form = self.fetch(
            course,
            task,
            knowledge_id=knowledge_id,
            ktoken=ktoken,
            defaults=defaults,
        )
        active_provider = provider if provider is not None else self._provider
        resolution = resolve_quiz_answers(
            form,
            active_provider,
            course_context=course_context.strip() or course.title,
        )
        return self.submit(
            form,
            resolution,
            mode=mode,
            submit_threshold=submit_threshold,
        )

    def submit(
        self,
        form: QuizForm,
        resolution: QuizAnswerResolution,
        *,
        mode: QuizSubmissionMode | None = None,
        submit_threshold: float | None = None,
    ) -> QuizSubmissionResult:
        resolution = self._sanitize_resolution(form, resolution)
        active_mode = self._coerce_mode(mode or self._mode)
        threshold = (
            self._submit_threshold
            if submit_threshold is None
            else self._validate_threshold(submit_threshold)
        )
        no_request_reason = self._no_request_reason(form, resolution)
        if no_request_reason:
            return self._result(
                status=QuizSubmissionStatus.UNSUBMITTED,
                accepted=False,
                resolution=resolution,
                reason=no_request_reason,
            )

        meets_threshold = resolution.coverage >= threshold
        should_submit = active_mode is not QuizSubmissionMode.SAVE_ONLY and meets_threshold
        data = build_quiz_submission_data(form, resolution.answers, submit=should_submit)
        accepted, status_code = self._post_submission(data)
        if not accepted:
            return self._result(
                status=QuizSubmissionStatus.REJECTED,
                accepted=False,
                resolution=resolution,
                status_code=status_code,
                reason="platform_rejected",
            )
        if should_submit:
            return self._result(
                status=QuizSubmissionStatus.SUBMITTED,
                accepted=True,
                resolution=resolution,
                status_code=status_code,
            )
        if active_mode is QuizSubmissionMode.SAVE_ONLY:
            return self._result(
                status=QuizSubmissionStatus.SAVED,
                accepted=True,
                resolution=resolution,
                status_code=status_code,
                reason="save_only",
            )
        return self._result(
            status=QuizSubmissionStatus.UNSUBMITTED,
            accepted=True,
            resolution=resolution,
            status_code=status_code,
            reason="coverage_below_threshold",
        )

    def _post_submission(self, data: Mapping[str, str]) -> tuple[bool, int]:
        try:
            response = self._session.request(
                "POST",
                _QUIZ_SUBMISSION_URL,
                data=data,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": "https://mooc1.chaoxing.com",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=self._timeout,
                verify=self._tls_verify,
            )
        except requests.Timeout as exc:
            raise PlatformTimeoutError("quiz submission") from exc
        except requests.RequestException as exc:
            raise PlatformTransportError("quiz submission") from exc
        if response.status_code != 200:
            raise PlatformHTTPError("quiz submission", response.status_code)
        if _LOGIN_HOST in str(response.url or "").casefold() or _LOGIN_MARKER in response.text:
            raise PlatformAuthenticationError("platform session is not authenticated")
        try:
            payload: object = response.json()
        except ValueError as exc:
            raise PlatformParseError(
                "quiz submission response",
                "response is not valid JSON",
            ) from exc
        result = _mapping(payload)
        if not result or "status" not in result:
            raise PlatformParseError(
                "quiz submission response",
                "missing status",
            )
        status = result["status"]
        if isinstance(status, bool):
            accepted = status
        elif isinstance(status, (str, int)):
            accepted = str(status).strip().casefold() in {"true", "1"}
        else:
            raise PlatformParseError(
                "quiz submission response",
                "invalid status",
            )
        return accepted, response.status_code

    @staticmethod
    def _sanitize_resolution(
        form: QuizForm,
        resolution: QuizAnswerResolution,
    ) -> QuizAnswerResolution:
        answers: dict[str, str] = {}
        unanswered: list[str] = []
        for question in form.questions:
            raw_answer = resolution.answers.get(question.question_id)
            answer = normalize_quiz_answer(question, raw_answer)
            if answer is None:
                unanswered.append(question.question_id)
                continue
            answers[question.question_id] = answer
        return QuizAnswerResolution(
            answers=answers,
            unanswered_question_ids=tuple(unanswered),
            provider_error_count=max(resolution.provider_error_count, 0),
            provider_configured=resolution.provider_configured,
            total_questions=len(form.questions),
        )

    @staticmethod
    def _validate_threshold(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise PlatformConfigurationError("quiz submit threshold must be numeric")
        threshold = float(value)
        if not 0.0 <= threshold <= 1.0:
            raise PlatformConfigurationError("quiz submit threshold must be between 0 and 1")
        return threshold

    @staticmethod
    def _coerce_mode(value: QuizSubmissionMode | str) -> QuizSubmissionMode:
        try:
            return QuizSubmissionMode(value)
        except ValueError as exc:
            raise PlatformConfigurationError("invalid quiz submission mode") from exc

    @staticmethod
    def _no_request_reason(
        form: QuizForm,
        resolution: QuizAnswerResolution,
    ) -> str:
        if not form.questions:
            return "no_questions"
        if resolution.answers:
            return ""
        if not resolution.provider_configured:
            return "provider_unconfigured"
        if resolution.provider_error_count:
            return "provider_error"
        return "no_answers"

    @staticmethod
    def _result(
        *,
        status: QuizSubmissionStatus,
        accepted: bool,
        resolution: QuizAnswerResolution,
        status_code: int | None = None,
        reason: str = "",
    ) -> QuizSubmissionResult:
        return QuizSubmissionResult(
            status=status,
            accepted=accepted,
            coverage=resolution.coverage,
            answered_count=resolution.answered_count,
            total_questions=resolution.total_questions,
            status_code=status_code,
            reason=reason,
            provider_error_count=resolution.provider_error_count,
        )
