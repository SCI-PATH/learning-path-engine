"""
Per-lesson AR assets (keyed by lesson_id only — not by weak/average/smart).

Supports:
- optional model_url (teacher / seed GLB)
- generated payload_json (LLM scenes from library lesson text) — generate once, reuse
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.chroma_setup import BACKEND_ROOT

DB_PATH = BACKEND_ROOT / "data" / "ar_assets.db"
SEED_PATH = BACKEND_ROOT / "app" / "data" / "ar_seed.json"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(ar_assets)").fetchall()
    }
    if "payload_json" not in cols:
        conn.execute("ALTER TABLE ar_assets ADD COLUMN payload_json TEXT")
    if "source_content_id" not in cols:
        conn.execute("ALTER TABLE ar_assets ADD COLUMN source_content_id TEXT")
    if "generated_at" not in cols:
        conn.execute("ALTER TABLE ar_assets ADD COLUMN generated_at TEXT")


def init_ar_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ar_assets (
              lesson_id TEXT PRIMARY KEY,
              grade INTEGER,
              title TEXT,
              model_url TEXT NOT NULL DEFAULT '',
              poster_url TEXT,
              caption TEXT,
              source TEXT,
              updated_at TEXT NOT NULL,
              payload_json TEXT,
              source_content_id TEXT,
              generated_at TEXT
            )
            """
        )
        _ensure_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ar_grade ON ar_assets(grade)"
        )
        conn.commit()
    finally:
        conn.close()
    seed_default_ar_assets()


def _parse_payload(raw: str | None) -> dict[str, Any] | None:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return None
    return data


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    d["payload"] = _parse_payload(d.pop("payload_json", None))
    return d


