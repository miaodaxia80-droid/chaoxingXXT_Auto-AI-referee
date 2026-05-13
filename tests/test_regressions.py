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
from web import tasks as web_tasks
from web.tiku_config import build_effective_tiku_config


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
        self.addCleanup(self._reset_scheduler_state)

    def _reset_scheduler_state(self):
        with web_tasks._scheduler_cond:
            web_tasks._pending_tasks.clear()
            web_tasks._active_tasks.clear()
            web_tasks._active_user_ids.clear()
            web_tasks._queue_hide_on_finish.clear()
            web_tasks._stop_events.clear()

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
        models.update_user(1, {"cookies_data": "uid=demo", "use_cookies": 1})
        models.set_settings({"course_progress_workers": 2})
        overlap = {"active": 0, "max_active": 0}
        lock = threading.Lock()

        def fake_worker(_user, _cookies_data, course):
            with lock:
                overlap["active"] += 1
                overlap["max_active"] = max(overlap["max_active"], overlap["active"])
            time.sleep(0.03)
            with lock:
                overlap["active"] -= 1
            enriched = dict(course)
            enriched.update({
                "total_points": 2,
                "done_points": 1,
            })
            return enriched

        courses = [
            {"courseId": "c1", "clazzId": "z1", "cpi": "p1", "title": "A"},
            {"courseId": "c2", "clazzId": "z2", "cpi": "p2", "title": "B"},
            {"courseId": "c3", "clazzId": "z3", "cpi": "p3", "title": "C"},
        ]

        with mock.patch("web.routes.api._course_progress_worker", side_effect=fake_worker):
            response = self.client.post("/api/users/1/courses/progress", json={"courses": courses})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload), 3)
        self.assertEqual(overlap["max_active"], 2)
        self.assertEqual(payload[0]["total_points"], 2)
        self.assertEqual(payload[0]["done_points"], 1)

    def test_timezone_setting_changes_generated_timestamp_offset(self):
        models.set_settings({"timezone": "UTC"})
        timestamp = models.now_iso()
        self.assertTrue(timestamp.endswith("+00:00"))

    def test_dashboard_can_disable_system_metrics_panel(self):
        models.set_settings({"show_system_metrics": False})
        payload = self.client.get("/api/dashboard").get_json()
        self.assertFalse(payload["show_system_metrics"])
        self.assertIsNone(payload["system_metrics"])

    def test_run_window_settings_are_normalized(self):
        models.set_settings({
            "run_window_enabled": True,
            "run_window_start": "7:5",
            "run_window_end": "23:7",
        })
        settings = models.get_settings()
        self.assertTrue(settings["run_window_enabled"])
        self.assertEqual(settings["run_window_start"], "07:05")
        self.assertEqual(settings["run_window_end"], "23:07")

    def test_queue_pause_and_resume_update_scheduler_state(self):
        pause_response = self.client.post("/api/study/queue/pause")
        self.assertEqual(pause_response.status_code, 200)
        paused = self.client.get("/api/study/queue/status").get_json()
        self.assertTrue(paused["paused"])

        resume_response = self.client.post("/api/study/queue/resume")
        self.assertEqual(resume_response.status_code, 200)
        resumed = self.client.get("/api/study/queue/status").get_json()
        self.assertFalse(resumed["paused"])

    def test_deleting_task_hides_it_from_queue_list(self):
        models.create_user({"username": "demo", "password": "pw"})
        task_id = models.create_task(1, "course-1", "Course 1")

        response = self.client.delete(f"/api/study/task/{task_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/study/tasks").get_json(), [])

    def test_clearing_queue_hides_visible_tasks(self):
        models.create_user({"username": "demo", "password": "pw"})
        models.create_task(1, "course-1", "Course 1")
        models.create_task(1, "course-2", "Course 2")

        response = self.client.post("/api/study/queue/clear")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/study/tasks").get_json(), [])

    def test_force_stopping_active_task_releases_user_slot(self):
        class FakeProcess:
            def __init__(self):
                self.alive = True
                self.exitcode = None

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.alive = False
                self.exitcode = -15

            def join(self, timeout=None):
                return None

            def kill(self):
                self.alive = False
                self.exitcode = -9

        class FakeEvent:
            def __init__(self):
                self.is_stopped = False

            def set(self):
                self.is_stopped = True

        models.create_user({"username": "demo", "password": "pw"})
        task_id = models.create_task(1, "course-1", "Course 1")
        models.update_task_status(task_id, "running")
        process = FakeProcess()
        stop_event = FakeEvent()

        with web_tasks._scheduler_cond:
            web_tasks._active_tasks[task_id] = {
                "process": process,
                "stop_event": stop_event,
                "user_id": 1,
                "course_title": "Course 1",
                "stopping": False,
            }
            web_tasks._active_user_ids.add(1)
            web_tasks._stop_events[task_id] = stop_event

        web_tasks.stop_task(task_id)

        self.assertTrue(stop_event.is_stopped)
        self.assertFalse(process.is_alive())
        self.assertEqual(models.get_task(task_id)["status"], "stopped")
        with web_tasks._scheduler_cond:
            self.assertNotIn(task_id, web_tasks._active_tasks)
            self.assertNotIn(1, web_tasks._active_user_ids)

    def test_user_ai_config_can_fall_back_to_global_defaults(self):
        settings = {
            "tiku_config": {
                "provider": "AI",
                "endpoint": "https://global.example/v1",
                "key": "global-key",
                "model": "gpt-global",
            }
        }
        effective = build_effective_tiku_config(
            {
                "provider": "AI",
                "models": '[{"provider":"AI","endpoint":"","key":"","model":""}]',
            },
            settings,
        )
        self.assertEqual(effective["endpoint"], "https://global.example/v1")
        self.assertEqual(effective["key"], "global-key")
        self.assertEqual(effective["model"], "gpt-global")
        self.assertIn('"endpoint": "https://global.example/v1"', effective["models"])

    def test_intervention_records_can_be_cleared(self):
        models.create_user({"username": "demo", "password": "pw"})
        task_id = models.create_task(1, "course-1", "Course 1")
        models.add_chapter_log(task_id, "Chapter 1", "error", "Needs manual review")

        pending = self.client.get("/api/intervention").get_json()
        self.assertEqual(len(pending), 1)

        response = self.client.delete(f"/api/intervention/{pending[0]['id']}")
        self.assertEqual(response.status_code, 200)

        cleared = self.client.get("/api/intervention").get_json()
        self.assertEqual(cleared, [])

    def test_dashboard_logs_can_be_cleared_without_deleting_rows(self):
        models.create_user({"username": "demo", "password": "pw", "remark": "测试账号"})
        task_id = models.create_task(1, "course-1", "Course 1")
        models.add_chapter_log(task_id, "Chapter 1", "success", "")

        before = models.get_recent_logs()
        self.assertEqual(len(before), 1)

        response = self.client.post("/api/dashboard/logs/clear")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.get_recent_logs(), [])

    def test_dashboard_progress_can_be_cleared_for_finished_tasks(self):
        models.create_user({"username": "demo", "password": "pw"})
        task_id = models.create_task(1, "course-1", "Course 1")
        models.update_task_status(task_id, "done")

        before = models.get_progress()
        self.assertEqual(len(before), 1)

        response = self.client.post("/api/dashboard/progress/clear")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(models.get_progress(), [])

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

    def test_progress_uses_stored_total_chapters(self):
        models.create_user({"username": "demo", "password": "pw"})
        task_id = models.create_task(1, "course-1", "Course 1")
        models.update_task_total_chapters(task_id, 50)
        models.add_chapter_log(task_id, "Chapter 1", "success", "")

        progress = models.get_progress()
        self.assertEqual(progress[0]["done_chapters"], 1)
        self.assertEqual(progress[0]["total_chapters"], 50)

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
