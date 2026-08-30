"""Tests for chapter unlock rules."""

from app.chapter_unlock import (
    find_pending_chapter_game,
    is_chapter_unlocked_for_learning,
    is_lesson_game_complete,
)
from app.curriculum import LessonEntry


def _lesson(lesson_id: str) -> LessonEntry:
    return LessonEntry(
        lesson_id=lesson_id,
        title=lesson_id,
        topic_id=f"t_{lesson_id}",
        page_start=1,
        page_end=2,
        grade=7,
    )


def test_no_pending_farm_unlocks_entire_syllabus():
    lessons = [_lesson("g7_sci_01"), _lesson("g7_sci_02"), _lesson("g7_sci_03")]
    completed = ["g7_sci_01"]
    quiz = {"g7_sci_01": {"attempts": 1, "last_score": 1.0}}

    assert find_pending_chapter_game(lessons, completed, quiz) is None
    for i in range(3):
        assert is_chapter_unlocked_for_learning(i, lessons, quiz, completed) is True


def test_pending_farm_locks_everything_except_that_chapter():
    lessons = [_lesson("g7_sci_01"), _lesson("g7_sci_02"), _lesson("g7_sci_03")]
    completed = ["g7_sci_01", "g7_sci_03"]
    quiz = {"g7_sci_01": {"attempts": 1, "last_score": 1.0}}

    pending = find_pending_chapter_game(lessons, completed, quiz)
    assert pending is not None
    assert pending.lesson_id == "g7_sci_03"

    assert is_chapter_unlocked_for_learning(0, lessons, quiz, completed) is False
    assert is_chapter_unlocked_for_learning(1, lessons, quiz, completed) is False
    assert is_chapter_unlocked_for_learning(2, lessons, quiz, completed) is True


def test_is_lesson_game_complete_threshold():
    quiz = {"g7_sci_01": {"attempts": 1, "last_score": 0.64}}
    assert is_lesson_game_complete("g7_sci_01", quiz) is False
    quiz["g7_sci_01"]["last_score"] = 0.65
    assert is_lesson_game_complete("g7_sci_01", quiz) is True
