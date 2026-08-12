from __future__ import annotations

import tempfile
from pathlib import Path
from typing import ClassVar

import requests
from sqlalchemy.orm import Session

from chaoxing_app.application.answer_runtime import (
    AnswerProviderRuntimeFactory,
    CachedAnswerProvider,
    ProfiledAnswerProvider,
)
from chaoxing_app.domain.answer_profiles import AnswerProfile
from chaoxing_app.infrastructure.db.engine import (
    create_database_engine,
    create_schema,
    make_session_factory,
)
from chaoxing_app.infrastructure.db.integrations import IntegrationSettingRepository
from chaoxing_app.infrastructure.security.secrets import SecretBox
from chaoxing_app.platform.task_points.quiz import (
    ProviderAnswer,
    QuizQuestion,
    QuizQuestionType,
)


class DirtyTrackingSession(requests.Session):
    created: ClassVar[list[DirtyTrackingSession]] = []

    def __init__(self) -> None:
        super().__init__()
        self.cookies.set("chaoxing", "must-not-leak")
        self.headers["Cookie"] = "chaoxing=must-not-leak"
        self.closed_by_factory = False
        self.__class__.created.append(self)

    def close(self) -> None:
        self.closed_by_factory = True
        super().close()


def make_runtime_database() -> tuple[tempfile.TemporaryDirectory[str], object, SecretBox]:
    temp_dir: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
    engine = create_database_engine(
        f"sqlite:///{(Path(temp_dir.name) / 'runtime.db').as_posix()}"
    )
    create_schema(engine)
    return temp_dir, engine, SecretBox(b"r" * 32)


def test_enabled_provider_uses_clean_session_and_exact_revision() -> None:
    temp_dir, engine, secret_box = make_runtime_database()
    try:
        repository = IntegrationSettingRepository(secret_box=secret_box)
        with Session(engine) as session, session.begin():
            configured = repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "yanxi",
                    "endpoint": "https://answers.example.test/query",
                },
                secret_changes={"tokens": "private-token"},
            )
            snapshot = repository.answer_task_snapshot(session)
            assert snapshot["answer_config_revision"] == configured.revision

        DirtyTrackingSession.created.clear()
        factory = AnswerProviderRuntimeFactory(
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            provider_session_factory=DirtyTrackingSession,
        )
        with factory.open(snapshot) as binding:
            assert binding.provider is not None
            assert binding.unavailable_reason == ""

        created = DirtyTrackingSession.created
        assert len(created) == 1
        assert not created[0].cookies
        assert "Cookie" not in created[0].headers
        assert created[0].closed_by_factory is True
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_revision_mismatch_never_opens_provider_session() -> None:
    temp_dir, engine, secret_box = make_runtime_database()
    try:
        repository = IntegrationSettingRepository(secret_box=secret_box)
        with Session(engine) as session, session.begin():
            repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "tiku_adapter",
                    "endpoint": "https://answers.example.test/query",
                },
                secret_changes={},
            )
            stale_snapshot = repository.answer_task_snapshot(session)
            repository.update_answer(session, changes={"threshold": 0.7}, secret_changes={})

        DirtyTrackingSession.created.clear()
        factory = AnswerProviderRuntimeFactory(
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            provider_session_factory=DirtyTrackingSession,
        )
        with factory.open(stale_snapshot) as binding:
            assert binding.provider is None
            assert binding.unavailable_reason == "answer_config_revision_mismatch"
        assert DirtyTrackingSession.created == []
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_each_open_gets_a_distinct_provider_session() -> None:
    temp_dir, engine, secret_box = make_runtime_database()
    try:
        repository = IntegrationSettingRepository(secret_box=secret_box)
        with Session(engine) as session, session.begin():
            repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "tiku_adapter",
                    "endpoint": "https://answers.example.test/query",
                },
                secret_changes={},
            )
            snapshot = repository.answer_task_snapshot(session)

        DirtyTrackingSession.created.clear()
        factory = AnswerProviderRuntimeFactory(
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            provider_session_factory=DirtyTrackingSession,
        )
        with factory.open(snapshot) as first:
            assert first.provider is not None
        with factory.open(snapshot) as second:
            assert second.provider is not None
        assert len(DirtyTrackingSession.created) == 2
        assert DirtyTrackingSession.created[0] is not DirtyTrackingSession.created[1]
        assert all(session.closed_by_factory for session in DirtyTrackingSession.created)
    finally:
        engine.dispose()
        temp_dir.cleanup()


