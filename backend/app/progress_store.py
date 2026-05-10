"""SQLite persistence for per-learner lesson path state."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Literal

from app.chroma_setup import BACKEND_ROOT
from app.curriculum import Curriculum, load_curriculum

DB_PATH = BACKEND_ROOT / "data" / "progress.db"

ProgressAction = Literal["set_current", "mark_complete", "record_quiz", "save_state"]


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
        conn.commit()
    finally:
        conn.close()


def _default_state(cur: Curriculum) -> dict[str, Any]:
    first = cur.lessons[0].lesson_id if cur.lessons else ""
    return {
        "current_lesson_id": first,
        "completed_lesson_ids": [],
        "quiz_by_lesson": {},
        "session_state": {"lesson_id": first, "step_index": 0},
        "updated_at": "",
    }


def get_progress(user_id: str, cur: Curriculum | None = None) -> dict[str, Any]:
    init_db()
    c = cur or load_curriculum()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT current_lesson_id, completed_json, quiz_json, session_json, updated_at FROM learner_progress WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            st = _default_state(c)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "INSERT INTO learner_progress (user_id, current_lesson_id, completed_json, quiz_json, session_json, updated_at) VALUES (?,?,?,?,?,?)",
                (
                    user_id,
                    st["current_lesson_id"],
                    json.dumps(st["completed_lesson_ids"]),
                    json.dumps(st["quiz_by_lesson"]),
                    json.dumps(st["session_state"]),
                    now,
                ),
            )
            conn.commit()
            return {**st, "updated_at": now, "user_id": user_id}
        completed = json.loads(row["completed_json"] or "[]")
        quiz = json.loads(row["quiz_json"] or "{}")
        session = json.loads(row["session_json"] or "{}")
        return {
            "user_id": user_id,
            "current_lesson_id": row["current_lesson_id"],
            "completed_lesson_ids": completed,
            "quiz_by_lesson": quiz,
            "session_state": session,
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def update_progress(
    user_id: str,
    *,
    action: ProgressAction,
    lesson_id: str,
    score: float | None = None,
    step_index: int | None = None,
    pass_threshold: float = 0.65,
    cur: Curriculum | None = None,
) -> dict[str, Any]:
    """Apply an action and return fresh state. For record_quiz, may auto-advance when score >= pass_threshold."""
    init_db()
    c = cur or load_curriculum()
    state = get_progress(user_id, c)
    completed = list(state["completed_lesson_ids"])
    quiz = dict(state["quiz_by_lesson"])
    session = dict(state.get("session_state") or {})
    current = state["current_lesson_id"]
    lid = lesson_id.strip()

    if action == "set_current":
        current = lid
        session = {"lesson_id": lid, "step_index": 0}
    elif action == "mark_complete":
        if lid not in completed:
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
                session = {"lesson_id": nxt, "step_index": 0}
    elif action == "save_state":
        if step_index is not None and step_index >= 0:
            session = {"lesson_id": lid, "step_index": int(step_index)}

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE learner_progress
            SET current_lesson_id = ?, completed_json = ?, quiz_json = ?, session_json = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (current, json.dumps(completed), json.dumps(quiz), json.dumps(session), now, user_id),
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
        "updated_at": now,
    }
