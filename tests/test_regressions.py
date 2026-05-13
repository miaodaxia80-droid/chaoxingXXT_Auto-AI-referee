import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from api.base import Account, Chaoxing, SessionManager, StudyResult
import main
from web import create_app, models


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "test_web.db")
        self.original_db_path = models.DB_PATH
        models.DB_PATH = self.db_path
        self.addCleanup(self._restore_db_path)
        models.init_db()
        self.app = create_app()
        self.client = self.app.test_client()

    def _restore_db_path(self):
        models.DB_PATH = self.original_db_path

    def test_resolve_cookies_prefers_account_data_and_can_disable_global_cookie_file(self):
        account = Account(
            "user-a",
            "pw",
            cookies_data="uid=1001; token=abc=def",
            use_cookie_file=False,
        )

        with mock.patch("api.base.use_cookies", return_value={"global": "cookie"}):
            self.assertEqual(
                SessionManager.resolve_cookies(account),
                {"uid": "1001", "token": "abc=def"},
            )
            self.assertEqual(
                SessionManager.resolve_cookies(Account("user-b", "pw", use_cookie_file=False)),
                {},
            )

    def test_chaoxing_sessions_are_isolated_per_account(self):
        first = Chaoxing(
            account=Account(
                "first",
                "pw",
                cookies_data="uid=1001; token=alpha",
                user_agent="Agent/One",
                use_cookie_file=False,
            )
        )
        second = Chaoxing(
            account=Account(
                "second",
                "pw",
                cookies_data="uid=2002; token=beta",
                user_agent="Agent/Two",
                use_cookie_file=False,
            )
        )

        first.session.cookies.set("extra", "only-first")

        self.assertIsNot(first.session, second.session)
        self.assertEqual(first.session.headers["User-Agent"], "Agent/One")
        self.assertEqual(second.session.headers["User-Agent"], "Agent/Two")
        self.assertEqual(first.session.cookies.get("uid"), "1001")
        self.assertEqual(second.session.cookies.get("uid"), "2002")
        self.assertEqual(first.session.cookies.get("extra"), "only-first")
        self.assertIsNone(second.session.cookies.get("extra"))

    def test_get_courses_progress_fetches_without_parallel_overlap(self):
        models.create_user({"username": "demo", "password": "pw"})
        overlap = {"active": 0, "max_active": 0}
        lock = threading.Lock()

        class FakeCx:
            def get_course_list(self):
                return [
                    {"courseId": "c1", "clazzId": "z1", "cpi": "p1", "title": "A"},
                    {"courseId": "c2", "clazzId": "z2", "cpi": "p2", "title": "B"},
                    {"courseId": "c3", "clazzId": "z3", "cpi": "p3", "title": "C"},
                ]

            def get_course_point(self, *_args):
                with lock:
                    overlap["active"] += 1
                    overlap["max_active"] = max(overlap["max_active"], overlap["active"])
                time.sleep(0.03)
                with lock:
                    overlap["active"] -= 1
                return {
                    "points": [
                        {"has_finished": True},
                        {"has_finished": False},
                    ]
                }

        with mock.patch("web.routes.api._make_cx", return_value=FakeCx()):
            response = self.client.get("/api/users/1/courses?progress=1")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload), 3)
        self.assertEqual(overlap["max_active"], 1)
        self.assertEqual(payload[0]["total_points"], 2)
        self.assertEqual(payload[0]["done_points"], 1)

    def test_task_timestamps_follow_status_transitions(self):
        models.create_user({"username": "demo", "password": "pw"})
        task_id = models.create_task(1, "course-1", "Course 1")

        pending = models.get_task(task_id)
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["started_at"])
        self.assertIsNone(pending["finished_at"])

        models.update_task_status(task_id, "running")
        running = models.get_task(task_id)
        self.assertEqual(running["status"], "running")
        self.assertIsNotNone(running["started_at"])
        self.assertIsNone(running["finished_at"])

        models.update_task_status(task_id, "stopped")
        stopped = models.get_task(task_id)
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["started_at"], running["started_at"])
        self.assertIsNotNone(stopped["finished_at"])

    def test_process_chapter_propagates_stopped_result(self):
        callbacks = []

        fake_chaoxing = SimpleNamespace(
            rate_limiter=SimpleNamespace(limit_rate=lambda **_kwargs: None),
            get_job_list=lambda _course, _point: (
                [{"type": "video", "jobid": "1"}, {"type": "video", "jobid": "2"}],
                {"notOpen": False},
            ),
            _last_quiz_low_coverage=False,
        )

        with mock.patch.object(
            main,
            "process_job",
            side_effect=[StudyResult.SUCCESS, StudyResult.STOPPED],
        ):
            result = main.process_chapter(
                fake_chaoxing,
                {"title": "Course"},
                {"title": "Chapter 1", "has_finished": False},
                1.0,
                on_complete=lambda title, state, message: callbacks.append((title, state, message)),
            )

        self.assertEqual(result, main.ChapterResult.STOPPED)
        self.assertEqual(
            callbacks,
            [("Chapter 1", main.ChapterResult.STOPPED, "Stopped by user")],
        )


if __name__ == "__main__":
    unittest.main()
