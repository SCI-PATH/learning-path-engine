"""Learning-path chapter unlock rules (mirrors frontend chapterGameProgress.ts)."""

from __future__ import annotations

from typing import Any, Sequence

from app.curriculum import Curriculum, LessonEntry, load_curriculum

GAME_PASS_THRESHOLD = 0.65


def is_lesson_game_complete(
    lesson_id: str | None,
    quiz_by_lesson: dict[str, Any] | None,
) -> bool:
    lid = str(lesson_id or "").strip()
    if not lid:
        return False
    row = (quiz_by_lesson or {}).get(lid) or {}
    last = row.get("last_score")
    try:
        score = float(last)
    except (TypeError, ValueError):
        return False
    return score >= GAME_PASS_THRESHOLD


def is_lesson_game_complete_with_legacy(
    lesson_id: str,
    quiz_by_lesson: dict[str, Any],
    completed_lesson_ids: Sequence[str],
    grade_lessons: Sequence[LessonEntry],
) -> bool:
    if is_lesson_game_complete(lesson_id, quiz_by_lesson):
        return True
    idx = next(
        (i for i, le in enumerate(grade_lessons) if le.lesson_id == lesson_id),
        -1,
    )
    if idx < 0:
        return False
    done = {str(x) for x in completed_lesson_ids}
    return any(
        str(le.lesson_id or "") in done for le in grade_lessons[idx + 1 :]
    )


def find_pending_chapter_game(
    grade_lessons: Sequence[LessonEntry],
    completed_lesson_ids: Sequence[str],
    quiz_by_lesson: dict[str, Any],
) -> LessonEntry | None:
    done = {str(x) for x in completed_lesson_ids}
    for lesson in grade_lessons:
        lesson_id = str(lesson.lesson_id or "").strip()
        if not lesson_id or lesson_id not in done:
            continue
        if is_lesson_game_complete_with_legacy(
            lesson_id,
            quiz_by_lesson,
            completed_lesson_ids,
            grade_lessons,
        ):
            continue
        return lesson
    return None


def is_chapter_unlocked_for_learning(
    index: int,
    grade_lessons: Sequence[LessonEntry],
    quiz_by_lesson: dict[str, Any],
    completed_lesson_ids: Sequence[str],
) -> bool:
    if index < 0 or index >= len(grade_lessons):
        return False
    lesson = grade_lessons[index]
    lesson_id = str(lesson.lesson_id or "").strip()

    pending = find_pending_chapter_game(grade_lessons, completed_lesson_ids, quiz_by_lesson)
    if not pending:
        return True

    return lesson_id == str(pending.lesson_id or "").strip()


def assert_lesson_unlocked_for_learning(
    lesson_id: str,
    *,
    user_id: str,
    completed_lesson_ids: Sequence[str],
    quiz_by_lesson: dict[str, Any],
    grade: int | None,
    cur: Curriculum | None = None,
) -> None:
    """Raise ValueError when a learner tries to open a locked chapter."""
    lid = str(lesson_id or "").strip()
    if not lid:
        raise ValueError("lesson_id is required")
    c = cur or load_curriculum()
    entry = c.by_lesson_id(lid)
    if not entry or entry.grade is None:
        return
    grade_lessons = list(c.lessons_for_grade(int(entry.grade)))
    idx = next((i for i, le in enumerate(grade_lessons) if le.lesson_id == lid), -1)
    if idx < 0:
        return
    if is_chapter_unlocked_for_learning(
        idx, grade_lessons, quiz_by_lesson, completed_lesson_ids
    ):
        return
    pending = find_pending_chapter_game(
        grade_lessons, completed_lesson_ids, quiz_by_lesson
    )
    if pending:
        title = getattr(pending, "title", None) or pending.lesson_id
        raise ValueError(
            f"Finish the farm game for {title} first — other chapters are locked until then."
        )
    raise ValueError("This chapter is not available yet.")
