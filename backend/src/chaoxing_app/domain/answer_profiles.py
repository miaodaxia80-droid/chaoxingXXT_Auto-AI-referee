from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import Final

from chaoxing_app.domain.integrations import AnswerProviderKind

MAX_ENSEMBLE_MODELS: Final = 8
MAX_CACHE_TTL_SECONDS: Final = 30 * 24 * 60 * 60


class AnswerProfileError(ValueError):
    """A sanitized answer-profile validation failure."""


@dataclass(frozen=True, slots=True)
class AnswerProfile:
    """Effective non-secret answer policy stored in a queued-task snapshot."""

    ensemble_enabled: bool = False
    models: tuple[str, ...] = ()
    referee_model: str = ""
    max_workers: int = 4
    cache_enabled: bool = True
    cache_ttl_seconds: int = 7 * 24 * 60 * 60
    course_context_enabled: bool = True
    web_search_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.ensemble_enabled, bool):
            raise AnswerProfileError("ensemble_enabled must be a boolean")
        if not isinstance(self.models, tuple) or not all(
            isinstance(model, str) for model in self.models
        ):
            raise AnswerProfileError("models must be a list of strings")
        normalized_models = tuple(
            dict.fromkeys(model.strip() for model in self.models if model.strip())
        )
        if len(normalized_models) > MAX_ENSEMBLE_MODELS:
            raise AnswerProfileError(
                f"at most {MAX_ENSEMBLE_MODELS} ensemble models are supported"
            )
        if any(len(model) > 512 for model in normalized_models):
            raise AnswerProfileError("answer model name is too long")
        if not isinstance(self.referee_model, str):
            raise AnswerProfileError("referee_model must be a string")
        referee_model = self.referee_model.strip()
        if len(referee_model) > 512:
            raise AnswerProfileError("answer model name is too long")
        if (
            isinstance(self.max_workers, bool)
            or not isinstance(self.max_workers, int)
            or not 1 <= self.max_workers <= MAX_ENSEMBLE_MODELS
        ):
            raise AnswerProfileError(
                f"max_workers must be between 1 and {MAX_ENSEMBLE_MODELS}"
            )
        if not isinstance(self.cache_enabled, bool):
            raise AnswerProfileError("cache_enabled must be a boolean")
        if (
            isinstance(self.cache_ttl_seconds, bool)
            or not isinstance(self.cache_ttl_seconds, int)
            or not 60 <= self.cache_ttl_seconds <= MAX_CACHE_TTL_SECONDS
        ):
            raise AnswerProfileError(
                f"cache_ttl_seconds must be between 60 and {MAX_CACHE_TTL_SECONDS}"
            )
        if not isinstance(self.course_context_enabled, bool):
            raise AnswerProfileError("course_context_enabled must be a boolean")
        if not isinstance(self.web_search_enabled, bool):
            raise AnswerProfileError("web_search_enabled must be a boolean")
        object.__setattr__(self, "models", normalized_models)
        object.__setattr__(self, "referee_model", referee_model)

    def to_json(self) -> dict[str, object]:
        return {
            "ensemble_enabled": self.ensemble_enabled,
            "models": list(self.models),
            "referee_model": self.referee_model,
            "max_workers": self.max_workers,
            "cache_enabled": self.cache_enabled,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "course_context_enabled": self.course_context_enabled,
            "web_search_enabled": self.web_search_enabled,
        }


_PROFILE_FIELDS: Final = frozenset(
    field.name for field in dataclass_fields(AnswerProfile)
)


def parse_answer_profile(
    value: Mapping[str, object] | None,
    *,
    base: AnswerProfile | None = None,
    partial: bool = False,
) -> AnswerProfile:
    """Validate a profile or merge a partial account override over ``base``."""

    if value is None:
        return base or AnswerProfile()
    unknown = set(value) - _PROFILE_FIELDS
    if unknown:
        raise AnswerProfileError("answer profile contains unsupported fields")
    if not partial and set(value) != _PROFILE_FIELDS:
        raise AnswerProfileError("answer profile is incomplete")

    current = (base or AnswerProfile()).to_json()
    current.update(value)
    raw_models = current["models"]
    if not isinstance(raw_models, (list, tuple)) or not all(
        isinstance(model, str) for model in raw_models
    ):
        raise AnswerProfileError("models must be a list of strings")
    current["models"] = tuple(raw_models)
    try:
        return AnswerProfile(**current)  # type: ignore[arg-type]
    except TypeError:
        raise AnswerProfileError("answer profile is invalid") from None


def normalize_answer_profile_override(value: object) -> dict[str, object] | None:
    """Return a compact, validated override without injecting inherited defaults."""

    if value is None:
        return None
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise AnswerProfileError("answer profile override must be an object")
    parse_answer_profile(value, partial=True)
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key == "models":
            assert isinstance(item, (list, tuple))
            normalized[key] = list(dict.fromkeys(model.strip() for model in item if model.strip()))
        elif key == "referee_model":
            assert isinstance(item, str)
            normalized[key] = item.strip()
        else:
            normalized[key] = item
    return normalized


def merge_answer_profile(
    global_profile: AnswerProfile,
    override: Mapping[str, object] | None,
) -> AnswerProfile:
    return parse_answer_profile(override, base=global_profile, partial=True)


def validate_answer_profile_provider(
    profile: AnswerProfile,
    provider: AnswerProviderKind,
) -> None:
    ensemble_providers = {
        AnswerProviderKind.OPENAI_COMPATIBLE,
        AnswerProviderKind.SILICONFLOW,
        AnswerProviderKind.LIKE,
    }
    if profile.ensemble_enabled and provider not in ensemble_providers:
        raise AnswerProfileError(
            "answer ensembles require a model-capable provider"
        )
    if profile.ensemble_enabled and not profile.models:
        raise AnswerProfileError("answer ensemble models are required")
    if profile.web_search_enabled and provider not in {
        AnswerProviderKind.OPENAI_COMPATIBLE,
        AnswerProviderKind.SILICONFLOW,
    }:
        raise AnswerProfileError(
            "web search requires an OpenAI-compatible provider"
        )
