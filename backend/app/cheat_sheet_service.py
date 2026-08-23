"""
Student cheat sheet / short notes — generated once per lesson from Chroma textbook chunks.
Static for the whole chapter (does not change as the student steps through slides).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.content_filters import strip_llm_reasoning
from app.curriculum import load_curriculum
from app.lesson_media_store import get_cheatsheet, save_cheatsheet
from app.lesson_service import _chat_completion_text, retrieve_chunks
from app.prompts import build_retrieval_query

log = logging.getLogger("learning_path.cheatsheet")

CHEAT_SHEET_SYSTEM = """You create a short student cheat sheet / revision notes for a science chapter.
Return ONLY valid JSON (no markdown fences) with this shape:
{
  "title": "short chapter title",
  "headline": "one sentence overview for quick recall",
  "sections": [
    {
      "heading": "Topic name (2-5 words)",
      "bullets": ["short fact", "short fact", "short fact"]
    }
  ],
  "terms": [
    {"term": "Scientific term exactly as in sources", "definition": "plain 8-14 word definition"}
  ]
}

Rules:
- Ground EVERY fact ONLY in the textbook excerpts below. Do not invent content.
- 4 to 6 sections. 2 to 4 bullets each. Bullets under 16 words.
- 4 to 8 key terms with definitions.
- Use real names from the sources (plants, organisms, chemicals) — never vague placeholders.
- No activities, exercises, or quiz questions.
- No thinking process, planning steps, or meta commentary — JSON only.
"""


def _extract_json(text: str) -> dict[str, Any]:
    raw = strip_llm_reasoning((text or "").strip())
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
        raise ValueError("Model did not return JSON for cheat sheet")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("Cheat sheet JSON root must be an object")
    return data


def _normalize_cheatsheet(data: dict[str, Any], *, fallback_title: str) -> dict[str, Any]:
    sections_in = data.get("sections") or data.get("branches") or []
    if not isinstance(sections_in, list):
        sections_in = []
    sections: list[dict[str, Any]] = []
    for i, sec in enumerate(sections_in[:6]):
        if not isinstance(sec, dict):
            continue
        heading = str(sec.get("heading") or sec.get("label") or sec.get("title") or f"Topic {i + 1}").strip()[
            :60
        ]
        bullets_raw = sec.get("bullets") or sec.get("points") or []
        bullets: list[str] = []
        if isinstance(bullets_raw, list):
            for b in bullets_raw[:4]:
                t = str(b).strip()
                if t:
                    bullets.append(t[:120])
        if heading and bullets:
            sections.append({"heading": heading, "bullets": bullets})

    terms_in = data.get("terms") or []
    terms: list[dict[str, str]] = []
    if isinstance(terms_in, list):
        for item in terms_in[:8]:
            if not isinstance(item, dict):
                continue
            term = str(item.get("term") or "").strip()[:80]
            definition = str(item.get("definition") or item.get("meaning") or "").strip()[:140]
            if term and definition:
                terms.append({"term": term, "definition": definition})

    if not sections:
        raise ValueError("Cheat sheet had no usable sections")

    return {
        "title": str(data.get("title") or fallback_title).strip()[:80],
        "headline": str(data.get("headline") or data.get("summary") or "").strip()[:240],
        "sections": sections,
        "terms": terms,
    }


def _generate_from_chroma(*, lesson_id: str, topic_id: str, lesson_title: str | None) -> dict[str, Any]:
    q = build_retrieval_query(
        topic_id=topic_id,
        lesson_title=lesson_title,
        event="lesson_start",
    )
    docs, _ids = retrieve_chunks(topic_id=topic_id, query_hint=q, top_k=12)
    if not docs:
        raise ValueError(
            "No textbook chunks found for this chapter. Run Chroma ingest (scripts/ingest.py)."
        )

    joined = "\n\n---\n\n".join(f"[EXCERPT {i + 1}]\n{chunk}" for i, chunk in enumerate(docs))
    title = lesson_title or lesson_id
    user = (
        f"CHAPTER: {title}\n"
        f"TOPIC_ID: {topic_id}\n\n"
        f"TEXTBOOK EXCERPTS (sole source of truth):\n{joined}\n\n"
        "Build the cheat sheet JSON for students revising this chapter."
    )

    from app.lesson_service import _llm_client_and_model

    client, model = _llm_client_and_model()
    messages = [
        {"role": "system", "content": CHEAT_SHEET_SYSTEM},
        {"role": "user", "content": user},
    ]
    raw = _chat_completion_text(
        client,
        model,
        messages,
        max_tokens=1400,
        base_temperature=0.25,
        topic_id=topic_id,
    )
    if not raw:
        raise RuntimeError("Empty cheat sheet completion from model.")
    payload = _normalize_cheatsheet(_extract_json(raw), fallback_title=title)
    payload["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["source"] = "chroma"
    return payload


def get_or_generate_cheatsheet(lesson_id: str, *, force: bool = False) -> dict[str, Any]:
    """Return cached cheat sheet or generate from Chroma + LLM."""
    lid = (lesson_id or "").strip()
    cur = load_curriculum()
    le = cur.by_lesson_id(lid)
    if not le:
        raise KeyError(f"Unknown lesson_id: {lid}")

    if not force:
        cached = get_cheatsheet(lid)
        if cached:
            return {
                "lesson_id": lid,
                "title": le.title,
                "grade": le.grade,
                "cheatsheet": cached,
                "cached": True,
                "generated_at": cached.get("built_at"),
            }

    t0 = time.perf_counter()
    sheet = _generate_from_chroma(
        lesson_id=lid,
        topic_id=le.topic_id,
        lesson_title=le.title,
    )
    save_cheatsheet(lid, sheet)
    log.info(
        "cheatsheet generated lesson=%s sections=%s terms=%s %.3fs",
        lid,
        len(sheet.get("sections") or []),
        len(sheet.get("terms") or []),
        time.perf_counter() - t0,
    )
    return {
        "lesson_id": lid,
        "title": le.title,
        "grade": le.grade,
        "cheatsheet": sheet,
        "cached": False,
        "generated_at": sheet.get("built_at"),
    }
