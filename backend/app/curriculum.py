"""Load ordered lesson path + page ranges for chapter-scoped Chroma metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CURRICULUM_PATH = Path(__file__).resolve().parent / "data" / "curriculum.json"


@dataclass(frozen=True)
class LessonEntry:
    lesson_id: str
    title: str
    topic_id: str
    page_start: int
    page_end: int


@dataclass(frozen=True)
class Curriculum:
    course_id: str
    title: str
    description: str
    lessons: tuple[LessonEntry, ...]

    def by_lesson_id(self, lesson_id: str) -> LessonEntry | None:
        lid = lesson_id.strip()
        for le in self.lessons:
            if le.lesson_id == lid:
                return le
        return None

    def topic_id_for_page(self, page: int) -> tuple[str, str | None]:
        """
        Return (chroma topic_id, lesson_id) for a 1-based PDF page.
        Falls back to whole-book bucket if no range matches.
        """
        for le in self.lessons:
            if le.page_start <= page <= le.page_end:
                return le.topic_id, le.lesson_id
        return "g6_science", None

    def next_lesson_id(self, lesson_id: str) -> str | None:
        ids = [le.lesson_id for le in self.lessons]
        try:
            i = ids.index(lesson_id.strip())
        except ValueError:
            return None
        if i + 1 < len(ids):
            return ids[i + 1]
        return None

    def index_of(self, lesson_id: str) -> int | None:
        ids = [le.lesson_id for le in self.lessons]
        try:
            return ids.index(lesson_id.strip())
        except ValueError:
            return None


def _parse_curriculum(raw: dict) -> Curriculum:
    lessons_raw = raw.get("lessons") or []
    lessons: list[LessonEntry] = []
    for row in lessons_raw:
        lessons.append(
            LessonEntry(
                lesson_id=str(row["lesson_id"]),
                title=str(row.get("title") or row["lesson_id"]),
                topic_id=str(row["topic_id"]),
                page_start=int(row["page_start"]),
                page_end=int(row["page_end"]),
            )
        )
    return Curriculum(
        course_id=str(raw.get("course_id") or "unknown"),
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        lessons=tuple(lessons),
    )


@lru_cache(maxsize=1)
def load_curriculum(path: str | None = None) -> Curriculum:
    p = Path(path) if path else CURRICULUM_PATH
    if not p.is_file():
        raise FileNotFoundError(f"Curriculum file not found: {p}")
    with p.open(encoding="utf-8") as f:
        return _parse_curriculum(json.load(f))


def curriculum_public_dict(c: Curriculum) -> dict:
    """Safe for API responses (no secrets)."""
    return {
        "course_id": c.course_id,
        "title": c.title,
        "description": c.description,
        "lessons": [
            {
                "lesson_id": le.lesson_id,
                "title": le.title,
                "topic_id": le.topic_id,
                "page_start": le.page_start,
                "page_end": le.page_end,
            }
            for le in c.lessons
        ],
    }
