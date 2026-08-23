"""
Shared Topic ID helpers for Chroma + Assessment + Analytics.

Accepts:
  - new Assessment-style IDs (G7_S1_PLA_DIVER)
  - sibling skill IDs (G7_S1_PLA_CLASSIF)
  - legacy Chroma IDs (g7_science_ch01)
  - lesson_id (g7_sci_01)

Always resolves to the curriculum primary topic_id used in Chroma metadata.
"""

from __future__ import annotations

from typing import Any

from app.curriculum import (
    Curriculum,
    LessonEntry,
    _skill_to_lesson_map,
    load_curriculum,
)


def clear_topic_caches() -> None:
    _skill_to_lesson_map.cache_clear()
    load_curriculum.cache_clear()


def resolve_lesson(
    topic_or_lesson_or_skill: str,
    *,
    cur: Curriculum | None = None,
) -> LessonEntry | None:
    """Resolve any known id to a LessonEntry."""
    raw = (topic_or_lesson_or_skill or "").strip()
    if not raw:
        return None
    c = cur or load_curriculum()
    le = c.by_lesson_id(raw)
    if le:
        return le
    return c.by_topic_id(raw)


def resolve_chroma_topic_id(
    topic_or_lesson_or_skill: str,
    *,
    cur: Curriculum | None = None,
) -> str:
    """Map any id → primary Chroma topic_id (Assessment-style)."""
    raw = (topic_or_lesson_or_skill or "").strip()
    if not raw:
        return raw
    c = cur or load_curriculum()
    # lesson_id passed directly
    le = c.by_lesson_id(raw)
    if le:
        return le.topic_id
    return c.resolve_chroma_topic_id(raw)


def chroma_theory_where(topic_or_lesson_or_skill: str | None) -> dict[str, Any]:
    """
    Chroma where-filter for theory chunks.

    Resolves legacy / skill / lesson ids to the curriculum primary topic_id,
    and also matches legacy_topic_id so queries work before and after re-ingest.
    """
    if not topic_or_lesson_or_skill:
        return {"content_type": "theory"}
    le = resolve_lesson(topic_or_lesson_or_skill)
    if le:
        ids = [le.topic_id]
        if le.legacy_topic_id and le.legacy_topic_id not in ids:
            ids.append(le.legacy_topic_id)
        topic_clause: dict[str, Any] = (
            {"topic_id": {"$in": ids}} if len(ids) > 1 else {"topic_id": ids[0]}
        )
        return {"$and": [topic_clause, {"content_type": "theory"}]}
    tid = resolve_chroma_topic_id(topic_or_lesson_or_skill)
    return {"$and": [{"topic_id": tid}, {"content_type": "theory"}]}


def topic_public_dict(le: LessonEntry) -> dict[str, Any]:
    return {
        "lesson_id": le.lesson_id,
        "title": le.title,
        "topic_id": le.topic_id,
        "legacy_topic_id": le.legacy_topic_id or None,
        "skill_topic_ids": list(le.skill_topic_ids),
        "skill_section_id": le.skill_section_id or None,
        "grade": le.grade,
        "part": le.part,
        "source": le.source,
    }
