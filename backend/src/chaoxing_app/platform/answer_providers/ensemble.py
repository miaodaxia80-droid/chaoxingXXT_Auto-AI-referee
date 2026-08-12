from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

from chaoxing_app.platform.task_points.quiz import (
    AnswerProvider,
    ProviderAnswer,
    ProviderAnswerValue,
    QuizQuestion,
)


def _answer_key(answer: ProviderAnswer | ProviderAnswerValue) -> tuple[str, ...] | None:
    if answer is None:
        return None
    value = answer.value if isinstance(answer, ProviderAnswer) else answer
    if isinstance(value, str):
        normalized_text = value.strip()
        return (normalized_text,) if normalized_text else None
    if isinstance(value, Sequence):
        normalized_items = tuple(str(item).strip() for item in value)
        return normalized_items if normalized_items and all(normalized_items) else None
    return None


class AnswerEnsembleProvider:
    """Query isolated providers in parallel and referee disagreements."""

    configured = True

    def __init__(
        self,
        providers: Sequence[AnswerProvider],
        *,
        referee: AnswerProvider | None = None,
        max_workers: int = 4,
        search_context: Callable[[QuizQuestion, str], str] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("ensemble requires at least one provider")
        if not 1 <= max_workers <= 8:
            raise ValueError("ensemble max_workers must be between one and eight")
        self._providers = tuple(providers)
        self._referee = referee
        self._max_workers = min(max_workers, len(self._providers))
        self._search_context = search_context

    def answer(
        self,
        question: QuizQuestion,
        *,
        course_context: str = "",
    ) -> ProviderAnswer | None:
        context = course_context.strip()
        if self._search_context is not None:
            try:
                search = self._search_context(question, context).strip()
            except Exception:
                search = ""
            if search:
                context = f"{context}\n\nSearch context:\n{search}".strip()

        candidates: list[tuple[str, ...]] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [
                executor.submit(provider.answer, question, course_context=context)
                for provider in self._providers
            ]
            for future in as_completed(futures):
                try:
                    candidate = _answer_key(future.result())
                except Exception:
                    candidate = None
                if candidate is not None:
                    candidates.append(candidate)

        if not candidates:
            return None
        counts = Counter(candidates)
        winner, votes = counts.most_common(1)[0]
        if len(counts) == 1 or votes > len(candidates) / 2:
            return self._result(winner, source="ensemble_consensus")

        if self._referee is not None:
            candidate_text = "\n".join(
                f"Candidate {index + 1}: {' | '.join(candidate)}"
                for index, candidate in enumerate(counts)
            )
            referee_context = (
                f"{context}\n\nChoose the most accurate answer from these candidates "
                f"and return only that answer:\n{candidate_text}"
            ).strip()
            try:
                refereed = _answer_key(
                    self._referee.answer(question, course_context=referee_context)
                )
            except Exception:
                refereed = None
            if refereed is not None:
                return self._result(refereed, source="ensemble_referee")
        return self._result(candidates[0], source="ensemble_fallback")

    @staticmethod
    def _result(value: tuple[str, ...], *, source: str) -> ProviderAnswer:
        resolved: str | tuple[str, ...] = value[0] if len(value) == 1 else value
        return ProviderAnswer(value=resolved, source=source)
