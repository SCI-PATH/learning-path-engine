"""Load ordered lesson path + page ranges for chapter-scoped Chroma metadata.

Supports multi-grade books (G6–G9) in curriculum.json under a top-level \"books\" array.
Legacy single-course curriculum.json (flat lessons) is still accepted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CURRICULUM_PATH = Path(__file__).resolve().parent / "data" / "curriculum.json"
SKILL_HIERARCHY_PATH = Path(__file__).resolve().parent / "data" / "skill_hierarchy.json"

_SECTION_RE = re.compile(r"^(G\d+_S\d+)_", re.IGNORECASE)


@lru_cache(maxsize=1)
def _skill_to_lesson_map() -> dict[str, str]:
    """Assessment / legacy / skill id → lesson_id (from skill_hierarchy.json)."""
    if not SKILL_HIERARCHY_PATH.is_file():
        return {}
    raw = json.loads(SKILL_HIERARCHY_PATH.read_text(encoding="utf-8"))
    mapping = raw.get("skill_to_lesson") or {}
    return {str(k).strip(): str(v).strip() for k, v in mapping.items() if k and v}


@dataclass(frozen=True)
class LessonEntry:
    lesson_id: str
    title: str
    topic_id: str
    page_start: int
    page_end: int
    source: str = ""
    grade: int | None = None
    part: int | None = None
    course_id: str = ""
    legacy_topic_id: str = ""
    skill_topic_ids: tuple[str, ...] = ()
    skill_section_id: str = ""


@dataclass(frozen=True)
class BookEntry:
    course_id: str
    title: str
    description: str
    source: str
    grade: int | None
    part: int | None
    lessons: tuple[LessonEntry, ...]


@dataclass(frozen=True)
class Curriculum:
    course_id: str
    title: str
    description: str
    lessons: tuple[LessonEntry, ...]
    books: tuple[BookEntry, ...] = ()

    def by_lesson_id(self, lesson_id: str) -> LessonEntry | None:
        lid = lesson_id.strip()
        for le in self.lessons:
            if le.lesson_id == lid:
                return le
        return None

    def by_topic_id(self, topic_id: str) -> LessonEntry | None:
        """Resolve assessment/skill Topic ID (or legacy Chroma id) to a lesson."""
        tid = (topic_id or "").strip()
        if not tid:
            return None
        for le in self.lessons:
            if le.topic_id == tid:
                return le
            if le.legacy_topic_id and le.legacy_topic_id == tid:
                return le
            if tid in le.skill_topic_ids:
                return le
            if le.skill_section_id and le.skill_section_id == tid:
                return le

        # skill_hierarchy aliases (Excel + legacy)
        mapped_lid = _skill_to_lesson_map().get(tid)
        if mapped_lid:
            hit = self.by_lesson_id(mapped_lid)
            if hit:
                return hit

        # Excel-only skill under same section: G8_S2_TIS_PLANT → first G8_S2 lesson
        m = _SECTION_RE.match(tid)
        if m:
            section = m.group(1).upper()
            for le in self.lessons:
                if (le.skill_section_id or "").upper() == section:
                    return le
        return None

    def resolve_chroma_topic_id(self, topic_or_skill_id: str) -> str:
        """
        Map any assessment skill ID / legacy id / lesson topic to the Chroma topic_id.
        Example: G7_S1_PLA_CLASSIF → G7_S1_PLA_DIVER (chapter primary).
        """
        raw = (topic_or_skill_id or "").strip()
        if not raw:
            return raw
        le = self.by_topic_id(raw)
        return le.topic_id if le else raw

    def book_for_source(self, source: str) -> BookEntry | None:
        name = Path(source).name
        for book in self.books:
            if book.source == name or Path(book.source).name == name:
                return book
        return None

    def topic_id_for_page(
        self,
        page: int,
        *,
        source: str | None = None,
    ) -> tuple[str, str | None]:
        """
        Return (chroma topic_id, lesson_id) for a 1-based PDF page.
        When source is set, only that book's page ranges are used.
        Falls back to a whole-book bucket if no range matches.
        """
        lessons = self.lessons
        if source:
            book = self.book_for_source(source)
            if book:
                lessons = book.lessons
            else:
                lessons = tuple(le for le in self.lessons if Path(le.source).name == Path(source).name)

        for le in lessons:
            if le.page_start <= page <= le.page_end:
                return le.topic_id, le.lesson_id

        if source:
            stem = Path(source).stem.lower().replace(" ", "_")
            return f"{stem}_misc", None
        return "G6_S1_ORG_CHARS", None

    def lessons_for_grade(self, grade: int) -> tuple[LessonEntry, ...]:
        """
        Merged path for one grade: Part I then Part II in curriculum order.
        Grade 6 has a single book; 7/8/9 combine both parts.
        """
        g = int(grade)
        return tuple(le for le in self.lessons if le.grade == g)

    def available_grades(self) -> list[int]:
        grades = sorted({le.grade for le in self.lessons if le.grade is not None})
        return grades

    def first_lesson_for_grade(self, grade: int) -> LessonEntry | None:
        pool = self.lessons_for_grade(grade)
        return pool[0] if pool else None

    def next_lesson_id(self, lesson_id: str) -> str | None:
        """Next lesson within the same grade (Parts I+II merged for G7–G9)."""
        entry = self.by_lesson_id(lesson_id)
        if not entry:
            return None
        if entry.grade is not None:
            pool = list(self.lessons_for_grade(entry.grade))
        else:
            pool = [le for le in self.lessons if le.course_id == entry.course_id] or list(self.lessons)
        ids = [le.lesson_id for le in pool]
        try:
            i = ids.index(lesson_id.strip())
        except ValueError:
            return None
        if i + 1 < len(ids):
            return ids[i + 1]
        return None

    def index_of(self, lesson_id: str) -> int | None:
        entry = self.by_lesson_id(lesson_id)
        if not entry:
            return None
        if entry.grade is not None:
            pool = list(self.lessons_for_grade(entry.grade))
        else:
            pool = [le for le in self.lessons if le.course_id == entry.course_id] or list(self.lessons)
        ids = [le.lesson_id for le in pool]
        try:
            return ids.index(lesson_id.strip())
        except ValueError:
            return None


def _lesson_from_row(
    row: dict,
    *,
    source: str = "",
    grade: int | None = None,
    part: int | None = None,
    course_id: str = "",
) -> LessonEntry:
    skill_ids = row.get("skill_topic_ids") or []
    if isinstance(skill_ids, str):
        skill_ids = [s.strip() for s in skill_ids.split(",") if s.strip()]
    return LessonEntry(
        lesson_id=str(row["lesson_id"]),
        title=str(row.get("title") or row["lesson_id"]),
        topic_id=str(row["topic_id"]),
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        source=str(row.get("source") or source),
        grade=int(row["grade"]) if row.get("grade") is not None else grade,
        part=int(row["part"]) if row.get("part") is not None else part,
        course_id=str(row.get("course_id") or course_id),
        legacy_topic_id=str(row.get("legacy_topic_id") or ""),
        skill_topic_ids=tuple(str(s) for s in skill_ids),
        skill_section_id=str(row.get("skill_section_id") or ""),
    )


def _parse_curriculum(raw: dict) -> Curriculum:
    books_raw = raw.get("books")
    if books_raw:
        books: list[BookEntry] = []
        all_lessons: list[LessonEntry] = []
        for book in books_raw:
            course_id = str(book.get("course_id") or "unknown")
            source = str(book.get("source") or "")
            grade = int(book["grade"]) if book.get("grade") is not None else None
            part = int(book["part"]) if book.get("part") is not None else None
            lessons = tuple(
                _lesson_from_row(
                    row,
                    source=source,
                    grade=grade,
                    part=part,
                    course_id=course_id,
                )
                for row in (book.get("lessons") or [])
            )
            books.append(
                BookEntry(
                    course_id=course_id,
                    title=str(book.get("title") or course_id),
                    description=str(book.get("description") or ""),
                    source=source,
                    grade=grade,
                    part=part,
                    lessons=lessons,
                )
            )
            all_lessons.extend(lessons)

        default_id = str(raw.get("default_course_id") or (books[0].course_id if books else "unknown"))
        default_book = next((b for b in books if b.course_id == default_id), books[0] if books else None)
        return Curriculum(
            course_id=default_id,
            title=default_book.title if default_book else "Science",
            description=default_book.description if default_book else "",
            lessons=tuple(all_lessons),
            books=tuple(books),
        )

    # Legacy flat curriculum
    lessons = tuple(_lesson_from_row(row) for row in (raw.get("lessons") or []))
    return Curriculum(
        course_id=str(raw.get("course_id") or "unknown"),
        title=str(raw.get("title") or ""),
        description=str(raw.get("description") or ""),
        lessons=lessons,
        books=(),
    )


@lru_cache(maxsize=1)
def load_curriculum(path: str | None = None) -> Curriculum:
    p = Path(path) if path else CURRICULUM_PATH
    if not p.is_file():
        raise FileNotFoundError(f"Curriculum file not found: {p}")
    with p.open(encoding="utf-8") as f:
        return _parse_curriculum(json.load(f))


def curriculum_public_dict(c: Curriculum, *, grade: int | None = None) -> dict:
    """Safe for API responses. If grade is set, only that grade's merged path is returned."""
    lessons_src = c.lessons_for_grade(grade) if grade is not None else c.lessons
    lessons = [
        {
            "lesson_id": le.lesson_id,
            "title": le.title,
            "topic_id": le.topic_id,
            "legacy_topic_id": le.legacy_topic_id or None,
            "skill_topic_ids": list(le.skill_topic_ids),
            "skill_section_id": le.skill_section_id or None,
            "page_start": le.page_start,
            "page_end": le.page_end,
            "source": le.source,
            "grade": le.grade,
            "part": le.part,
            "course_id": le.course_id,
            "display_title": _display_title(le),
        }
        for le in lessons_src
    ]
    books = [
        {
            "course_id": b.course_id,
            "title": b.title,
            "description": b.description,
            "source": b.source,
            "grade": b.grade,
            "part": b.part,
            "lesson_count": len(b.lessons),
        }
        for b in c.books
        if grade is None or b.grade == grade
    ]
    grade_title = f"Grade {grade} Science" if grade is not None else c.title
    return {
        "course_id": c.course_id if grade is None else f"g{grade}_science",
        "title": grade_title,
        "description": (
            f"Merged Part I + Part II path for Grade {grade}."
            if grade in (7, 8, 9)
            else c.description
        ),
        "grade": grade,
        "available_grades": c.available_grades(),
        "books": books,
        "lessons": lessons,
    }


def _display_title(le: LessonEntry) -> str:
    """UI title: grade-scoped, parts merged (no P1/P2 in the label)."""
    if le.grade is None:
        return le.title
    return f"G{le.grade}: {le.title}"
