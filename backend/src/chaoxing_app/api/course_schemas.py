from pydantic import BaseModel, Field, field_validator


class CourseResponse(BaseModel):
    course_id: str
    class_id: str
    cpi: str
    title: str
    teacher: str
    description: str


class CourseOutlineRequest(BaseModel):
    class_id: str = Field(min_length=1, max_length=120)
    cpi: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=300)

    @field_validator("class_id", "cpi", "title")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped


class ChapterResponse(BaseModel):
    chapter_id: str
    title: str
    position: int
    job_count: int
    is_completed: bool
    requires_unlock: bool


class CourseOutlineResponse(BaseModel):
    has_locked_chapters: bool
    chapters: list[ChapterResponse]
