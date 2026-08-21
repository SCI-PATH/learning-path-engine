"""
Human-in-the-loop lesson review store (teacher verification before student UI).

Drafts are keyed by grade + lesson_id + profile + event so weak/average/strong
each get their own reviewed content for the same chapter.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from app.chroma_setup import BACKEND_ROOT

DB_PATH = BACKEND_ROOT / "data" / "lesson_reviews.db"

ReviewStatus = Literal["pending", "approved", "rejected"]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_review_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lesson_drafts (
              draft_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              grade INTEGER,
              lesson_id TEXT NOT NULL,
              lesson_title TEXT,
              profile TEXT NOT NULL,
              event TEXT NOT NULL,
              topic_id TEXT,
              lesson_text TEXT NOT NULL,
              edited_text TEXT,
              minion_state TEXT,
              presentation_mode TEXT,
              chunk_ids_json TEXT NOT NULL DEFAULT '[]',
              status TEXT NOT NULL,
              teacher_note TEXT,
              reviewed_by TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_drafts_status ON lesson_drafts(status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_drafts_lookup ON lesson_drafts(lesson_id, profile, event, status)"
        )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["chunk_ids"] = json.loads(d.pop("chunk_ids_json") or "[]")
    # Effective text for student: teacher edit wins when approved
    edited = d.get("edited_text")
    d["effective_text"] = (edited if edited and str(edited).strip() else d.get("lesson_text")) or ""
    return d


def create_pending_draft(
    *,
    user_id: str,
    grade: int | None,
    lesson_id: str,
    lesson_title: str | None,
    profile: str,
    event: str,
    topic_id: str | None,
    lesson_text: str,
    minion_state: str,
    presentation_mode: str | None,
    chunk_ids: list[str],
) -> dict[str, Any]:
    init_review_db()
    draft_id = str(uuid.uuid4())
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO lesson_drafts (
              draft_id, user_id, grade, lesson_id, lesson_title, profile, event,
              topic_id, lesson_text, edited_text, minion_state, presentation_mode,
              chunk_ids_json, status, teacher_note, reviewed_by, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                draft_id,
                user_id,
                grade,
                lesson_id,
                lesson_title or "",
                profile,
                event or "lesson_start",
                topic_id,
                lesson_text,
                None,
                minion_state,
                presentation_mode,
                json.dumps(list(chunk_ids)),
                "pending",
                None,
                None,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_draft(draft_id)  # type: ignore[return-value]


def get_draft(draft_id: str) -> dict[str, Any] | None:
    init_review_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM lesson_drafts WHERE draft_id = ?", (draft_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def find_approved_reuse(
    *,
    lesson_id: str,
    profile: str,
    event: str,
) -> dict[str, Any] | None:
    """Reuse a previously teacher-approved draft for same chapter + capacity + event."""
    init_review_db()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT * FROM lesson_drafts
            WHERE lesson_id = ? AND profile = ? AND event = ? AND status = 'approved'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (lesson_id, profile, event or "lesson_start"),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_drafts(
    *,
    status: ReviewStatus | None = "pending",
    grade: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_review_db()
    conn = _connect()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if grade is not None:
            clauses.append("grade = ?")
            params.append(int(grade))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = conn.execute(
            f"""
            SELECT * FROM lesson_drafts
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def review_draft(
    draft_id: str,
    *,
    action: Literal["approve", "reject"],
    teacher_id: str = "teacher-1",
    edited_text: str | None = None,
    teacher_note: str | None = None,
) -> dict[str, Any]:
    init_review_db()
    draft = get_draft(draft_id)
    if not draft:
        raise KeyError(f"Unknown draft_id: {draft_id}")
    if draft["status"] != "pending":
        raise ValueError(f"Draft is already {draft['status']}")

    status: ReviewStatus = "approved" if action == "approve" else "rejected"
    now = _now()
    text_edit = edited_text.strip() if edited_text and edited_text.strip() else None

    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE lesson_drafts
            SET status = ?, edited_text = COALESCE(?, edited_text),
                teacher_note = ?, reviewed_by = ?, updated_at = ?
            WHERE draft_id = ?
            """,
            (status, text_edit, teacher_note, teacher_id, now, draft_id),
        )
        conn.commit()
    finally:
        conn.close()

    out = get_draft(draft_id)
    if not out:
        raise RuntimeError("Draft vanished after review")
    return out
