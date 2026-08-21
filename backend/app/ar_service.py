"""
Generate simple lesson-matched AR scenes from published library content.

Flow: if AR payload already saved (with cartoon images) → return it.
Else read lesson text from content library → LLM scenes → generate cartoon images → save → return.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.ar_images import attach_images_to_payload, payload_missing_images
from app.ar_store import get_ar, save_generated_ar
from app.content_library import find_content, list_content
from app.curriculum import load_curriculum
from app.lesson_service import _llm_client_and_model

log = logging.getLogger("learning_path.ar")

ALLOWED_VISUALS = frozenset(
    {
        "monocot",
        "dicot",
        "plant",
        "leaf",
        "flower",
        "root",
        "magnet",
        "animal",
        "cell",
        "water",
        "earth",
        "machine",
        "electric",
        "space",
        "generic",
    }
)

AR_SYSTEM = """You create simple AR study cards from a science lesson.
Return ONLY valid JSON (no markdown fences) with this shape:
{
  "title": "short AR title",
  "summary": "one or two sentences summarizing what students see in AR",
  "scenes": [
    {
      "id": "snake_case_id",
      "label": "Monocot plant",
      "emoji": "🌱",
      "visual": "monocot",
      "image_prompt": "cute cartoon monocot corn plant with long leaves showing parallel veins and fibrous roots",
      "facts": ["short fact", "short fact", "short fact"]
    }
  ]
}

Rules:
- Ground every fact ONLY in the lesson text. Do not invent unrelated science.
- 2 to 4 scenes. Prefer the most important contrasting ideas (e.g. monocot vs dicot).
- Each fact under 14 words, clear for students.
- "visual" must be one of: monocot, dicot, plant, leaf, flower, root, magnet, animal, cell, water, earth, machine, electric, space, generic
- If the lesson discusses monocots and dicots, include separate scenes for each with visual monocot / dicot.
- If magnets, use visual magnet and cover poles / force.
- Labels should name the concept (not "Scene 1").
- "image_prompt" must describe a CLEAR educational CARTOON / caricature illustration for that scene
  (not a photo). Include the key science detail from the lesson (e.g. parallel veins, N and S poles).
  Keep it under 30 words. No text or labels inside the image description.
"""


def _pick_library_row(lesson_id: str) -> dict[str, Any] | None:
    """Prefer average → weak → smart published lesson_start content."""
    for profile in ("intermediate", "basic", "advanced"):
        row = find_content(lesson_id=lesson_id, profile=profile, event="lesson_start")
        if row and (row.get("lesson_text") or "").strip():
            return row
    rows = list_content(lesson_id=lesson_id, limit=20)
    for row in rows:
        if (row.get("lesson_text") or "").strip():
            return row
    return None


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError("Model did not return JSON for AR")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("AR JSON root must be an object")
    return data


def _normalize_payload(data: dict[str, Any], *, fallback_title: str) -> dict[str, Any]:
    scenes_in = data.get("scenes")
    if not isinstance(scenes_in, list):
        scenes_in = []
    scenes: list[dict[str, Any]] = []
    for i, s in enumerate(scenes_in[:4]):
        if not isinstance(s, dict):
            continue
        label = str(s.get("label") or s.get("title") or f"Idea {i + 1}").strip()
        visual = str(s.get("visual") or "generic").strip().lower()
        if visual not in ALLOWED_VISUALS:
            visual = "generic"
        facts_raw = s.get("facts") or s.get("bullets") or []
        facts: list[str] = []
        if isinstance(facts_raw, list):
            for f in facts_raw:
                t = str(f).strip()
                if t:
                    facts.append(t[:120])
                if len(facts) >= 4:
                    break
        if not facts:
            continue
        sid = str(s.get("id") or label.lower().replace(" ", "_"))[:40]
        image_prompt = str(
            s.get("image_prompt") or s.get("photo_query") or s.get("image_query") or ""
        ).strip()[:200]
        scenes.append(
            {
                "id": sid,
                "label": label[:60],
                "emoji": str(s.get("emoji") or "🔬")[:4],
                "visual": visual,
                "image_prompt": image_prompt,
                "facts": facts,
            }
        )
    if not scenes:
        raise ValueError("AR payload had no usable scenes")
    return {
        "title": str(data.get("title") or fallback_title).strip()[:80],
        "summary": str(data.get("summary") or "").strip()[:400],
        "scenes": scenes,
    }


def generate_ar_payload_from_text(
    *,
    lesson_title: str,
    lesson_text: str,
) -> dict[str, Any]:
    client, model = _llm_client_and_model()
    text = (lesson_text or "").strip()
    if len(text) > 6000:
        text = text[:6000] + "\n…"

    user = (
        f"Lesson title: {lesson_title}\n\n"
        f"Lesson text:\n{text}\n\n"
        "Build AR scenes for this lesson. Include a specific cartoon image_prompt for each scene."
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.3,
        max_tokens=1100,
        messages=[
            {"role": "system", "content": AR_SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    choice = resp.choices[0].message.content
    if not choice:
        raise RuntimeError("Empty AR completion from model")
    data = _extract_json(choice)
    return _normalize_payload(data, fallback_title=lesson_title)


def _public_ok(
    *,
    lesson_id: str,
    le: Any,
    row: dict[str, Any],
    cached: bool,
) -> dict[str, Any]:
    return {
        "lesson_id": lesson_id,
        "available": True,
        "cached": cached,
        "grade": row.get("grade") or le.grade,
        "title": row.get("title") or le.title,
        "model_url": (row.get("model_url") or "").strip() or None,
        "caption": row.get("caption") or "",
        "payload": row.get("payload"),
        "source": row.get("source"),
        "generated_at": row.get("generated_at"),
        "source_content_id": row.get("source_content_id"),
        "message": None,
    }


def ensure_ar_package(
    lesson_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Teacher-side: build and store the lesson AR package once.

    If a complete payload (with images) already exists and force is False, return it.
    Otherwise generate from published library text and persist.
    """
    return get_or_generate_ar(lesson_id, force=force)


