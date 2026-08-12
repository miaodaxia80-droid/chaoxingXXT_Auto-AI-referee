from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from chaoxing_app.platform.errors import PlatformParseError
from chaoxing_app.platform.models import Chapter, Course, CourseFolder, CourseOutline

_CHAPTER_ID_PATTERN = re.compile(r"^cur(?P<chapter_id>\d{1,20})$")


def _attribute(tag: Tag | None, name: str, resource: str) -> str:
    if tag is None:
        raise PlatformParseError(resource, f"missing element for {name}")
    value = tag.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PlatformParseError(resource, f"missing attribute {name}")
    return value.strip()


def _optional_attribute(tag: Tag, name: str) -> str:
    value = tag.get(name)
    return value.strip() if isinstance(value, str) else ""


def _display_text(tag: Tag | None, attribute_name: str = "title") -> str:
    if tag is None:
        return ""
    value = tag.get(attribute_name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return tag.get_text(" ", strip=True)


def parse_course_list(html: str) -> tuple[Course, ...]:
    soup = BeautifulSoup(html, "html.parser")
    courses: list[Course] = []

    for index, element in enumerate(soup.select("div.course"), start=1):
        if element.select_one("a.not-open-tip, div.not-open-tip") is not None:
            continue

        resource = f"course list item {index}"
        course_id = _attribute(element.select_one("input.courseId"), "value", resource)
        clazz_id = _attribute(element.select_one("input.clazzId"), "value", resource)
        link = element.select_one("a[href]")
        href = _attribute(link, "href", resource)
        cpi_values = parse_qs(urlparse(href).query).get("cpi", [])
        if not cpi_values or not cpi_values[0].strip():
            raise PlatformParseError(resource, "missing cpi query parameter")

        title = _display_text(element.select_one("span.course-name"))
        if not title:
            raise PlatformParseError(resource, "missing course title")

        courses.append(
            Course(
                course_id=course_id,
                clazz_id=clazz_id,
                cpi=cpi_values[0].strip(),
                title=title,
                teacher=_display_text(element.select_one("p.color3")),
                description=_display_text(element.select_one("p.margint10")),
                source_id=_optional_attribute(element, "id"),
                role_id=_optional_attribute(element, "roleid"),
                raw_info=_optional_attribute(element, "info"),
            )
        )

    return tuple(courses)


def parse_course_folders(html: str) -> tuple[CourseFolder, ...]:
    soup = BeautifulSoup(html, "html.parser")
    folders: list[CourseFolder] = []
    for index, element in enumerate(soup.select("ul.file-list > li[fileid]"), start=1):
        resource = f"course folder {index}"
        folders.append(
            CourseFolder(
                folder_id=_attribute(element, "fileid", resource),
                name=_display_text(element.select_one("input.rename-input"), "value"),
            )
        )
    return tuple(folders)


def parse_chapter_list(html: str) -> CourseOutline:
    soup = BeautifulSoup(html, "html.parser")
    chapters: list[Chapter] = []

    for item in soup.select("div.chapter_unit li"):
        element = item.find("div", id=True)
        if not isinstance(element, Tag):
            continue
        raw_id = element.get("id")
        if not isinstance(raw_id, str) or not raw_id.startswith("cur"):
            continue
        match = _CHAPTER_ID_PATTERN.fullmatch(raw_id)
        if match is None:
            raise PlatformParseError("chapter list", "invalid chapter identifier")

        chapter_id = match.group("chapter_id")
        resource = f"chapter {chapter_id}"
        title = _display_text(element.select_one("a.clicktitle"), "data-title")
        if not title:
            title = _display_text(element.select_one("a.clicktitle"), "title")
        if not title:
            raise PlatformParseError(resource, "missing chapter title")

        job_count_element = element.select_one("input.knowledgeJobCount")
        job_count = 1
        if job_count_element is not None:
            raw_job_count = _attribute(job_count_element, "value", resource)
            try:
                job_count = int(raw_job_count)
            except ValueError as exc:
                raise PlatformParseError(resource, "invalid job count") from exc
            if job_count < 0:
                raise PlatformParseError(resource, "invalid job count")

        tooltip = element.select_one("span.bntHoverTips")
        tooltip_text = tooltip.get_text(" ", strip=True) if tooltip is not None else ""
        chapters.append(
            Chapter(
                chapter_id=chapter_id,
                title=title,
                job_count=job_count,
                is_completed="已完成" in tooltip_text,
                requires_unlock="解锁" in tooltip_text,
            )
        )

    return CourseOutline(chapters=tuple(chapters))
