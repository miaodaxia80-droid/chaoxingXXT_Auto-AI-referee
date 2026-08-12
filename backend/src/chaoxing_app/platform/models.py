from __future__ import annotations

from dataclasses import dataclass, field


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class LoginCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_text(self.username, "username")
        if not self.password:
            raise ValueError("password must not be empty")


@dataclass(frozen=True, slots=True)
class LoginResult:
    message: str


@dataclass(frozen=True, slots=True)
class Course:
    course_id: str
    clazz_id: str
    cpi: str
    title: str
    teacher: str = ""
    description: str = ""
    source_id: str = ""
    role_id: str = ""
    raw_info: str = ""

    def __post_init__(self) -> None:
        _require_text(self.course_id, "course_id")
        _require_text(self.clazz_id, "clazz_id")
        _require_text(self.cpi, "cpi")
        _require_text(self.title, "title")

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.course_id, self.clazz_id, self.cpi


@dataclass(frozen=True, slots=True)
class CourseFolder:
    folder_id: str
    name: str = ""

    def __post_init__(self) -> None:
        _require_text(self.folder_id, "folder_id")


@dataclass(frozen=True, slots=True)
class Chapter:
    chapter_id: str
    title: str
    job_count: int
    is_completed: bool
    requires_unlock: bool

    def __post_init__(self) -> None:
        _require_text(self.chapter_id, "chapter_id")
        _require_text(self.title, "title")
        if self.job_count < 0:
            raise ValueError("job_count must not be negative")


@dataclass(frozen=True, slots=True)
class CourseOutline:
    chapters: tuple[Chapter, ...]

    @property
    def has_locked_chapters(self) -> bool:
        return any(chapter.requires_unlock for chapter in self.chapters)
