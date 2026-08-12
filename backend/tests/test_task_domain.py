import unittest

from chaoxing_app.domain.tasks import (
    ChapterStatus,
    InvalidTaskTransition,
    TaskStatus,
    ensure_task_transition,
    is_terminal_task,
    summarize_chapters,
)


class TaskTransitionTests(unittest.TestCase):
    def test_happy_path_can_finish(self) -> None:
        ensure_task_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
        ensure_task_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
        self.assertTrue(is_terminal_task(TaskStatus.SUCCEEDED))

    def test_terminal_task_cannot_be_restarted(self) -> None:
        with self.assertRaises(InvalidTaskTransition):
            ensure_task_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)

    def test_pause_and_resume_return_to_same_task(self) -> None:
        ensure_task_transition(TaskStatus.RUNNING, TaskStatus.PAUSE_REQUESTED)
        ensure_task_transition(TaskStatus.PAUSE_REQUESTED, TaskStatus.PAUSED)
        ensure_task_transition(TaskStatus.PAUSED, TaskStatus.QUEUED)

    def test_cancel_request_can_race_with_successful_completion(self) -> None:
        ensure_task_transition(TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED)
        ensure_task_transition(TaskStatus.CANCEL_REQUESTED, TaskStatus.SUCCEEDED)


class ChapterSummaryTests(unittest.TestCase):
    def test_only_success_and_already_completed_is_success(self) -> None:
        summary = summarize_chapters([ChapterStatus.SUCCEEDED, ChapterStatus.ALREADY_COMPLETED])
        self.assertEqual(summary.terminal_status, TaskStatus.SUCCEEDED)

    def test_unsubmitted_skipped_or_failed_needs_attention(self) -> None:
        for outcome in (
            ChapterStatus.UNSUBMITTED,
            ChapterStatus.SKIPPED_NOT_OPEN,
            ChapterStatus.FAILED,
        ):
            with self.subTest(outcome=outcome):
                summary = summarize_chapters([ChapterStatus.SUCCEEDED, outcome])
                self.assertEqual(summary.terminal_status, TaskStatus.NEEDS_ATTENTION)

    def test_non_terminal_chapter_cannot_be_aggregated(self) -> None:
        with self.assertRaises(ValueError):
            summarize_chapters([ChapterStatus.RUNNING])

    def test_empty_execution_is_not_success(self) -> None:
        self.assertEqual(summarize_chapters([]).terminal_status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
