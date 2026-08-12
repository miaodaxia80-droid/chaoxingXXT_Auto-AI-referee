from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.answer_cache import AnswerCacheRepository
from chaoxing_app.infrastructure.db.base import Base
from chaoxing_app.platform.answer_providers.ensemble import AnswerEnsembleProvider
from chaoxing_app.platform.task_points.quiz import (
    ProviderAnswer,
    QuizQuestion,
    QuizQuestionType,
)


class FixedProvider:
    configured = True

    def __init__(self, value: str | None) -> None:
        self.value = value
        self.contexts: list[str] = []

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer | None:
        del question
        self.contexts.append(course_context)
        return ProviderAnswer(self.value) if self.value is not None else None


def question() -> QuizQuestion:
    return QuizQuestion(
        question_id="q-1",
        title="Which option is correct?",
        question_type=QuizQuestionType.SINGLE,
        type_code="0",
        options=("A. Alpha", "B. Beta"),
    )


def test_ensemble_uses_consensus_and_referee_for_disagreement() -> None:
    consensus = AnswerEnsembleProvider(
        [FixedProvider("A"), FixedProvider("A"), FixedProvider("B")],
        max_workers=3,
    )
    result = consensus.answer(question(), course_context="Course")
    assert result == ProviderAnswer(value="A", source="ensemble_consensus")

    referee = FixedProvider("B")
    divided = AnswerEnsembleProvider(
        [FixedProvider("A"), FixedProvider("B")],
        referee=referee,
        search_context=lambda _question, _course: "verified source",
    )
    result = divided.answer(question(), course_context="Course")
    assert result == ProviderAnswer(value="B", source="ensemble_referee")
    assert "Candidate 1:" in referee.contexts[0]
    assert "Search context:" in referee.contexts[0]


def test_sqlite_answer_cache_is_provider_scoped_and_expires(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'cache.db').as_posix()}")
    Base.metadata.create_all(engine)
    cache = AnswerCacheRepository()
    now = datetime(2026, 8, 12, tzinfo=UTC)
    try:
        with Session(engine) as session:
            cache.put(
                session,
                question(),
                ProviderAnswer(value="A", source="model-a"),
                provider_key="profile-revision-1",
                ttl_seconds=60,
                now=now,
            )
            session.commit()

        with Session(engine) as session:
            cached = cache.get(
                session,
                question(),
                provider_key="profile-revision-1",
                now=now + timedelta(seconds=30),
            )
            assert cached == ProviderAnswer(value="A", source="cache:model-a")
            assert cache.get(
                session,
                question(),
                provider_key="profile-revision-2",
                now=now + timedelta(seconds=30),
            ) is None
            assert cache.get(
                session,
                question(),
                provider_key="profile-revision-1",
                now=now + timedelta(seconds=61),
            ) is None
    finally:
        engine.dispose()


def test_sqlite_answer_cache_can_be_scoped_by_course_context(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'course-cache.db').as_posix()}")
    Base.metadata.create_all(engine)
    cache = AnswerCacheRepository()
    try:
        with Session(engine) as session:
            cache.put(
                session,
                question(),
                ProviderAnswer(value="A", source="model-a"),
                provider_key="profile-revision-1",
                course_context="Course A",
                ttl_seconds=60,
            )
            session.commit()

        with Session(engine) as session:
            assert cache.get(
                session,
                question(),
                provider_key="profile-revision-1",
                course_context="Course A",
            ) is not None
            assert cache.get(
                session,
                question(),
                provider_key="profile-revision-1",
                course_context="Course B",
            ) is None
    finally:
        engine.dispose()
