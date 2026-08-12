from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol

import requests
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from chaoxing_app.domain.answer_profiles import (
    AnswerProfile,
    AnswerProfileError,
    parse_answer_profile,
    validate_answer_profile_provider,
)
from chaoxing_app.domain.integrations import AnswerProviderKind
from chaoxing_app.infrastructure.db.answer_cache import AnswerCacheRepository
from chaoxing_app.infrastructure.db.integrations import (
    AnswerIntegrationRevisionMismatch,
    AnswerRuntimeConfiguration,
    IntegrationConfigurationError,
    IntegrationSettingRepository,
)
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.platform.answer_providers import (
    AnswerEnsembleProvider,
    DuckDuckGoSearchContext,
    LikeAnswerProvider,
    OpenAICompatibleAnswerProvider,
    SiliconFlowAnswerProvider,
    TikuAdapterAnswerProvider,
    YanxiAnswerProvider,
)
from chaoxing_app.platform.errors import PlatformConfigurationError
from chaoxing_app.platform.task_points.quiz import AnswerProvider, ProviderAnswer, QuizQuestion


@dataclass(frozen=True, slots=True)
class AnswerProviderBinding:
    provider: AnswerProvider | None = None
    unavailable_reason: str = "answer_integration_disabled"


class AnswerProviderRuntimePort(Protocol):
    def open(
        self,
        config_snapshot: Mapping[str, object],
    ) -> AbstractContextManager[AnswerProviderBinding]: ...


class DisabledAnswerProviderRuntime:
    @contextmanager
    def open(
        self,
        config_snapshot: Mapping[str, object],
    ) -> Iterator[AnswerProviderBinding]:
        del config_snapshot
        yield AnswerProviderBinding()


type ProviderSessionFactory = type[requests.Session]
type SearchContextFactory = Callable[
    [requests.Session],
    Callable[[QuizQuestion, str], str],
]


class ProfiledAnswerProvider:
    configured = True

    def __init__(
        self,
        *,
        provider: AnswerProvider,
        course_context_enabled: bool,
        search_context: Callable[[QuizQuestion, str], str] | None = None,
    ) -> None:
        self._provider = provider
        self._course_context_enabled = course_context_enabled
        self._search_context = search_context

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer | str | Sequence[str] | None:
        context = course_context.strip() if self._course_context_enabled else ""
        if self._search_context is not None:
            try:
                search = self._search_context(question, context).strip()
            except Exception:
                search = ""
            if search:
                context = f"{context}\n\n{search}".strip()
        return self._provider.answer(question, course_context=context)


