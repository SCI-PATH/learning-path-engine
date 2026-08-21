"""SQLite persistence for per-learner lesson path state."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from app.chroma_setup import BACKEND_ROOT
from app.curriculum import Curriculum, load_curriculum
from app.mastery_profile import profile_display_label
from app.prompts import normalize_profile

DB_PATH = BACKEND_ROOT / "data" / "progress.db"

ProgressAction = Literal["set_current", "mark_complete", "record_quiz", "save_state", "set_grade"]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learner_progress (
              user_id TEXT PRIMARY KEY,
              current_lesson_id TEXT NOT NULL,
              completed_json TEXT NOT NULL DEFAULT '[]',
              quiz_json TEXT NOT NULL DEFAULT '{}',
              session_json TEXT NOT NULL DEFAULT '{}',
              grade INTEGER,
              updated_at TEXT NOT NULL
            )
            """
        )
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(learner_progress)").fetchall()
        }
        if "session_json" not in cols:
            conn.execute(
                "ALTER TABLE learner_progress ADD COLUMN session_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "grade" not in cols:
            conn.execute("ALTER TABLE learner_progress ADD COLUMN grade INTEGER")
        if "mastery_score" not in cols:
            conn.execute("ALTER TABLE learner_progress ADD COLUMN mastery_score REAL")
        if "derived_profile" not in cols:
            conn.execute("ALTER TABLE learner_progress ADD COLUMN derived_profile TEXT")
        if "mastery_updated_at" not in cols:
            conn.execute("ALTER TABLE learner_progress ADD COLUMN mastery_updated_at TEXT")
        if "mastery_source" not in cols:
            conn.execute("ALTER TABLE learner_progress ADD COLUMN mastery_source TEXT")
        conn.commit()
    finally:
        conn.close()


def _default_state(cur: Curriculum, grade: int | None = None) -> dict[str, Any]:
    g = grade if grade is not None else (cur.available_grades()[0] if cur.available_grades() else 6)
    first_entry = cur.first_lesson_for_grade(g)
    first = first_entry.lesson_id if first_entry else (cur.lessons[0].lesson_id if cur.lessons else "")
    return {
        "current_lesson_id": first,
        "completed_lesson_ids": [],
        "quiz_by_lesson": {},
        "session_state": {"lesson_id": first, "step_index": 0, "grade": g},
        "grade": g,
        "updated_at": "",
    }


def get_progress(user_id: str, cur: Curriculum | None = None) -> dict[str, Any]:
    init_db()
    c = cur or load_curriculum()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT current_lesson_id, completed_json, quiz_json, session_json, grade,
                   mastery_score, derived_profile, mastery_updated_at, mastery_source, updated_at
            FROM learner_progress WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            st = _default_state(c)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "INSERT INTO learner_progress (user_id, current_lesson_id, completed_json, quiz_json, session_json, grade, updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    user_id,
                    st["current_lesson_id"],
                    json.dumps(st["completed_lesson_ids"]),
                    json.dumps(st["quiz_by_lesson"]),
                    json.dumps(st["session_state"]),
                    st["grade"],
                    now,
                ),
            )
            conn.commit()
            return {
                **st,
                "updated_at": now,
                "user_id": user_id,
                "mastery_score": None,
                "derived_profile": None,
                "mastery_updated_at": None,
                "mastery_source": None,
            }
        completed = json.loads(row["completed_json"] or "[]")
        quiz = json.loads(row["quiz_json"] or "{}")
        session = json.loads(row["session_json"] or "{}")
        grade = row["grade"]
        if grade is None and session.get("grade") is not None:
            grade = int(session["grade"])
        if grade is None:
            entry = c.by_lesson_id(row["current_lesson_id"])
            grade = entry.grade if entry and entry.grade is not None else 6
        return {
            "user_id": user_id,
            "current_lesson_id": row["current_lesson_id"],
            "completed_lesson_ids": completed,
            "quiz_by_lesson": quiz,
            "session_state": session,
            "grade": int(grade) if grade is not None else 6,
            "mastery_score": row["mastery_score"],
            "derived_profile": row["derived_profile"],
            "mastery_updated_at": row["mastery_updated_at"],
            "mastery_source": row["mastery_source"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def update_progress(
    user_id: str,
    *,
    action: ProgressAction,
    lesson_id: str = "",
    score: float | None = None,
    step_index: int | None = None,
    grade: int | None = None,
    pass_threshold: float = 0.65,
    cur: Curriculum | None = None,
) -> dict[str, Any]:
    """Apply an action and return fresh state. For record_quiz, may auto-advance within the same grade."""
    init_db()
    c = cur or load_curriculum()
    state = get_progress(user_id, c)
    completed = list(state["completed_lesson_ids"])
    quiz = dict(state["quiz_by_lesson"])
    session = dict(state.get("session_state") or {})
    current = state["current_lesson_id"]
    current_grade = int(state.get("grade") or session.get("grade") or 6)
    lid = (lesson_id or "").strip()

    if action == "set_grade":
        if grade is None:
            raise ValueError("set_grade requires grade")
        current_grade = int(grade)
        first = c.first_lesson_for_grade(current_grade)
        if first:
            current = first.lesson_id
            session = {"lesson_id": current, "step_index": 0, "grade": current_grade}
        else:
            session = {**session, "grade": current_grade}
    elif action == "set_current":
        current = lid
        entry = c.by_lesson_id(lid)
        if entry and entry.grade is not None:
            current_grade = int(entry.grade)
        session = {"lesson_id": lid, "step_index": 0, "grade": current_grade}
    elif action == "mark_complete":
        if lid and lid not in completed:
            completed.append(lid)
    elif action == "record_quiz":
        prev = quiz.get(lid, {"attempts": 0, "last_score": None})
        attempts = int(prev.get("attempts") or 0) + 1
        last = float(score) if score is not None else None
        quiz[lid] = {"attempts": attempts, "last_score": last}
        if last is not None and last >= pass_threshold:
            if lid not in completed:
                completed.append(lid)
            nxt = c.next_lesson_id(lid)
            if nxt:
                current = nxt
                entry = c.by_lesson_id(nxt)
                if entry and entry.grade is not None:
                    current_grade = int(entry.grade)
                session = {"lesson_id": nxt, "step_index": 0, "grade": current_grade}
    elif action == "save_state":
        if step_index is not None and step_index >= 0:
            session = {"lesson_id": lid, "step_index": int(step_index), "grade": current_grade}

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE learner_progress
            SET current_lesson_id = ?, completed_json = ?, quiz_json = ?, session_json = ?, grade = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (
                current,
                json.dumps(completed),
                json.dumps(quiz),
                json.dumps(session),
                current_grade,
                now,
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "user_id": user_id,
        "current_lesson_id": current,
        "completed_lesson_ids": completed,
        "quiz_by_lesson": quiz,
        "session_state": session,
        "grade": current_grade,
        "mastery_score": state.get("mastery_score"),
        "derived_profile": state.get("derived_profile"),
        "mastery_updated_at": state.get("mastery_updated_at"),
        "mastery_source": state.get("mastery_source"),
        "updated_at": now,
    }


def set_learner_profile(
    user_id: str,
    profile: str,
    *,
    source: str = "learner_profile_analytics",
    grade: int | None = None,
) -> dict[str, Any]:
    """
    Store knowledge category from Learner Profile Analytics (weak | average | strong | smart).
    No score calculation — category is authoritative.
    """
    init_db()
    state = get_progress(user_id)
    normalized = normalize_profile(profile)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    next_grade = int(grade) if grade is not None else state.get("grade")
    conn = _connect()
    try:
        if grade is not None:
            conn.execute(
                """
                UPDATE learner_progress
                SET derived_profile = ?, mastery_score = NULL, mastery_updated_at = ?,
                    mastery_source = ?, grade = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (normalized, now, (source or "")[:120], next_grade, now, user_id),
            )
        else:
            conn.execute(
                """
                UPDATE learner_progress
                SET derived_profile = ?, mastery_score = NULL, mastery_updated_at = ?,
                    mastery_source = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (normalized, now, (source or "")[:120], now, user_id),
            )
        conn.commit()
    finally:
        conn.close()
    return {
        **state,
        "mastery_score": None,
        "derived_profile": normalized,
        "mastery_updated_at": now,
        "mastery_source": source,
        "grade": next_grade if next_grade is not None else state.get("grade"),
        "updated_at": now,
        "profile_label": profile_display_label(normalized),
    }


def set_mastery_score(
    user_id: str,
    mastery_score: float,
    *,
    source: str = "learner_profile_analytics",
) -> dict[str, Any]:
    """
    Deprecated: old score-based path. Kept only for backward compatibility.
    Prefer set_learner_profile(profile=...).
    """
    # Map common bands so old clients do not crash; prefer direct profile API.
    try:
        score = float(mastery_score)
    except (TypeError, ValueError):
        score = 50.0
    if score <= 1.0 and score >= 0.0:
        score = score * 100.0
    if score <= 49:
        profile = "weak"
    elif score <= 74:
        profile = "average"
    else:
        profile = "strong"
    return set_learner_profile(user_id, profile, source=source)