class ContextRecordingProvider:
    configured = True

    def __init__(self) -> None:
        self.contexts: list[str] = []

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer:
        del question
        self.contexts.append(course_context)
        return ProviderAnswer("A", source="recording")


def runtime_question() -> QuizQuestion:
    return QuizQuestion(
        question_id="q-runtime",
        title="Which answer is correct?",
        question_type=QuizQuestionType.SINGLE,
        type_code="0",
        options=("A. First", "B. Second"),
    )


def test_profile_wrapper_controls_course_and_search_context() -> None:
    provider = ContextRecordingProvider()
    search_calls: list[str] = []
    wrapped = ProfiledAnswerProvider(
        provider=provider,
        course_context_enabled=False,
        search_context=lambda _question, context: (
            search_calls.append(context) or "Untrusted search excerpt"
        ),
    )

    assert wrapped.answer(runtime_question(), course_context="Private course title") == (
        ProviderAnswer("A", source="recording")
    )
    assert search_calls == [""]
    assert provider.contexts == ["Untrusted search excerpt"]


def test_ensemble_referee_and_search_sessions_are_all_closed() -> None:
    temp_dir, engine, secret_box = make_runtime_database()
    try:
        repository = IntegrationSettingRepository(secret_box=secret_box)
        profile = AnswerProfile(
            ensemble_enabled=True,
            models=("model-a", "model-b"),
            referee_model="referee",
            cache_enabled=False,
            web_search_enabled=True,
        )
        with Session(engine) as session, session.begin():
            repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "openai_compatible",
                    "base_url": "https://answers.example.test/v1",
                    "model": "fallback",
                    "profile": profile.to_json(),
                },
                secret_changes={"api_key": "private-key"},
            )
            snapshot = repository.answer_task_snapshot(session)

        DirtyTrackingSession.created.clear()
        factory = AnswerProviderRuntimeFactory(
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            provider_session_factory=DirtyTrackingSession,
            search_context_factory=lambda _session: lambda _question, _course: "",
        )
        with factory.open(snapshot) as binding:
            assert binding.provider is not None

        assert len(DirtyTrackingSession.created) == 4
        assert all(session.closed_by_factory for session in DirtyTrackingSession.created)
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_invalid_profile_snapshot_fails_closed_before_creating_http_sessions() -> None:
    temp_dir, engine, secret_box = make_runtime_database()
    try:
        repository = IntegrationSettingRepository(secret_box=secret_box)
        with Session(engine) as session, session.begin():
            repository.update_answer(
                session,
                changes={
                    "enabled": True,
                    "provider": "tiku_adapter",
                    "endpoint": "https://answers.example.test/query",
                },
                secret_changes={},
            )
            snapshot = repository.answer_task_snapshot(session)
        answer = snapshot["answer"]
        assert isinstance(answer, dict)
        answer["profile"] = {"cache_enabled": "yes"}

        DirtyTrackingSession.created.clear()
        factory = AnswerProviderRuntimeFactory(
            session_factory=make_session_factory(engine),
            secret_box=secret_box,
            provider_session_factory=DirtyTrackingSession,
        )
        with factory.open(snapshot) as binding:
            assert binding.provider is None
            assert binding.unavailable_reason == "answer_snapshot_invalid"
        assert DirtyTrackingSession.created == []
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_runtime_cache_hit_avoids_a_second_provider_call() -> None:
    temp_dir, engine, _secret_box = make_runtime_database()
    try:
        provider = ContextRecordingProvider()
        cached = CachedAnswerProvider(
            provider=provider,
            session_factory=make_session_factory(engine),
            provider_key="answer:revision:profile",
            ttl_seconds=60,
            cache_course_context=True,
        )

        first = cached.answer(runtime_question(), course_context="Course A")
        second = cached.answer(runtime_question(), course_context="Course A")
        third = cached.answer(runtime_question(), course_context="Course B")

        assert first == ProviderAnswer("A", source="recording")
        assert second == ProviderAnswer("A", source="cache:recording")
        assert third == ProviderAnswer("A", source="recording")
        assert provider.contexts == ["Course A", "Course B"]
    finally:
        engine.dispose()
        temp_dir.cleanup()
