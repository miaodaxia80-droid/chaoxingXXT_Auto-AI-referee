from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from chaoxing_app.infrastructure.db.models import AnswerCacheEntry
from chaoxing_app.platform.task_points.quiz import ProviderAnswer, QuizQuestion


def answer_cache_key(
    question: QuizQuestion,
    *,
    provider_key: str,
    course_context: str = "",
) -> tuple[str, str]:
    question_payload = json.dumps(
        {
            "title": question.title.strip(),
            "type": question.question_type.value,
            "options": list(question.options),
            "course_context": course_context.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    question_hash = hashlib.sha256(question_payload.encode("utf-8")).hexdigest()
    key = hashlib.sha256(f"{provider_key}\0{question_hash}".encode()).hexdigest()
    return key, question_hash


class AnswerCacheRepository:
    def get(
        self,
        session: Session,
        question: QuizQuestion,
        *,
        provider_key: str,
        course_context: str = "",
        now: datetime | None = None,
    ) -> ProviderAnswer | None:
        key, _question_hash = answer_cache_key(
            question,
            provider_key=provider_key,
            course_context=course_context,
        )
        entry = session.get(AnswerCacheEntry, key)
        current = now or datetime.now(UTC)
        if entry is None:
            return None
        expires_at = (
            entry.expires_at.replace(tzinfo=UTC)
            if entry.expires_at.tzinfo is None
            else entry.expires_at.astimezone(UTC)
        )
        if expires_at <= current:
            session.delete(entry)
            return None
        value = entry.answer_json.get("value")
        source = entry.answer_json.get("source", "cache")
        if isinstance(value, str) and value.strip() and isinstance(source, str):
            return ProviderAnswer(value=value, source=f"cache:{source}")
        if (
            isinstance(value, list)
            and value
            and all(isinstance(item, str) and item.strip() for item in value)
            and isinstance(source, str)
        ):
            return ProviderAnswer(
                value=tuple(item.strip() for item in value),
                source=f"cache:{source}",
            )
        session.delete(entry)
        return None

    def put(
        self,
        session: Session,
        question: QuizQuestion,
        answer: ProviderAnswer,
        *,
        provider_key: str,
        ttl_seconds: int,
        course_context: str = "",
        now: datetime | None = None,
    ) -> None:
        if not 60 <= ttl_seconds <= 30 * 24 * 60 * 60:
            raise ValueError("answer cache TTL is out of range")
        key, question_hash = answer_cache_key(
            question,
            provider_key=provider_key,
            course_context=course_context,
        )
        current = now or datetime.now(UTC)
        value = answer.value if isinstance(answer.value, str) else list(answer.value)
        entry = session.get(AnswerCacheEntry, key)
        if entry is None:
            entry = AnswerCacheEntry(key=key)
            session.add(entry)
        entry.question_hash = question_hash
        entry.provider_key = provider_key[:160]
        entry.answer_json = {"value": value, "source": answer.source[:120]}
        entry.created_at = current
        entry.expires_at = current + timedelta(seconds=ttl_seconds)
        session.flush()

    def purge_expired(
        self,
        session: Session,
        *,
        now: datetime | None = None,
        limit: int = 1000,
    ) -> int:
        if limit < 1:
            raise ValueError("purge limit must be positive")
        keys = list(
            session.scalars(
                select(AnswerCacheEntry.key)
                .where(AnswerCacheEntry.expires_at <= (now or datetime.now(UTC)))
                .order_by(AnswerCacheEntry.expires_at)
                .limit(limit)
            )
        )
        if not keys:
            return 0
        result = session.execute(delete(AnswerCacheEntry).where(AnswerCacheEntry.key.in_(keys)))
        return len(keys) if result is not None else 0
