"""
Per-lesson static media: YouTube URL + AI summary infographic (teacher-approved).

Keyed by lesson_id only — same for all knowledge levels. Text slides change;
video and summary stay frozen for the whole chapter.

Backend: Neon Postgres (content_generation.verified_lesson_media) when
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
    sqlite_media_path,
    using_postgres,
    _pg_connect,
)

DB_PATH = sqlite_media_path()
_initialized = False


def _connect_sqlite() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def init_lesson_media_db() -> None:
    global _initialized
    if _initialized:
        return
    if using_postgres():
        ensure_content_schema()
        seed_demo_summaries()
        _initialized = True
        return
    conn = _connect_sqlite()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lesson_media (
              lesson_id TEXT PRIMARY KEY,
              youtube_url TEXT NOT NULL DEFAULT '',
              videos_json TEXT,
              summary_json TEXT,
              summary_image_url TEXT,
              summary_approved INTEGER NOT NULL DEFAULT 0,
              summary_generated_at TEXT,
              summary_approved_at TEXT,
              updated_by TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(lesson_media)").fetchall()
        }
        if "videos_json" not in columns:
            conn.execute("ALTER TABLE lesson_media ADD COLUMN videos_json TEXT")
        conn.commit()
    finally:
        conn.close()
    seed_demo_summaries()
    _initialized = True


def _demo_summaries() -> dict[str, dict[str, Any]]:
    return {
        "g7_sci_01": {
            "title": "Nutrition in Plants",
            "headline": "How green plants make food and why leaves matter.",
            "branches": [
                {
                    "id": "photo",
                    "label": "Photosynthesis",
                    "points": ["Uses sunlight, CO₂, water", "Makes sugar + oxygen"],
                },
                {
                    "id": "leaf",
                    "label": "Leaf role",
                    "points": ["Chlorophyll traps light", "Stomata exchange gases"],
                },
                {
                    "id": "raw",
                    "label": "Raw materials",
                    "points": ["Water from roots", "CO₂ from air"],
                },
                {
                    "id": "mode",
                    "label": "Modes",
                    "points": ["Autotrophs make food", "Heterotrophs depend on plants"],
                },
            ],
        },
        "g7_sci_02": {
            "title": "Nutrition in Animals",
            "headline": "Steps animals use to get energy from food.",
            "branches": [
                {
                    "id": "ingest",
                    "label": "Ingestion",
                    "points": ["Taking food in", "Mouth and teeth"],
                },
                {
                    "id": "digest",
                    "label": "Digestion",
                    "points": ["Break food into simpler form", "Enzymes help"],
                },
                {
                    "id": "absorb",
                    "label": "Absorption",
                    "points": ["Nutrients enter blood", "Small intestine key"],
                },
                {
                    "id": "egest",
                    "label": "Egestion",
                    "points": ["Removes undigested waste"],
                },
            ],
        },
    }


def seed_demo_summaries() -> None:
    """
    Ensure demo chapters have an approved mindmap even if AI image gen failed.
    Does not overwrite an existing teacher summary.
    """
    demos = _demo_summaries()
    now = _now()

    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                for lid, summary in demos.items():
                    cur.execute(
                        f"""
                        SELECT summary_json, summary_approved
                        FROM {SCHEMA}.verified_lesson_media
                        WHERE lesson_id = %s
                        """,
                        (lid,),
                    )
                    row = cur.fetchone()
                    if row and row.get("summary_json"):
                        if not row.get("summary_approved"):
                            cur.execute(
                                f"""
                                UPDATE {SCHEMA}.verified_lesson_media
                                SET summary_approved = TRUE,
                                    summary_approved_at = COALESCE(summary_approved_at, NOW()),
                                    updated_at = NOW()
                                WHERE lesson_id = %s
                                """,
                                (lid,),
                            )
                        continue
                    cur.execute(
                        f"""
                        INSERT INTO {SCHEMA}.verified_lesson_media (
                          lesson_id, grade, chapter_title, youtube_url, videos_json,
                          summary_title, summary_headline, summary_json,
                          summary_approved, summary_generated_at, summary_approved_at,
                          updated_by, updated_at
                        ) VALUES (
                          %s, 7, %s, '', '[]'::jsonb, %s, %s, %s::jsonb,
                          TRUE, NOW(), NOW(), 'seed', NOW()
                        )
                        ON CONFLICT (lesson_id) DO UPDATE SET
                          summary_title = EXCLUDED.summary_title,
                          summary_headline = EXCLUDED.summary_headline,
                          summary_json = EXCLUDED.summary_json,
                          summary_approved = TRUE,
                          summary_generated_at = EXCLUDED.summary_generated_at,
                          summary_approved_at = EXCLUDED.summary_approved_at,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = NOW()
                        WHERE {SCHEMA}.verified_lesson_media.summary_json IS NULL
                        """,
                        (
                            lid,
                            summary.get("title"),
                            summary.get("title"),
                            summary.get("headline"),
                            json.dumps(summary),
                        ),
                    )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return

    conn = _connect_sqlite()
    try:
        for lid, summary in demos.items():
            row = conn.execute(
                "SELECT summary_json, summary_approved FROM lesson_media WHERE lesson_id = ?",
                (lid,),
            ).fetchone()
            if row and (row["summary_json"] or "").strip():
                if not int(row["summary_approved"] or 0):
                    conn.execute(
                        """
                        UPDATE lesson_media
                        SET summary_approved = 1,
                            summary_approved_at = COALESCE(summary_approved_at, ?),
                            updated_at = ?
                        WHERE lesson_id = ?
                        """,
                        (now, now, lid),
                    )
                continue
            conn.execute(
                """
                INSERT INTO lesson_media (
                  lesson_id, youtube_url, summary_json, summary_image_url,
                  summary_approved, summary_generated_at, summary_approved_at,
                  updated_by, updated_at
                ) VALUES (?, '', ?, NULL, 1, ?, ?, 'seed', ?)
                ON CONFLICT(lesson_id) DO UPDATE SET
                  summary_json=excluded.summary_json,
                  summary_approved=1,
                  summary_generated_at=excluded.summary_generated_at,
                  summary_approved_at=excluded.summary_approved_at,
                  updated_by=excluded.updated_by,
                  updated_at=excluded.updated_at
                WHERE (lesson_media.summary_json IS NULL OR lesson_media.summary_json = '')
                """,
                (lid, json.dumps(summary), now, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def _parse_summary(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_videos(raw: Any, legacy_url: str = "") -> list[dict[str, str]]:
    videos: list[dict[str, str]] = []
    data = raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
    if isinstance(data, list):
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            title = str(item.get("title") or f"Video {i + 1}").strip()
            videos.append({"title": title[:120], "url": url})
    legacy = (legacy_url or "").strip()
    if not videos and legacy:
        videos.append({"title": "Chapter video", "url": legacy})
    return videos


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    d = as_dict(row)
    if not d:
        return None
    summary = _parse_summary(d.pop("summary_json", None))
    if summary is None and (d.get("summary_title") or d.get("summary_headline")):
        summary = {
            "title": d.get("summary_title"),
            "headline": d.get("summary_headline"),
        }
    d["summary"] = summary
    d["summary_approved"] = bool(d.get("summary_approved"))
    d["youtube_url"] = (d.get("youtube_url") or "").strip()
    d["videos"] = _parse_videos(d.pop("videos_json", None), d["youtube_url"])
    d["summary_image_url"] = (d.get("summary_image_url") or "").strip() or None
    return d


def get_media(lesson_id: str) -> dict[str, Any] | None:
    init_lesson_media_db()
    lid = (lesson_id or "").strip()
    if not lid:
        return None
    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"SELECT * FROM {SCHEMA}.verified_lesson_media WHERE lesson_id = %s",
                    (lid,),
                )
                return _row_to_dict(cur.fetchone())
        finally:
            conn.close()
    conn = _connect_sqlite()
    try:
        row = conn.execute(
            "SELECT * FROM lesson_media WHERE lesson_id = ?", (lid,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def _resolve_grade_title(lesson_id: str) -> tuple[int, str | None]:
    try:
        from app.topic_ids import resolve_lesson

        le = resolve_lesson(lesson_id)
        if le:
            return int(le.grade or 7), le.title
    except Exception:
        pass
    return 7, None


def upsert_youtube(
    lesson_id: str,
    *,
    youtube_url: str,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    init_lesson_media_db()
    lid = (lesson_id or "").strip()
    if not lid:
        raise ValueError("lesson_id is required")
    now = _now()
    url = (youtube_url or "").strip()
    videos = [{"title": "Chapter video", "url": url}] if url else []
    videos_json = json.dumps(videos)
    grade, title = _resolve_grade_title(lid)

    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.verified_lesson_media (
                      lesson_id, grade, chapter_title, youtube_url, videos_json,
                      updated_by, updated_at
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, NOW())
                    ON CONFLICT (lesson_id) DO UPDATE SET
                      youtube_url = EXCLUDED.youtube_url,
                      videos_json = EXCLUDED.videos_json,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = NOW()
                    """,
                    (lid, grade, title, url, videos_json, teacher_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return get_media(lid) or {}

    conn = _connect_sqlite()
    try:
        conn.execute(
            """
            INSERT INTO lesson_media (
              lesson_id, youtube_url, videos_json, updated_by, updated_at
            ) VALUES (?,?,?,?,?)
            ON CONFLICT(lesson_id) DO UPDATE SET
              youtube_url=excluded.youtube_url,
              videos_json=excluded.videos_json,
              updated_by=excluded.updated_by,
              updated_at=excluded.updated_at
            """,
            (lid, url, videos_json, teacher_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_media(lid) or {}


def upsert_videos(
    lesson_id: str,
    *,
    videos: list[dict[str, str]],
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    init_lesson_media_db()
    lid = (lesson_id or "").strip()
    if not lid:
        raise ValueError("lesson_id is required")
    cleaned: list[dict[str, str]] = []
    for i, item in enumerate(videos[:20]):
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        cleaned.append(
            {
                "title": str(item.get("title") or f"Video {i + 1}").strip()[:120],
                "url": url,
            }
        )
    now = _now()
    legacy_url = cleaned[0]["url"] if cleaned else ""
    videos_json = json.dumps(cleaned)
    grade, title = _resolve_grade_title(lid)

    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.verified_lesson_media (
                      lesson_id, grade, chapter_title, youtube_url, videos_json,
                      updated_by, updated_at
                    ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, NOW())
                    ON CONFLICT (lesson_id) DO UPDATE SET
                      youtube_url = EXCLUDED.youtube_url,
                      videos_json = EXCLUDED.videos_json,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = NOW()
                    """,
                    (lid, grade, title, legacy_url, videos_json, teacher_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return get_media(lid) or {}

    conn = _connect_sqlite()
    try:
        conn.execute(
            """
            INSERT INTO lesson_media (
              lesson_id, youtube_url, videos_json, updated_by, updated_at
            ) VALUES (?,?,?,?,?)
            ON CONFLICT(lesson_id) DO UPDATE SET
              youtube_url=excluded.youtube_url,
              videos_json=excluded.videos_json,
              updated_by=excluded.updated_by,
              updated_at=excluded.updated_at
            """,
            (lid, legacy_url, videos_json, teacher_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_media(lid) or {}


def save_summary_draft(
    lesson_id: str,
    *,
    summary: dict[str, Any],
    summary_image_url: str | None,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    """Save generated (or re-generated) draft — not approved until teacher confirms."""
    init_lesson_media_db()
    lid = (lesson_id or "").strip()
    if not lid:
        raise ValueError("lesson_id is required")
    now = _now()
    image = (summary_image_url or "").strip()
    title = str(summary.get("title") or "").strip() or None
    headline = str(summary.get("headline") or "").strip() or None

    if using_postgres():
        grade, chapter = _resolve_grade_title(lid)
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"SELECT youtube_url FROM {SCHEMA}.verified_lesson_media WHERE lesson_id = %s",
                    (lid,),
                )
                existing = cur.fetchone()
                yt = (existing["youtube_url"] if existing else "") or ""
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.verified_lesson_media (
                      lesson_id, grade, chapter_title, youtube_url, summary_title, summary_headline,
                      summary_json, summary_image_url, summary_approved,
                      summary_generated_at, summary_approved_at, updated_by, updated_at
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s::jsonb, %s, FALSE, NOW(), NULL, %s, NOW()
                    )
                    ON CONFLICT (lesson_id) DO UPDATE SET
                      summary_title = EXCLUDED.summary_title,
                      summary_headline = EXCLUDED.summary_headline,
                      summary_json = EXCLUDED.summary_json,
                      summary_image_url = EXCLUDED.summary_image_url,
                      summary_approved = FALSE,
                      summary_generated_at = NOW(),
                      summary_approved_at = NULL,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = NOW()
                    """,
                    (lid, grade, chapter, yt, title, headline, json.dumps(summary), image, teacher_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return get_media(lid) or {}

    conn = _connect_sqlite()
    try:
        existing = conn.execute(
            "SELECT youtube_url FROM lesson_media WHERE lesson_id = ?", (lid,)
        ).fetchone()
        yt = (existing["youtube_url"] if existing else "") or ""
        conn.execute(
            """
            INSERT INTO lesson_media (
              lesson_id, youtube_url, summary_json, summary_image_url,
              summary_approved, summary_generated_at, summary_approved_at,
              updated_by, updated_at
            ) VALUES (?,?,?,?,0,?,NULL,?,?)
            ON CONFLICT(lesson_id) DO UPDATE SET
              summary_json=excluded.summary_json,
              summary_image_url=excluded.summary_image_url,
              summary_approved=0,
              summary_generated_at=excluded.summary_generated_at,
              summary_approved_at=NULL,
              updated_by=excluded.updated_by,
              updated_at=excluded.updated_at
            """,
            (lid, yt, json.dumps(summary), image, now, teacher_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return get_media(lid) or {}


def approve_summary(lesson_id: str, *, teacher_id: str = "teacher-1") -> dict[str, Any] | None:
    init_lesson_media_db()
    lid = (lesson_id or "").strip()
    row = get_media(lid)
    if not row or not row.get("summary"):
        return None
    now = _now()

    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"""
                    UPDATE {SCHEMA}.verified_lesson_media
                    SET summary_approved = TRUE,
                        summary_approved_at = NOW(),
                        updated_by = %s,
                        updated_at = NOW()
                    WHERE lesson_id = %s
                    """,
                    (teacher_id, lid),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return get_media(lid)

    conn = _connect_sqlite()
    try:
        conn.execute(
            """
            UPDATE lesson_media
            SET summary_approved = 1,
                summary_approved_at = ?,
                updated_by = ?,
                updated_at = ?
            WHERE lesson_id = ?
            """,
            (now, teacher_id, now, lid),
        )
        conn.commit()
    finally:
        conn.close()
    return get_media(lid)


def _image_row(row: Any) -> dict[str, Any]:
    data = as_dict(row) or {}
    if data.get("image_id") is not None:
        data["image_id"] = str(data["image_id"])
    created = data.get("created_at")
    if hasattr(created, "isoformat"):
        data["created_at"] = created.isoformat()
    return data


def _ensure_media_row(lesson_id: str, teacher_id: str = "teacher-1") -> None:
    """Gallery images FK to verified_lesson_media — create a stub row if needed."""
    if get_media(lesson_id):
        return
    grade, title = _resolve_grade_title(lesson_id)
    if using_postgres():
        conn = _pg_connect()
        try:
            with pg_cursor(conn) as cur:
                cur.execute(
                    f"""
                    INSERT INTO {SCHEMA}.verified_lesson_media (
                      lesson_id, grade, chapter_title, youtube_url, videos_json,
                      updated_by, updated_at
                    ) VALUES (%s, %s, %s, '', '[]'::jsonb, %s, NOW())
                    ON CONFLICT (lesson_id) DO NOTHING
                    """,
                    (lesson_id, grade, title, teacher_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return
    now = _now()
    conn = _connect_sqlite()
    try:
        conn.execute(
            """
            INSERT INTO lesson_media (lesson_id, youtube_url, videos_json, updated_by, updated_at)
            VALUES (?, '', '[]', ?, ?)
            ON CONFLICT(lesson_id) DO NOTHING
            """,
            (lesson_id, teacher_id, now),
        )
        conn.commit()
    finally:
        conn.close()


def list_lesson_images(lesson_id: str) -> list[dict[str, Any]]:
    lid = (lesson_id or "").strip()
    if not lid or not using_postgres():
        return []
    conn = _pg_connect()
    try:
        with pg_cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT image_id, lesson_id, image_type, image_url, caption, sort_order, created_at
                FROM {SCHEMA}.verified_lesson_images
                WHERE lesson_id = %s
                ORDER BY sort_order ASC, created_at ASC
                """,
                (lid,),
            )
            return [_image_row(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_lesson_image(
    lesson_id: str,
    *,
    image_url: str,
    caption: str | None = None,
    image_type: str = "gallery",
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    lid = (lesson_id or "").strip()
    url = (image_url or "").strip()
    if not lid:
        raise ValueError("lesson_id is required")
    if not url:
        raise ValueError("image_url is required")
    kind = (image_type or "gallery").strip().lower()
    if kind not in ("gallery", "mindmap", "ar", "infographic", "other"):
        kind = "gallery"
    if not using_postgres():
        raise ValueError("Image gallery requires the Neon content database.")
    _ensure_media_row(lid, teacher_id)
    image_id = str(uuid.uuid4())
    conn = _pg_connect()
    try:
        with pg_cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order
                FROM {SCHEMA}.verified_lesson_images
                WHERE lesson_id = %s
                """,
                (lid,),
            )
            nxt = int((cur.fetchone() or {}).get("next_order") or 0)
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.verified_lesson_images (
                  image_id, lesson_id, image_type, image_url, caption, sort_order
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (image_id, lid, kind, url, (caption or "").strip() or None, nxt),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "image_id": image_id,
        "lesson_id": lid,
        "image_type": kind,
        "image_url": url,
        "caption": (caption or "").strip() or None,
        "sort_order": nxt,
    }


def delete_lesson_image(lesson_id: str, image_id: str) -> dict[str, Any] | None:
    lid = (lesson_id or "").strip()
    iid = (image_id or "").strip()
    if not lid or not iid or not using_postgres():
        return None
    conn = _pg_connect()
    try:
        with pg_cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT image_id, lesson_id, image_type, image_url, caption, sort_order, created_at
                FROM {SCHEMA}.verified_lesson_images
                WHERE lesson_id = %s AND image_id = %s
                """,
                (lid, iid),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                f"""
                DELETE FROM {SCHEMA}.verified_lesson_images
                WHERE lesson_id = %s AND image_id = %s
                """,
                (lid, iid),
            )
        conn.commit()
        return _image_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