def get_or_generate_ar(
    lesson_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Return AR for lesson_id. Uses saved payload if present (with cartoon images).
    Otherwise reads content library → LLM scenes → generate cartoons → save → return.
    """
    cur = load_curriculum()
    le = cur.by_lesson_id(lesson_id)
    if not le:
        raise KeyError(f"Unknown lesson_id: {lesson_id}")

    existing = get_ar(lesson_id)
    if existing and existing.get("payload") and not force:
        payload = existing["payload"]
        if payload_missing_images(payload):
            log.info("AR missing cartoon images — generating lesson=%s", lesson_id)
            payload = attach_images_to_payload(
                lesson_id,
                payload,
                force=True,
                lesson_title=existing.get("title") or le.title,
            )
            existing = save_generated_ar(
                lesson_id,
                grade=existing.get("grade") or le.grade,
                title=existing.get("title") or le.title,
                caption=existing.get("caption") or payload.get("summary") or "",
                payload=payload,
                source_content_id=existing.get("source_content_id"),
            )
            return _public_ok(lesson_id=lesson_id, le=le, row=existing, cached=True)
        return _public_ok(lesson_id=lesson_id, le=le, row=existing, cached=True)

    lib = _pick_library_row(lesson_id)
    if not lib:
        return {
            "lesson_id": lesson_id,
            "available": False,
            "cached": False,
            "grade": le.grade,
            "title": le.title,
            "model_url": (existing or {}).get("model_url") if existing else None,
            "caption": (existing or {}).get("caption") if existing else None,
            "payload": None,
            "source": None,
            "message": (
                "No published lesson text yet. Ask your teacher to generate and "
                "save this chapter first — AR is built from that content."
            ),
        }

    title = lib.get("lesson_title") or le.title
    payload = generate_ar_payload_from_text(
        lesson_title=title,
        lesson_text=lib["lesson_text"],
    )
    payload["lesson_id"] = lesson_id
    payload["topic_id"] = le.topic_id
    payload = attach_images_to_payload(
        lesson_id, payload, force=True, lesson_title=title
    )
    payload["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    caption = payload.get("summary") or f"AR scenes for {title}"
    saved = save_generated_ar(
        lesson_id,
        grade=lib.get("grade") or le.grade,
        title=payload.get("title") or title,
        caption=caption,
        payload=payload,
        source_content_id=lib.get("content_id"),
    )
    log.info(
        "AR generated lesson=%s scenes=%s cartoons=%s from content=%s",
        lesson_id,
        len(payload["scenes"]),
        sum(1 for s in payload["scenes"] if s.get("image_url")),
        lib.get("content_id"),
    )
    return _public_ok(lesson_id=lesson_id, le=le, row=saved, cached=False)
