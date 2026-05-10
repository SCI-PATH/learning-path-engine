"""
RAG + LLM: retrieve textbook chunks (Chroma), then generate a profile-aware lesson.

Providers (OpenAI-compatible `chat.completions`):

  **Groq** (preferred if set): https://console.groq.com/
    GROQ_API_KEY
    GROQ_BASE_URL   — default https://api.groq.com/openai/v1
    GROQ_MODEL      — default llama-3.3-70b-versatile (see Groq console for current ids)

  **xAI Grok** (if GROQ_API_KEY is unset and xAI key is set):
    XAI_API_KEY or GROK_API_KEY
    XAI_BASE_URL    — default https://api.x.ai/v1
    XAI_MODEL       — default grok-4-1-fast-non-reasoning
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Literal

from app.chroma_setup import COLLECTION_NAME, get_chroma_client
from app.curriculum import load_curriculum

Profile = Literal["weak", "average", "strong"]

log = logging.getLogger(__name__)


def _normalize_profile(raw: str) -> Profile:
    v = (raw or "average").strip().lower()
    if v in ("weak", "struggling", "beginner", "low"):
        return "weak"
    if v in ("strong", "advanced", "high"):
        return "strong"
    return "average"


def _profile_system_instructions(profile: Profile, event: str | None) -> str:
    """Pedagogy knobs — maps to your 'dynamic content / scaffolding' story."""
    base = (
        "You are a Grade 6 science tutor in Sri Lanka. Be accurate, encouraging, and concise. "
        "Use simple English suitable for 11–12 year olds when appropriate."
    )
    if profile == "weak":
        style = (
            "The learner needs EXTRA support: very short sentences, step-by-step, "
            "one idea at a time, at least one everyday analogy, and optional a tiny recap bullet list. "
            "Avoid jargon unless you define it in plain words."
        )
    elif profile == "strong":
        style = (
            "The learner is comfortable: you may be a bit denser, use correct scientific terms, "
            "connect ideas more quickly, and skip hand-holding — but stay clear."
        )
    else:
        style = (
            "The learner is typical: balance clarity with a normal level of detail; "
            "define new terms once; one short example is enough."
        )

    evt = (event or "lesson_start").lower()
    if evt in ("wrong_answer", "game_failed_return"):
        style += (
            " They returned after failing a quiz/game item: give extra support with a gentle nudge, "
            "one misconception fix, and a tiny confidence-building recap from CONTEXT only."
        )
    elif evt in ("lesson_complete", "quiz_passed", "wrap_up_success"):
        style += (
            " This is a wrap-up after correct answers: briefly reinforce key points and end with "
            "a motivating bridge to the next lesson."
        )

    return f"{base}\n\n{style}"


def _minion_state_for_event(event: str | None) -> str:
    evt = (event or "lesson_start").lower()
    if evt in ("wrong_answer", "game_failed_return"):
        return "hint"
    if evt in ("lesson_complete", "quiz_passed", "wrap_up_success"):
        return "celebrate"
    return "teaching"


def _build_theory_where(topic_id: str | None) -> dict:
    if topic_id:
        return {"$and": [{"topic_id": topic_id}, {"content_type": "theory"}]}
    return {"content_type": "theory"}


def retrieve_chunks(
    *,
    topic_id: str,
    query_hint: str,
    top_k: int = 6,
) -> tuple[list[str], list[str]]:
    """Return (documents, ids) from Chroma."""
    kwargs: dict = {
        "query_texts": [query_hint],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }
    kwargs["where"] = _build_theory_where(topic_id if topic_id else None)
    raw = _query_with_recovery(kwargs)
    ids = (raw.get("ids") or [[]])[0]
    docs = (raw.get("documents") or [[]])[0]
    out_ids: list[str] = []
    out_docs: list[str] = []
    for i, cid in enumerate(ids):
        text = docs[i] if i < len(docs) else ""
        if text and cid:
            out_ids.append(str(cid))
            out_docs.append(_normalize_chunk_for_llm(str(text)))
    return out_docs, out_ids


def _query_with_recovery(kwargs: dict) -> dict:
    """
    Chroma can throw transient on-disk segment errors right after re-ingest
    while another process still holds old readers. Retry with fresh handles once.
    """
    client = get_chroma_client()
    col = client.get_collection(COLLECTION_NAME)
    try:
        return col.query(**kwargs)
    except Exception:
        client = get_chroma_client()
        col = client.get_collection(COLLECTION_NAME)
        return col.query(**kwargs)


def _normalize_chunk_for_llm(text: str) -> str:
    """
    Final light cleanup so textbook noise (captions/headers) is less likely to leak.
    """
    t = text.strip()
    t = re.sub(r"\bFig\.?\s*\d+(?:\.\d+)?\b.*?(?=\s[A-Z]|$)", "", t, flags=re.I)
    t = re.sub(r"\bScience\s*\|[A-Za-z\s]+\b", "", t, flags=re.I)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def build_retrieval_query(
    topic_id: str,
    profile: str,
    event: str | None,
    *,
    lesson_title: str | None = None,
) -> str:
    """Turn API fields into a single semantic query for embedding search."""
    tid = topic_id.replace("_", " ")
    focus = f" Lesson focus: {lesson_title}." if lesson_title else ""
    return (
        f"Grade 6 science textbook material about: {tid}.{focus} "
        f"Learner support level: {profile}. "
        f"Situation: {event or 'lesson_start'}."
    )


def _llm_client_and_model() -> tuple[object, str]:
    """Return (OpenAI client, model_id). Groq takes priority if GROQ_API_KEY is set."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install the openai package: pip install openai") from exc

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        base = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        return OpenAI(api_key=groq_key, base_url=base), model

    xai_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
    if xai_key:
        base = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
        model = os.getenv("XAI_MODEL", "grok-4-1-fast-non-reasoning")
        return OpenAI(api_key=xai_key, base_url=base), model

    raise RuntimeError(
        "Missing LLM API key. Set GROQ_API_KEY (https://console.groq.com/) "
        "or XAI_API_KEY / GROK_API_KEY in backend/.env."
    )