class CachedAnswerProvider:
    configured = True

    def __init__(
        self,
        *,
        provider: AnswerProvider,
        session_factory: sessionmaker[Session],
        provider_key: str,
        ttl_seconds: int,
        cache_course_context: bool,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._provider_key = provider_key
        self._ttl_seconds = ttl_seconds
        self._cache_course_context = cache_course_context
        self._cache = AnswerCacheRepository()

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer | str | Sequence[str] | None:
        try:
            with self._session_factory() as db:
                cached = self._cache.get(
                    db,
                    question,
                    provider_key=self._provider_key,
                    course_context=course_context if self._cache_course_context else "",
                )
                db.commit()
            if cached is not None:
                return cached
        except SQLAlchemyError:
            pass

        answer = self._provider.answer(question, course_context=course_context)
        if isinstance(answer, ProviderAnswer):
            normalized = answer
        elif isinstance(answer, str):
            normalized = ProviderAnswer(answer)
        elif isinstance(answer, Sequence):
            normalized = ProviderAnswer(tuple(str(item) for item in answer))
        else:
            return answer
        try:
            with self._session_factory.begin() as db:
                self._cache.put(
                    db,
                    question,
                    normalized,
                    provider_key=self._provider_key,
                    ttl_seconds=self._ttl_seconds,
                    course_context=course_context if self._cache_course_context else "",
                )
        except SQLAlchemyError:
            pass
        return normalized


class AnswerProviderRuntimeFactory:
    """Build one isolated third-party provider session for each quiz chapter."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        secret_box: SecretBox,
        provider_session_factory: ProviderSessionFactory = requests.Session,
        search_context_factory: SearchContextFactory = (
            lambda session: DuckDuckGoSearchContext(session=session)
        ),
    ) -> None:
        self._session_factory = session_factory
        self._repository = IntegrationSettingRepository(secret_box=secret_box)
        self._provider_session_factory = provider_session_factory
        self._search_context_factory = search_context_factory

    @contextmanager
    def open(
        self,
        config_snapshot: Mapping[str, object],
    ) -> Iterator[AnswerProviderBinding]:
        policy = self._snapshot_policy(config_snapshot)
        if policy is None:
            yield AnswerProviderBinding(unavailable_reason="answer_snapshot_invalid")
            return
        enabled, expected_revision, expected_provider = policy
        if not enabled:
            yield AnswerProviderBinding()
            return

        try:
            with self._session_factory() as db:
                configuration = self._repository.answer_runtime_configuration(
                    db,
                    expected_revision=expected_revision,
                )
        except AnswerIntegrationRevisionMismatch:
            yield AnswerProviderBinding(
                unavailable_reason="answer_config_revision_mismatch"
            )
            return
        except (IntegrationConfigurationError, SQLAlchemyError):
            yield AnswerProviderBinding(
                unavailable_reason="answer_configuration_unavailable"
            )
            return

        if configuration is None:
            yield AnswerProviderBinding()
            return
        if configuration.provider is not expected_provider:
            yield AnswerProviderBinding(
                unavailable_reason="answer_config_revision_mismatch"
            )
            return

        provider_sessions: list[requests.Session] = []

        def new_provider_session() -> requests.Session:
            session = self._provider_session_factory()
            session.cookies.clear()
            session.headers.pop("Cookie", None)
            provider_sessions.append(session)
            return session

        try:
            try:
                profile = self._snapshot_profile(config_snapshot)
                if profile is None:
                    yield AnswerProviderBinding(unavailable_reason="answer_snapshot_invalid")
                    return
                try:
                    validate_answer_profile_provider(profile, configuration.provider)
                except AnswerProfileError:
                    yield AnswerProviderBinding(unavailable_reason="answer_snapshot_invalid")
                    return
                ensemble_enabled = profile.ensemble_enabled
                models = profile.models
                if ensemble_enabled and models:
                    providers = [
                        self._build_provider(
                            configuration,
                            new_provider_session(),
                            model_override=model,
                        )
                        for model in models
                    ]
                    referee_model = profile.referee_model
                    referee = (
                        self._build_provider(
                            configuration,
                            new_provider_session(),
                            model_override=referee_model,
                        )
                        if referee_model
                        else providers[0]
                    )
                    provider: AnswerProvider = AnswerEnsembleProvider(
                        providers,
                        referee=referee,
                        max_workers=profile.max_workers,
                    )
                else:
                    provider = self._build_provider(configuration, new_provider_session())
                search_context = (
                    self._search_context_factory(new_provider_session())
                    if profile.web_search_enabled
                    and configuration.provider
                    in {
                        AnswerProviderKind.OPENAI_COMPATIBLE,
                        AnswerProviderKind.SILICONFLOW,
                    }
                    else None
                )
                provider = ProfiledAnswerProvider(
                    provider=provider,
                    course_context_enabled=profile.course_context_enabled,
                    search_context=search_context,
                )
                if profile.cache_enabled:
                    provider = CachedAnswerProvider(
                        provider=provider,
                        session_factory=self._session_factory,
                        provider_key=(
                            f"answer:{configuration.revision}:"
                            f"{profile.to_json()}"
                        ),
                        ttl_seconds=profile.cache_ttl_seconds,
                        cache_course_context=profile.course_context_enabled,
                    )
            except PlatformConfigurationError:
                yield AnswerProviderBinding(
                    unavailable_reason="answer_configuration_unavailable"
                )
                return
            yield AnswerProviderBinding(provider=provider, unavailable_reason="")
        finally:
            for provider_session in provider_sessions:
                provider_session.close()

    @staticmethod
    def _snapshot_profile(snapshot: Mapping[str, object]) -> AnswerProfile | None:
        raw_answer = snapshot.get("answer")
        raw_profile = raw_answer.get("profile") if isinstance(raw_answer, Mapping) else None
        if raw_profile is None:
            return AnswerProfile()
        if not isinstance(raw_profile, Mapping):
            return None
        try:
            return parse_answer_profile(raw_profile)
        except AnswerProfileError:
            return None

    @staticmethod
    def _snapshot_policy(
        snapshot: Mapping[str, object],
    ) -> tuple[bool, int, AnswerProviderKind] | None:
        raw_policy = snapshot.get("answer")
        if not isinstance(raw_policy, Mapping):
            return False, 0, AnswerProviderKind.YANXI
        enabled = raw_policy.get("enabled")
        revision = raw_policy.get("config_revision")
        provider_value = raw_policy.get("provider")
        if (
            not isinstance(enabled, bool)
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(provider_value, str)
        ):
            return None
        try:
            provider = AnswerProviderKind(provider_value)
        except ValueError:
            return None

        duplicate_values = (
            ("answer_enabled", enabled),
            ("answer_config_revision", revision),
            ("answer_provider", provider.value),
        )
        if any(key in snapshot and snapshot[key] != value for key, value in duplicate_values):
            return None
        return enabled, revision, provider

    @staticmethod
    def _build_provider(
        configuration: AnswerRuntimeConfiguration,
        session: requests.Session,
        *,
        model_override: str = "",
    ) -> AnswerProvider:
        if configuration.provider is AnswerProviderKind.YANXI:
            return YanxiAnswerProvider(
                tokens=configuration.credential or "",
                endpoint=configuration.endpoint,
                session=session,
                allow_unsafe_endpoint=configuration.allow_unsafe_endpoint,
            )
        if configuration.provider is AnswerProviderKind.LIKE:
            return LikeAnswerProvider(
                token=configuration.credential or "",
                endpoint=configuration.endpoint,
                model=model_override or configuration.model,
                search=configuration.search,
                session=session,
                allow_unsafe_endpoint=configuration.allow_unsafe_endpoint,
            )
        if configuration.provider is AnswerProviderKind.TIKU_ADAPTER:
            return TikuAdapterAnswerProvider(
                endpoint=configuration.endpoint,
                session=session,
                allow_unsafe_endpoint=configuration.allow_unsafe_endpoint,
            )
        if configuration.provider is AnswerProviderKind.OPENAI_COMPATIBLE:
            return OpenAICompatibleAnswerProvider(
                api_key=configuration.credential or "",
                base_url=configuration.base_url,
                model=model_override or configuration.model,
                session=session,
                allow_unsafe_endpoint=configuration.allow_unsafe_endpoint,
            )
        return SiliconFlowAnswerProvider(
            api_key=configuration.credential or "",
            base_url=configuration.base_url,
            model=model_override or configuration.model,
            session=session,
            allow_unsafe_endpoint=configuration.allow_unsafe_endpoint,
        )
