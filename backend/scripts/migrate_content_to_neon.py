"""
One-shot: create Neon content_generation tables and copy local SQLite
verified lessons + media into them.

Usage (from learning-path-engine/backend):
  $env:PYTHONPATH = "."
  .\\.venv\\Scripts\\python.exe scripts\\migrate_content_to_neon.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.content_db import (  # noqa: E402
    SCHEMA,
    content_database_url,
    ensure_content_schema,
    pg_cursor,
    redact_url,
    sqlite_library_path,
    sqlite_media_path,
    using_postgres,
    _pg_connect,
)


def _sqlite_rows(db_path: Path, sql: str) -> list[sqlite3.Row]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


def migrate_content() -> dict:
    rows = _sqlite_rows(
        sqlite_library_path(),
        "SELECT * FROM lesson_content ORDER BY grade, lesson_id, profile",
    )
    conn = _pg_connect()
    inserted = 0
    updated = 0
    try:
        with pg_cursor(conn) as cur:
            for r in rows:
                profile = (r["profile"] or "average").strip().lower()
                if profile == "smart":
                    profile = "strong"
                if profile not in ("weak", "average", "strong"):
                    profile = "average"
                chunks = r["chunk_ids_json"] or "[]"
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.verified_lesson_content (
                      content_id, grade, lesson_id, chapter_title, topic_id,
                      student_level, event, lesson_text, minion_state,
                      presentation_mode, chunk_ids, status,
                      verified_by, verified_at, created_by, updated_by,
                      created_at, updated_at
                    ) VALUES (
                      %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'approved',
                      %s,NOW(),%s,%s,
                      COALESCE(NULLIF(%s,'')::timestamptz, NOW()),
                      COALESCE(NULLIF(%s,'')::timestamptz, NOW())
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
                    """,
                    (
                        r["content_id"],
                        int(r["grade"] or 7),
                        r["lesson_id"],
                        (r["lesson_title"] or r["lesson_id"] or "Untitled"),
                        r["topic_id"],
                        profile,
                        r["event"] or "lesson_start",
                        r["lesson_text"],
                        r["minion_state"],
                        r["presentation_mode"],
                        chunks if isinstance(chunks, str) else json.dumps(chunks),
                        r["updated_by"] or r["created_by"],
                        r["created_by"],
                        r["updated_by"] or r["created_by"],
                        r["created_at"] or "",
                        r["updated_at"] or "",
                    ),
                )
                if cur.rowcount == 1:
                    # INSERT or UPDATE both report 1 in psycopg2 usually
                    inserted += 1
                else:
                    updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"source_rows": len(rows), "upserted": inserted + updated}


def migrate_media() -> dict:
    rows = _sqlite_rows(sqlite_media_path(), "SELECT * FROM lesson_media")
    # title/grade hints from content library
    content_rows = _sqlite_rows(
        sqlite_library_path(),
        "SELECT lesson_id, grade, lesson_title FROM lesson_content",
    )
    meta = {r["lesson_id"]: r for r in content_rows}

    conn = _pg_connect()
    upserted = 0
    try:
        with pg_cursor(conn) as cur:
            for r in rows:
                lid = r["lesson_id"]
                m = meta.get(lid)
                videos = r["videos_json"] or "[]"
                if isinstance(videos, str) and not videos.strip():
                    videos = "[]"
                summary = r["summary_json"]
                summary_obj = None
                if summary:
                    try:
                        summary_obj = json.loads(summary)
                    except json.JSONDecodeError:
                        summary_obj = None
                title = (summary_obj or {}).get("title") if isinstance(summary_obj, dict) else None
                headline = (
                    (summary_obj or {}).get("headline") if isinstance(summary_obj, dict) else None
                )
                if not title and m:
                    title = m["lesson_title"]
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.verified_lesson_media (
                      lesson_id, grade, chapter_title, youtube_url, videos_json,
                      summary_title, summary_headline, summary_json, summary_image_url,
                      summary_approved, summary_generated_at, summary_approved_at,
                      updated_by, updated_at
                    ) VALUES (
                      %s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,
                      %s,
                      NULLIF(%s,'')::timestamptz,
                      NULLIF(%s,'')::timestamptz,
                      %s, COALESCE(NULLIF(%s,'')::timestamptz, NOW())
                    )
                    ON CONFLICT (lesson_id) DO UPDATE SET
                      grade = COALESCE(EXCLUDED.grade, {SCHEMA}.verified_lesson_media.grade),
                      chapter_title = COALESCE(EXCLUDED.chapter_title, {SCHEMA}.verified_lesson_media.chapter_title),
                      youtube_url = EXCLUDED.youtube_url,
                      videos_json = EXCLUDED.videos_json,
                      summary_title = EXCLUDED.summary_title,
                      summary_headline = EXCLUDED.summary_headline,
                      summary_json = EXCLUDED.summary_json,
                      summary_image_url = EXCLUDED.summary_image_url,
                      summary_approved = EXCLUDED.summary_approved,
                      summary_generated_at = EXCLUDED.summary_generated_at,
                      summary_approved_at = EXCLUDED.summary_approved_at,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = NOW()
                    """,
                    (
                        lid,
                        int(m["grade"]) if m and m["grade"] is not None else 7,
                        (m["lesson_title"] if m else None) or title or lid,
                        (r["youtube_url"] or "").strip(),
                        videos if isinstance(videos, str) else json.dumps(videos),
                        title,
                        headline,
                        summary if summary else None,
                        (r["summary_image_url"] or "").strip() or None,
                        bool(int(r["summary_approved"] or 0)),
                        r["summary_generated_at"] or "",
                        r["summary_approved_at"] or "",
                        r["updated_by"] or "migrate",
                        r["updated_at"] or "",
                    ),
                )
                upserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"source_rows": len(rows), "upserted": upserted}


def main() -> None:
    if not using_postgres():
        print("ERROR: Set CONTENT_DATABASE_URL or DATABASE_URL to your Neon Postgres URL.")
        sys.exit(1)
    print("Target:", redact_url(content_database_url()))
    ensure_content_schema()
    print("Schema ready:", SCHEMA)
    c = migrate_content()
    print("Content:", c)
    m = migrate_media()
    print("Media:", m)

    conn = _pg_connect()
    try:
        with pg_cursor(conn) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {SCHEMA}.verified_lesson_content")
            print("Neon content rows:", cur.fetchone()["n"])
            cur.execute(f"SELECT COUNT(*) AS n FROM {SCHEMA}.verified_lesson_media")
            print("Neon media rows:", cur.fetchone()["n"])
            cur.execute(
                f"""
                SELECT lesson_id, student_level, grade, left(chapter_title,40) AS title
                FROM {SCHEMA}.verified_lesson_content
                ORDER BY grade, lesson_id, student_level
                """
            )
            for row in cur.fetchall():
                print(" ", dict(row))
    finally:
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