def seed_default_ar_assets(*, force: bool = False) -> int:
    """
    Insert seed rows for demo chapters if missing.
    Teacher overrides (source=teacher) are never overwritten unless force=True.
    Does not wipe generated payload_json unless force=True.
    """
    if not SEED_PATH.is_file():
        return 0
    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return 0

    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ar_assets (
              lesson_id TEXT PRIMARY KEY,
              grade INTEGER,
              title TEXT,
              model_url TEXT NOT NULL DEFAULT '',
              poster_url TEXT,
              caption TEXT,
              source TEXT,
              updated_at TEXT NOT NULL,
              payload_json TEXT,
              source_content_id TEXT,
              generated_at TEXT
            )
            """
        )
        _ensure_columns(conn)
        conn.commit()
        inserted = 0
        now = _now()
        for item in raw:
            lesson_id = str(item.get("lesson_id") or "").strip()
            if not lesson_id:
                continue
            model_url = str(item.get("model_url") or "").strip()
            existing = conn.execute(
                "SELECT source, payload_json FROM ar_assets WHERE lesson_id = ?",
                (lesson_id,),
            ).fetchone()
            if existing and existing["source"] == "teacher" and not force:
                continue
            # Keep generated AR; only refresh title/caption/model seed fields
            if existing and existing["payload_json"] and not force:
                conn.execute(
                    """
                    UPDATE ar_assets
                    SET grade = COALESCE(?, grade),
                        title = COALESCE(NULLIF(?, ''), title),
                        model_url = CASE
                          WHEN model_url IS NULL OR model_url = '' THEN ?
                          ELSE model_url
                        END,
                        caption = CASE
                          WHEN caption IS NULL OR caption = '' THEN ?
                          ELSE caption
                        END,
                        updated_at = ?
                    WHERE lesson_id = ? AND source = 'seed'
                    """,
                    (
                        item.get("grade"),
                        item.get("title") or "",
                        model_url,
                        item.get("caption") or "",
                        now,
                        lesson_id,
                    ),
                )
                inserted += 1
                continue
            conn.execute(
                """
                INSERT INTO ar_assets (
                  lesson_id, grade, title, model_url, poster_url, caption, source, updated_at
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(lesson_id) DO UPDATE SET
                  grade=excluded.grade,
                  title=excluded.title,
                  model_url=excluded.model_url,
                  poster_url=excluded.poster_url,
                  caption=excluded.caption,
                  source=excluded.source,
                  updated_at=excluded.updated_at
                """,
                (
                    lesson_id,
                    item.get("grade"),
                    item.get("title") or "",
                    model_url,
                    item.get("poster_url") or "",
                    item.get("caption") or "",
                    "seed",
                    now,
                ),
            )
            inserted += 1
        conn.commit()
        return inserted
    finally:
        conn.close()


def get_ar(lesson_id: str) -> dict[str, Any] | None:
    init_ar_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM ar_assets WHERE lesson_id = ?", (lesson_id,)
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_ar(*, grade: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_ar_db()
    conn = _connect()
    try:
        if grade is not None:
            rows = conn.execute(
                """
                SELECT * FROM ar_assets
                WHERE grade = ?
                ORDER BY lesson_id ASC
                LIMIT ?
                """,
                (int(grade), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM ar_assets
                ORDER BY grade ASC, lesson_id ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        conn.close()


def save_generated_ar(
    lesson_id: str,
    *,
    grade: int | None,
    title: str,
    caption: str,
    payload: dict[str, Any],
    source_content_id: str | None,
    model_url: str | None = None,
) -> dict[str, Any]:
    """Upsert generated AR payload; preserve teacher model_url if set."""
    init_ar_db()
    now = _now()
    payload_json = json.dumps(payload, ensure_ascii=False)
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT model_url, source FROM ar_assets WHERE lesson_id = ?",
            (lesson_id,),
        ).fetchone()
        keep_model = ""
        if existing:
            keep_model = (existing["model_url"] or "").strip()
        if model_url is not None:
            keep_model = (model_url or "").strip()
        source = "generated"
        if existing and existing["source"] == "teacher":
            source = "teacher"
        conn.execute(
            """
            INSERT INTO ar_assets (
              lesson_id, grade, title, model_url, poster_url, caption, source,
              updated_at, payload_json, source_content_id, generated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(lesson_id) DO UPDATE SET
              grade=COALESCE(excluded.grade, ar_assets.grade),
              title=excluded.title,
              caption=excluded.caption,
              model_url=CASE
                WHEN excluded.model_url != '' THEN excluded.model_url
                ELSE ar_assets.model_url
              END,
              source=excluded.source,
              updated_at=excluded.updated_at,
              payload_json=excluded.payload_json,
              source_content_id=excluded.source_content_id,
              generated_at=excluded.generated_at
            """,
            (
                lesson_id,
                grade,
                title,
                keep_model,
                "",
                caption,
                source,
                now,
                payload_json,
                source_content_id,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    out = get_ar(lesson_id)
    if not out:
        raise RuntimeError("AR asset vanished after save")
    return out


def upsert_ar(
    lesson_id: str,
    *,
    grade: int | None = None,
    title: str | None = None,
    model_url: str,
    poster_url: str | None = None,
    caption: str | None = None,
    source: str = "teacher",
) -> dict[str, Any]:
    init_ar_db()
    url = (model_url or "").strip()
    if not url:
        raise ValueError("model_url is required")
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO ar_assets (
              lesson_id, grade, title, model_url, poster_url, caption, source, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(lesson_id) DO UPDATE SET
              grade=COALESCE(excluded.grade, ar_assets.grade),
              title=COALESCE(excluded.title, ar_assets.title),
              model_url=excluded.model_url,
              poster_url=excluded.poster_url,
              caption=excluded.caption,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (
                lesson_id,
                grade,
                title or "",
                url,
                poster_url or "",
                caption or "",
                source,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    out = get_ar(lesson_id)
    if not out:
        raise RuntimeError("AR asset vanished after upsert")
    return out


def clear_generated_payload(lesson_id: str) -> bool:
    init_ar_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            UPDATE ar_assets
            SET payload_json = NULL, source_content_id = NULL, generated_at = NULL,
                updated_at = ?
            WHERE lesson_id = ?
            """,
            (_now(), lesson_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_ar(lesson_id: str) -> bool:
    init_ar_db()
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM ar_assets WHERE lesson_id = ?", (lesson_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
