"""
Topic-level (grade-agnostic) AR assets.

Unlike per-lesson AR (`ar_assets.db` keyed by lesson_id), topic packs are keyed
by a stable `topic_key` (e.g. AR_PLANTS) and are designed to be approved once
by the teacher, then reused across multiple lessons/grades.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from app.chroma_setup import BACKEND_ROOT


DB_PATH = BACKEND_ROOT / "data" / "ar_topic_assets.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def init_ar_topic_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ar_topic_assets (
              topic_key TEXT PRIMARY KEY,
              title TEXT,
              payload_json TEXT,
              source_lesson_ids_json TEXT NOT NULL DEFAULT '[]',
              approved INTEGER NOT NULL DEFAULT 0,
              approved_at TEXT,
              generated_at TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ar_topic_approved ON ar_topic_assets(approved)")
        conn.commit()
    finally:
        conn.close()


def _parse_payload(raw: str | None) -> dict[str, Any] | None:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    d = dict(row)
    d["source_lesson_ids"] = json.loads(d.pop("source_lesson_ids_json") or "[]")
    d["payload"] = _parse_payload(d.pop("payload_json", None))
    d["approved"] = bool(d.get("approved"))
    return d


def get_topic_asset(topic_key: str) -> dict[str, Any] | None:
    init_ar_topic_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM ar_topic_assets WHERE topic_key = ?",
            (topic_key,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def list_topic_assets(*, only_approved: bool = False) -> list[dict[str, Any]]:
    init_ar_topic_db()
    conn = _connect()
    try:
        where = "WHERE approved = 1" if only_approved else ""
        rows = conn.execute(f"SELECT * FROM ar_topic_assets {where} ORDER BY topic_key ASC").fetchall()
        return [_row_to_dict(r) for r in rows if r]
    finally:
        conn.close()


def upsert_generated_topic_asset(
    *,
    topic_key: str,
    title: str,
    payload: dict[str, Any],
    source_lesson_ids: list[str],
    approved: bool = False,
) -> dict[str, Any]:
    """
    Save (or replace) the generated payload for a topic.
    Approval status is not removed; `approved` only controls the stored flag.
    """
    init_ar_topic_db()
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO ar_topic_assets (
              topic_key, title, payload_json, source_lesson_ids_json,
              approved, approved_at, generated_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(topic_key) DO UPDATE SET
              title=excluded.title,
              payload_json=excluded.payload_json,
              source_lesson_ids_json=excluded.source_lesson_ids_json,
              approved=excluded.approved,
              approved_at=CASE
                WHEN excluded.approved = 1 THEN COALESCE(ar_topic_assets.approved_at, ?)
                ELSE NULL
              END,
              generated_at=excluded.generated_at,
              updated_at=excluded.updated_at
            """,
            (
                topic_key,
                title,
                json.dumps(payload),
                json.dumps(source_lesson_ids),
                1 if approved else 0,
                now if approved else None,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_topic_asset(topic_key) or {}


def approve_topic_asset(topic_key: str) -> dict[str, Any] | None:
    init_ar_topic_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload_json FROM ar_topic_assets WHERE topic_key = ?",
            (topic_key,),
        ).fetchone()
        if not row:
            return None
        if not (row["payload_json"] or "").strip():
            return None
        now = _now()
        conn.execute(
            """
            UPDATE ar_topic_assets
            SET approved = 1,
                approved_at = COALESCE(approved_at, ?),
                updated_at = ?
            WHERE topic_key = ?
            """,
            (now, now, topic_key),
        )
        conn.commit()
    finally:
        conn.close()
    return get_topic_asset(topic_key)


def clear_topic_asset(topic_key: str) -> bool:
    init_ar_topic_db()
    conn = _connect()
    try:
        cur = conn.execute(
            """
            UPDATE ar_topic_assets
            SET payload_json = NULL,
                approved = 0,
                approved_at = NULL,
                generated_at = NULL,
                updated_at = ?
            WHERE topic_key = ?
            """,
            (_now(), topic_key),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

