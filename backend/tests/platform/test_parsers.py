import unittest
from pathlib import Path

from chaoxing_app.platform.errors import PlatformParseError
from chaoxing_app.platform.parsers import (
    parse_chapter_list,
    parse_course_folders,
    parse_course_list,
)

FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class CourseListParserTests(unittest.TestCase):
    def test_parses_open_courses_and_skips_closed_courses(self) -> None:
        courses = parse_course_list(fixture("course_list_root.html"))

        self.assertEqual(len(courses), 1)
        course = courses[0]
        self.assertEqual(course.course_id, "course-100")
        self.assertEqual(course.clazz_id, "clazz-100")
        self.assertEqual(course.cpi, "cpi-100")
        self.assertEqual(course.title, "Network Security")
        self.assertEqual(course.teacher, "Teacher Chen")
        self.assertEqual(course.description, "A sanitized course description")

    def test_cpi_can_be_the_last_query_parameter(self) -> None:
        courses = parse_course_list(fixture("course_list_folder.html"))
        self.assertEqual(courses[0].cpi, "cpi-100")

    def test_missing_required_course_field_is_explicit(self) -> None:
        with self.assertRaisesRegex(PlatformParseError, "missing element for value"):
            parse_course_list(
                '<div class="course"><input class="courseId" value="course-1"></div>'
            )

    def test_parses_course_folders(self) -> None:
        folders = parse_course_folders(fixture("course_folders.html"))
        self.assertEqual(
            [(item.folder_id, item.name) for item in folders],
            [("folder-7", "Archive")],
        )


class ChapterListParserTests(unittest.TestCase):
    def test_parses_completion_lock_and_job_count(self) -> None:
        outline = parse_chapter_list(fixture("chapter_list.html"))

        self.assertEqual(len(outline.chapters), 3)
        self.assertEqual(outline.chapters[0].chapter_id, "10001")
        self.assertEqual(outline.chapters[0].job_count, 2)
        self.assertTrue(outline.chapters[0].is_completed)
        self.assertFalse(outline.chapters[0].requires_unlock)
        self.assertEqual(outline.chapters[1].title, "2. Locked chapter")
        self.assertTrue(outline.chapters[1].requires_unlock)
        self.assertEqual(outline.chapters[2].job_count, 1)
        self.assertTrue(outline.has_locked_chapters)

    def test_invalid_job_count_is_explicit(self) -> None:
        html = """
        <div class="chapter_unit"><li><div id="cur42">
          <a class="clicktitle">Chapter</a>
          <input class="knowledgeJobCount" value="many">
        </div></li></div>
        """
        with self.assertRaisesRegex(PlatformParseError, "invalid job count"):
            parse_chapter_list(html)


if __name__ == "__main__":
    unittest.main()
