"""
Permanent teacher-approved lesson library.

One published row per (lesson_id, profile, event). Students only read from here;
teachers generate, approve, update, delete, and regenerate.

Backend: Neon Postgres (content_generation.verified_lesson_content) when
CONTENT_DATABASE_URL / DATABASE_URL is set; otherwise local SQLite.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from app.content_db import (
    SCHEMA,
    as_dict,
    ensure_content_schema,
    pg_cursor,
    sqlite_library_path,
    using_postgres,
    _pg_connect,
)

DB_PATH = sqlite_library_path()


def _connect_sqlite() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_library_db() -> None:
    if using_postgres():
        ensure_content_schema()
        return
    conn = _connect_sqlite()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lesson_content (
              content_id TEXT PRIMARY KEY,
              grade INTEGER,
              lesson_id TEXT NOT NULL,
              lesson_title TEXT,
              profile TEXT NOT NULL,
              event TEXT NOT NULL,
              topic_id TEXT,
              lesson_text TEXT NOT NULL,
              minion_state TEXT,
              presentation_mode TEXT,
              chunk_ids_json TEXT NOT NULL DEFAULT '[]',
              created_by TEXT,
              updated_by TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(lesson_id, profile, event)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_library_filters "
            "ON lesson_content(grade, profile, lesson_id, event)"
        )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize_profile(profile: str | None) -> str:
    p = (profile or "average").strip().lower()
    if p == "smart":
        return "strong"
    if p not in ("weak", "average", "strong"):
        return "average"
    return p


def _chunk_ids_from_row(d: dict[str, Any]) -> list[Any]:
    raw = d.pop("chunk_ids", None)
    if raw is None:
        raw = d.pop("chunk_ids_json", None)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
    return []


def _row_to_dict(row: Any) -> dict[str, Any]:
    d = as_dict(row) or {}
    # Postgres columns → API shape expected by main.py / frontend
    if "chapter_title" in d and "lesson_title" not in d:
        d["lesson_title"] = d.get("chapter_title")
    if "student_level" in d and "profile" not in d:
        d["profile"] = d.get("student_level")
    d["chunk_ids"] = _chunk_ids_from_row(d)
    d.pop("chunk_ids_json", None)
    return d


def get_content(content_id: str) -> dict[str, Any] | None:
    init_library_db()
    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"SELECT * FROM {SCHEMA}.verified_lesson_content WHERE content_id = %s",
                    (content_id,),
                )
                row = cur.fetchone()
                return _row_to_dict(row) if row else None
        finally:
            conn.close()
    conn = _connect_sqlite()
    try:
        row = conn.execute(
            "SELECT * FROM lesson_content WHERE content_id = ?", (content_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def find_content(
    *,
    lesson_id: str,
    profile: str,
    event: str = "lesson_start",
) -> dict[str, Any] | None:
    init_library_db()
    profile = _normalize_profile(profile)
    evt = event or "lesson_start"
    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {SCHEMA}.verified_lesson_content
                    WHERE lesson_id = %s AND student_level = %s
                      AND COALESCE(event, 'lesson_start') = %s
                    LIMIT 1
                    """,
                    (lesson_id, profile, evt),
                )
                row = cur.fetchone()
                return _row_to_dict(row) if row else None
        finally:
            conn.close()
    conn = _connect_sqlite()
    try:
        row = conn.execute(
            """
            SELECT * FROM lesson_content
            WHERE lesson_id = ? AND profile = ? AND event = ?
            LIMIT 1
            """,
            (lesson_id, profile, evt),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def find_content_with_fallback(
    *,
    lesson_id: str,
    profile: str,
    event: str = "lesson_start",
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Load library text for lesson at preferred profile, else any published profile.

    Order: preferred → average → weak → strong (teacher often publishes one band first).
    Returns (row, profile_used).
    """
    preferred = _normalize_profile(profile)
    evt = event or "lesson_start"
    order: list[str] = []
    for p in (preferred, "average", "weak", "strong"):
        if p not in order:
            order.append(p)
    for p in order:
        row = find_content(lesson_id=lesson_id, profile=p, event=evt)
        if row and (row.get("lesson_text") or "").strip():
            return row, p
    return None, None


def list_content(
    *,
    grade: int | None = None,
    profile: str | None = None,
    lesson_id: str | None = None,
    event: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    init_library_db()
    if using_postgres():
        clauses: list[str] = []
        params: list[Any] = []
        if grade is not None:
            clauses.append("grade = %s")
            params.append(int(grade))
        if profile:
            clauses.append("student_level = %s")
            params.append(_normalize_profile(profile))
        if lesson_id:
            clauses.append("lesson_id = %s")
            params.append(lesson_id)
        if event:
            clauses.append("event = %s")
            params.append(event)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"""
                    SELECT * FROM {SCHEMA}.verified_lesson_content
                    {where}
                    ORDER BY grade ASC, lesson_id ASC, student_level ASC, event ASC
                    LIMIT %s
                    """,
                    params,
                )
                return [_row_to_dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    conn = _connect_sqlite()
    try:
        clauses = []
        params = []
        if grade is not None:
            clauses.append("grade = ?")
            params.append(int(grade))
        if profile:
            clauses.append("profile = ?")
            params.append(profile)
        if lesson_id:
            clauses.append("lesson_id = ?")
            params.append(lesson_id)
        if event:
            clauses.append("event = ?")
            params.append(event)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = conn.execute(
            f"""
            SELECT * FROM lesson_content
            {where}
            ORDER BY grade ASC, lesson_id ASC, profile ASC, event ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def upsert_content(
    *,
    grade: int | None,
    lesson_id: str,
    lesson_title: str | None,
    profile: str,
    event: str,
    topic_id: str | None,
    lesson_text: str,
    minion_state: str | None,
    presentation_mode: str | None,
    chunk_ids: list[str] | None,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    """Insert or replace the permanent row for (lesson_id, profile, event)."""
    init_library_db()
    event = event or "lesson_start"
    profile = _normalize_profile(profile)
    text = (lesson_text or "").strip()
    if not text:
        raise ValueError("lesson_text is required")
    now = _now()
    existing = find_content(lesson_id=lesson_id, profile=profile, event=event)
    chunks = list(chunk_ids or (existing or {}).get("chunk_ids") or [])

    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                title = (lesson_title or (existing or {}).get("lesson_title") or lesson_id or "Untitled").strip()
                g = int(grade if grade is not None else (existing or {}).get("grade") or 7)
                if existing:
                    content_id = existing["content_id"]
                    cur.execute(
                        f"""
                        UPDATE {SCHEMA}.verified_lesson_content
                        SET grade = %s,
                            chapter_title = %s,
                            topic_id = %s,
                            lesson_text = %s,
                            minion_state = %s,
                            presentation_mode = %s,
                            chunk_ids = %s::jsonb,
                            event = %s,
                            status = 'approved',
                            verified_by = %s,
                            verified_at = NOW(),
                            updated_by = %s,
                            updated_at = NOW()
                        WHERE content_id = %s
                        """,
                        (
                            g,
                            title,
                            topic_id,
                            text,
                            minion_state or existing.get("minion_state") or "idle",
                            presentation_mode or existing.get("presentation_mode"),
                            json.dumps(chunks),
                            event,
                            teacher_id,
                            teacher_id,
                            content_id,
                        ),
                    )
                else:
                    content_id = str(uuid.uuid4())
                    cur.execute(
                        f"""
                        INSERT INTO {SCHEMA}.verified_lesson_content (
                          content_id, grade, lesson_id, chapter_title, topic_id,
                          student_level, event, lesson_text, minion_state,
                          presentation_mode, chunk_ids, status,
                          verified_by, verified_at, created_by, updated_by
                        ) VALUES (
                          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'approved',
                          %s,NOW(),%s,%s
                        )
                        ON CONFLICT (lesson_id, student_level) DO UPDATE SET
                          grade = EXCLUDED.grade,
                          chapter_title = EXCLUDED.chapter_title,
                          topic_id = EXCLUDED.topic_id,
                          event = EXCLUDED.event,
                          lesson_text = EXCLUDED.lesson_text,
                          minion_state = EXCLUDED.minion_state,
                          presentation_mode = EXCLUDED.presentation_mode,
                          chunk_ids = EXCLUDED.chunk_ids,
                          status = 'approved',
                          verified_by = EXCLUDED.verified_by,
                          verified_at = NOW(),
                          updated_by = EXCLUDED.updated_by,
                          updated_at = NOW()
                        RETURNING content_id
                        """,
                        (
                            content_id,
                            g,
                            lesson_id,
                            title,
                            topic_id,
                            profile,
                            event,
                            text,
                            minion_state or "idle",
                            presentation_mode,
                            json.dumps(chunks),
                            teacher_id,
                            teacher_id,
                            teacher_id,
                        ),
                    )
                    returned = cur.fetchone()
                    if returned and returned.get("content_id"):
                        content_id = returned["content_id"]
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        out = get_content(content_id)
        if not out:
            raise RuntimeError("Content vanished after upsert")
        return out

    conn = _connect_sqlite()
    try:
        if existing:
            conn.execute(
                """
                UPDATE lesson_content
                SET grade = ?, lesson_title = ?, topic_id = ?, lesson_text = ?,
                    minion_state = ?, presentation_mode = ?, chunk_ids_json = ?,
                    updated_by = ?, updated_at = ?
                WHERE content_id = ?
                """,
                (
                    grade,
                    lesson_title or existing.get("lesson_title") or "",
                    topic_id,
                    text,
                    minion_state or existing.get("minion_state") or "idle",
                    presentation_mode or existing.get("presentation_mode"),
                    json.dumps(chunks),
                    teacher_id,
                    now,
                    existing["content_id"],
                ),
            )
            content_id = existing["content_id"]
        else:
            content_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO lesson_content (
                  content_id, grade, lesson_id, lesson_title, profile, event,
                  topic_id, lesson_text, minion_state, presentation_mode,
                  chunk_ids_json, created_by, updated_by, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    content_id,
                    grade,
                    lesson_id,
                    lesson_title or "",
                    profile,
                    event,
                    topic_id,
                    text,
                    minion_state or "idle",
                    presentation_mode,
                    json.dumps(chunks),
                    teacher_id,
                    teacher_id,
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    out = get_content(content_id)
    if not out:
        raise RuntimeError("Content vanished after upsert")
    return out


def update_content(
    content_id: str,
    *,
    lesson_text: str | None = None,
    lesson_title: str | None = None,
    minion_state: str | None = None,
    presentation_mode: str | None = None,
    chunk_ids: list[str] | None = None,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    init_library_db()
    existing = get_content(content_id)
    if not existing:
        raise KeyError(f"Unknown content_id: {content_id}")
    text = lesson_text if lesson_text is not None else existing["lesson_text"]
    text = (text or "").strip()
    if not text:
        raise ValueError("lesson_text cannot be empty")
    now = _now()
    title = lesson_title if lesson_title is not None else existing.get("lesson_title")
    chunks = list(chunk_ids if chunk_ids is not None else existing.get("chunk_ids") or [])
    mstate = minion_state if minion_state is not None else existing.get("minion_state")
    pmode = (
        presentation_mode
        if presentation_mode is not None
        else existing.get("presentation_mode")
    )

    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.verified_lesson_content
                    SET lesson_text = %s,
                        chapter_title = %s,
                        minion_state = %s,
                        presentation_mode = %s,
                        chunk_ids = %s::jsonb,
                        updated_by = %s,
                        updated_at = NOW()
                    WHERE content_id = %s
                    """,
                    (text, title, mstate, pmode, json.dumps(chunks), teacher_id, content_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        out = get_content(content_id)
        if not out:
            raise RuntimeError("Content vanished after update")
        return out

    conn = _connect_sqlite()
    try:
        conn.execute(
            """
            UPDATE lesson_content
            SET lesson_text = ?, lesson_title = ?, minion_state = ?,
                presentation_mode = ?, chunk_ids_json = ?,
                updated_by = ?, updated_at = ?
            WHERE content_id = ?
            """,
            (text, title, mstate, pmode, json.dumps(chunks), teacher_id, now, content_id),
        )
        conn.commit()
    finally:
        conn.close()
    out = get_content(content_id)
    if not out:
        raise RuntimeError("Content vanished after update")
    return out


def delete_content(content_id: str) -> bool:
    init_library_db()
    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"DELETE FROM {SCHEMA}.verified_lesson_content WHERE content_id = %s",
                    (content_id,),
                )
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    conn = _connect_sqlite()
    try:
        cur = conn.execute(
            "DELETE FROM lesson_content WHERE content_id = ?", (content_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def migrate_topic_ids() -> dict[str, Any]:
    """
    Rewrite stored topic_id values to Assessment-style primary IDs.
    Prefer lesson_id → curriculum topic_id; fall back to resolving the old topic_id.
    """
    from app.topic_ids import resolve_chroma_topic_id, resolve_lesson

    init_library_db()
    updated = 0
    unchanged = 0
    samples: list[dict[str, str]] = []

    rows = list_content(limit=5000)
    for row in rows:
        cid = row["content_id"]
        lid = row.get("lesson_id") or ""
        old = (row.get("topic_id") or "").strip()
        le = resolve_lesson(lid) if lid else None
        new = le.topic_id if le else (resolve_chroma_topic_id(old) if old else old)
        if not new or new == old:
            unchanged += 1
            continue
        if using_postgres():
            conn = _pg_connect()
            try:
                with pg_cursor(conn) as cur:
                    cur.execute(
                        f"""
                        UPDATE {SCHEMA}.verified_lesson_content
                        SET topic_id = %s, updated_at = NOW()
                        WHERE content_id = %s
                        """,
                        (new, cid),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        else:
            conn = _connect_sqlite()
            try:
                conn.execute(
                    "UPDATE lesson_content SET topic_id = ? WHERE content_id = ?",
                    (new, cid),
                )
                conn.commit()
            finally:
                conn.close()
        updated += 1
        if len(samples) < 12:
            samples.append(
                {"content_id": cid, "lesson_id": lid, "from": old, "to": new}
            )
    return {"updated": updated, "unchanged": unchanged, "samples": samples}
