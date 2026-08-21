"""
Neon / Postgres connection for teacher-verified lesson content + media.

Uses CONTENT_DATABASE_URL or DATABASE_URL (postgresql…). Falls back to local
SQLite only when no Postgres URL is configured.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from dotenv import load_dotenv

from app.chroma_setup import BACKEND_ROOT

load_dotenv(BACKEND_ROOT / ".env")

SCHEMA = "content_generation"


@lru_cache(maxsize=1)
def content_database_url() -> str | None:
    raw = (
        os.getenv("CONTENT_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    ).strip()
    if not raw:
        return None
    if raw.startswith("postgresql+psycopg2://"):
        raw = "postgresql://" + raw[len("postgresql+psycopg2://") :]
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    if not (raw.startswith("postgresql://") or raw.startswith("postgres://")):
        return None
    return raw


def using_postgres() -> bool:
    return content_database_url() is not None


def _pg_connect():
    import psycopg2

    url = content_database_url()
    if not url:
        raise RuntimeError("CONTENT_DATABASE_URL / DATABASE_URL is not set for Postgres")
    conn = psycopg2.connect(url)
    conn.autocommit = False
    return conn


def pg_cursor(conn):
    import psycopg2.extras

    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def ensure_content_schema() -> None:
    """
    Ensure verified-content tables/columns exist in content_generation.

    Team Neon users often already have the schema + base tables from DBeaver DDL.
    We only CREATE SCHEMA when allowed, then CREATE TABLE IF NOT EXISTS and
    ADD COLUMN IF NOT EXISTS for app fields.
    """
    if not using_postgres():
        return
    conn = _pg_connect()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
                conn.commit()
            except Exception:
                conn.rollback()
                cur.execute(
                    """
                    SELECT 1 FROM information_schema.schemata
                    WHERE schema_name = %s
                    """,
                    (SCHEMA,),
                )
                if not cur.fetchone():
                    raise RuntimeError(
                        f"Schema {SCHEMA} missing and CREATE SCHEMA is not allowed"
                    )

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.verified_lesson_content (
                  content_id          TEXT PRIMARY KEY,
                  grade               SMALLINT NOT NULL,
                  lesson_id           TEXT NOT NULL,
                  chapter_title       TEXT NOT NULL,
                  topic_id            TEXT,
                  student_level       TEXT NOT NULL
                    CHECK (student_level IN ('weak', 'average', 'strong')),
                  lesson_text         TEXT NOT NULL,
                  presentation_mode   TEXT,
                  status              TEXT NOT NULL DEFAULT 'approved'
                    CHECK (status IN ('draft', 'approved', 'archived')),
                  verified_by         TEXT,
                  verified_at         TIMESTAMPTZ,
                  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE (lesson_id, student_level)
                )
                """
            )
            for ddl in (
                f"ALTER TABLE {SCHEMA}.verified_lesson_content ADD COLUMN IF NOT EXISTS event TEXT NOT NULL DEFAULT 'lesson_start'",
                f"ALTER TABLE {SCHEMA}.verified_lesson_content ADD COLUMN IF NOT EXISTS minion_state TEXT",
                f"ALTER TABLE {SCHEMA}.verified_lesson_content ADD COLUMN IF NOT EXISTS chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
                f"ALTER TABLE {SCHEMA}.verified_lesson_content ADD COLUMN IF NOT EXISTS created_by TEXT",
                f"ALTER TABLE {SCHEMA}.verified_lesson_content ADD COLUMN IF NOT EXISTS updated_by TEXT",
            ):
                cur.execute(ddl)

            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_verified_content_grade_level
                  ON {SCHEMA}.verified_lesson_content
                  (grade, student_level, lesson_id)
                """
            )

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.verified_lesson_media (
                  lesson_id             TEXT PRIMARY KEY,
                  grade                 SMALLINT NOT NULL,
                  chapter_title         TEXT,
                  youtube_url           TEXT,
                  videos_json           JSONB NOT NULL DEFAULT '[]'::jsonb,
                  summary_title         TEXT,
                  summary_headline      TEXT,
                  summary_json          JSONB,
                  summary_image_url     TEXT,
                  summary_approved      BOOLEAN NOT NULL DEFAULT FALSE,
                  summary_approved_at   TIMESTAMPTZ,
                  updated_by            TEXT,
                  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                f"ALTER TABLE {SCHEMA}.verified_lesson_media "
                f"ADD COLUMN IF NOT EXISTS summary_generated_at TIMESTAMPTZ"
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_verified_media_grade
                  ON {SCHEMA}.verified_lesson_media (grade)
                """
            )

            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.verified_lesson_images (
                  image_id        TEXT PRIMARY KEY,
                  lesson_id       TEXT NOT NULL
                    REFERENCES {SCHEMA}.verified_lesson_media(lesson_id)
                    ON DELETE CASCADE,
                  image_type      TEXT NOT NULL
                    CHECK (image_type IN ('gallery', 'mindmap', 'ar', 'infographic', 'other')),
                  image_url       TEXT NOT NULL,
                  caption         TEXT,
                  sort_order      INT NOT NULL DEFAULT 0,
                  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_verified_images_lesson
                  ON {SCHEMA}.verified_lesson_images (lesson_id, image_type, sort_order)
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def redact_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
        netloc = p.hostname or ""
        if p.port:
            netloc = f"{netloc}:{p.port}"
        if p.username:
            netloc = f"{p.username}:***@{netloc}"
        return urlunparse((p.scheme, netloc, p.path, "", p.query, ""))
    except Exception:
        return "(invalid-url)"


def sqlite_library_path() -> Path:
    return BACKEND_ROOT / "data" / "content_library.db"


def sqlite_media_path() -> Path:
    return BACKEND_ROOT / "data" / "lesson_media.db"


def as_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    return dict(row)