def generate_lesson_text(
    *,
    context_chunks: list[str],
    chunk_ids: list[str],
    topic_id: str,
    profile: str,
    event: str | None,
) -> str:
    """Call Groq or xAI via OpenAI-compatible chat completions."""
    client, model = _llm_client_and_model()

    prof = _normalize_profile(profile)
    system = _profile_system_instructions(prof, event)

    joined = "\n\n---\n\n".join(
        f"[PASSAGE {i + 1}]\n{chunk}" for i, chunk in enumerate(context_chunks)
    )
    user = (
        f"TOPIC_ID: {topic_id}\n"
        f"Use ONLY the passages below as your source of facts. "
        f"If something is not in the passages, say you do not have that information in the textbook excerpt.\n\n"
        f"PASSAGES:\n{joined}\n\n"
        f"TASK: Write one cohesive lesson segment (not a list of quiz questions) that teaches the ideas relevant "
        f"to this topic for this learner. Start with a short title line, then 2–6 short paragraphs (or bullets only "
        f"if the learner is weak and you already said you'd use bullets). No filler about being an AI."
    )

    resp = client.chat.completions.create(
        model=model,
        temperature=0.35,
        max_tokens=1800,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    choice = resp.choices[0].message.content
    if not choice:
        raise RuntimeError("Empty completion from model.")
    return choice.strip()


def build_lesson(
    *,
    topic_id: str,
    profile: str,
    event: str | None,
    lesson_id: str | None = None,
    top_k: int = 6,
) -> tuple[str, str, list[str], str, str | None]:
    """
    Full pipeline: retrieve → generate →
    (text, minion_state, chunk_ids, retrieval_topic_id, lesson_id_or_none).

    If lesson_id is set, retrieval uses that lesson's topic_id from curriculum
    (chapter-scoped Chroma filter).
    """
    lesson_title: str | None = None
    resolved_lesson: str | None = None
    if lesson_id and lesson_id.strip():
        cur = load_curriculum()
        entry = cur.by_lesson_id(lesson_id)
        if not entry:
            raise RuntimeError(
                f"Unknown lesson_id: {lesson_id!r}. See GET /curriculum for valid ids."
            )
        effective_topic = entry.topic_id
        lesson_title = entry.title
        resolved_lesson = entry.lesson_id
    else:
        effective_topic = topic_id

    q = build_retrieval_query(
        effective_topic, profile, event, lesson_title=lesson_title
    )
    t_chroma = time.perf_counter()
    docs, ids = retrieve_chunks(topic_id=effective_topic, query_hint=q, top_k=top_k)
    log.info(
        "chroma query topic=%r lesson_id=%r top_k=%s hits=%s %.3fs",
        effective_topic,
        resolved_lesson,
        top_k,
        len(docs),
        time.perf_counter() - t_chroma,
    )
    if not docs:
        raise RuntimeError(
            "No chunks retrieved. For chapter ingest use topic_id like g6_science_ch01 or pass "
            "lesson_id after re-running: python scripts/ingest.py"
        )
    t_llm = time.perf_counter()
    text = generate_lesson_text(
        context_chunks=docs,
        chunk_ids=ids,
        topic_id=effective_topic,
        profile=profile,
        event=event,
    )
    log.info(
        "llm complete topic=%r profile=%r event=%r out_chars=%s %.3fs",
        effective_topic,
        _normalize_profile(profile),
        (event or "lesson_start").lower(),
        len(text or ""),
        time.perf_counter() - t_llm,
    )
    return text, _minion_state_for_event(event), ids, effective_topic, resolved_lesson
