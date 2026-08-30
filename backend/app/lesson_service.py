"""
RAG + LLM: retrieve textbook chunks (Chroma), then generate a profile-aware lesson.

Providers (OpenAI-compatible `chat.completions`):

  **Groq** (preferred if set): https://console.groq.com/
    GROQ_API_KEY
    GROQ_BASE_URL   — default https://api.groq.com/openai/v1
    GROQ_MODEL      — default openai/gpt-oss-120b (llama-3.3-70b retired Aug 2026)
    GROQ_FALLBACK_MODEL — default qwen/qwen3.6-27b when primary is empty or unavailable

  **xAI Grok** (if GROQ_API_KEY is unset and xAI key is set):
    XAI_API_KEY or GROK_API_KEY
    XAI_BASE_URL    — default https://api.x.ai/v1
    XAI_MODEL       — default grok-4-1-fast-non-reasoning

Prompt contracts live in app.prompts (Phase A prompt engineering).
"""

from __future__ import annotations

import logging
import os
import re
import time

from app.chroma_setup import COLLECTION_NAME, get_chroma_client
import re

from app.content_filters import is_non_lesson_chunk, scrub_chunk_for_lesson, polish_generated_lesson
from app.topic_ids import chroma_theory_where, resolve_chroma_topic_id, resolve_lesson
from app.prompts import (
    PROFILE_MAX_TOKENS,
    PROFILE_TEMPERATURE,
    build_enrichment_retrieval_query,
    build_retrieval_query,
    build_system_message,
    build_user_message,
    normalize_event,
    normalize_profile,
    presentation_mode_for_profile,
    retrieval_top_k,
)

log = logging.getLogger(__name__)


def _minion_state_for_event(event: str | None) -> str:
    evt = (event or "lesson_start").lower()
    if evt in ("wrong_answer", "game_failed_return"):
        return "hint"
    if evt in ("lesson_complete", "quiz_passed", "wrap_up_success"):
        return "celebrate"
    return "teaching"


def retrieve_chunks(
    *,
    topic_id: str,
    query_hint: str,
    top_k: int = 6,
) -> tuple[list[str], list[str]]:
    """
    Return (documents, ids) from Chroma — lesson/theory text only.
    Over-fetches then drops activity/question chunks and scrubs leftovers.

    topic_id may be Assessment-style (G7_S1_PLA_DIVER), a sibling skill ID,
    legacy (g7_science_ch01), or lesson_id — all resolve to the Chroma primary.
    """
    fetch_k = min(max(top_k * 3, top_k + 6), 24)
    kwargs: dict = {
        "query_texts": [query_hint],
        "n_results": fetch_k,
        "include": ["documents", "metadatas", "distances"],
    }
    kwargs["where"] = chroma_theory_where(topic_id if topic_id else None)
    raw = _query_with_recovery(kwargs)
    ids = (raw.get("ids") or [[]])[0]
    docs = (raw.get("documents") or [[]])[0]
    out_ids: list[str] = []
    out_docs: list[str] = []
    for i, cid in enumerate(ids):
        text = docs[i] if i < len(docs) else ""
        if not text or not cid:
            continue
        if is_non_lesson_chunk(str(text)):
            continue
        cleaned = scrub_chunk_for_lesson(str(text))
        if len(cleaned) < 120:
            continue
        if is_non_lesson_chunk(cleaned):
            continue
        out_ids.append(str(cid))
        out_docs.append(cleaned)
        if len(out_docs) >= top_k:
            break
    return out_docs, out_ids


def _merge_chunk_results(
    primary_docs: list[str],
    primary_ids: list[str],
    extra_docs: list[str],
    extra_ids: list[str],
    *,
    cap: int,
) -> tuple[list[str], list[str]]:
    """Append enrichment hits without duplicate chunk ids."""
    seen = set(primary_ids)
    docs = list(primary_docs)
    ids = list(primary_ids)
    for cid, doc in zip(extra_ids, extra_docs):
        if cid in seen or not doc:
            continue
        if is_non_lesson_chunk(doc):
            continue
        seen.add(cid)
        docs.append(doc)
        ids.append(cid)
        if len(docs) >= cap:
            break
    return docs, ids


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


def _llm_client_and_model() -> tuple[object, str]:
    """Return (OpenAI client, model_id). Groq takes priority if GROQ_API_KEY is set."""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Install the openai package: pip install openai") from exc

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        base = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
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
    lesson_title: str | None = None,
    grade: int | None = None,
) -> str:
    """Call Groq or xAI via OpenAI-compatible chat completions."""
    client, model = _llm_client_and_model()

    prof = normalize_profile(profile)
    system = build_system_message(profile=profile, event=event, grade=grade)
    user = build_user_message(
        topic_id=topic_id,
        lesson_title=lesson_title,
        passages=context_chunks,
        profile=profile,
        event=event,
        grade=grade,
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    models = [model]
    if os.getenv("GROQ_API_KEY"):
        fallback = os.getenv("GROQ_FALLBACK_MODEL", "qwen/qwen3.6-27b").strip()
        if fallback and fallback not in models:
            models.append(fallback)

    last_exc: Exception | None = None
    for model_id in models:
        try:
            choice = _chat_completion_text(
                client, model_id, messages, PROFILE_MAX_TOKENS[prof], PROFILE_TEMPERATURE[prof], topic_id
            )
            if choice:
                if model_id != model:
                    log.info("llm fallback model=%s topic=%r", model_id, topic_id)
                return polish_generated_lesson(choice, lesson_title=lesson_title)
            log.warning("empty completion from model=%s topic=%r", model_id, topic_id)
        except Exception as exc:
            last_exc = exc
            log.warning("llm model=%s failed topic=%r: %s", model_id, topic_id, exc)

    msg = "Empty completion from model. Groq may be rate-limited — wait a minute and try again."
    if last_exc:
        raise RuntimeError(msg) from last_exc
    raise RuntimeError(msg)


def _completion_extra_body(model: str) -> dict:
    """Groq reasoning controls — keep only final lesson text in message.content."""
    m = model.lower()
    if "qwen" in m:
        return {"reasoning_effort": "none"}
    if "gpt-oss" in m:
        return {"include_reasoning": False, "reasoning_effort": "low"}
    return {}


def _rate_limit_wait_seconds(exc: Exception, attempt: int) -> float:
    """Backoff for Groq/OpenAI 429; parse 'try again in Xs' when present."""
    msg = str(exc)
    m = re.search(r"try again in ([\d.]+)s", msg, re.I)
    if m:
        return float(m.group(1)) + 1.0
    return float(2**attempt)


def _should_fail_fast_to_fallback(exc: Exception) -> bool:
    """Skip retries on daily quota or long TPM waits so fallback model can run."""
    msg = str(exc).lower()
    if "tokens per day" in msg or "tpd" in msg:
        return True
    m = re.search(r"try again in ([\d.]+)s", str(exc), re.I)
    return bool(m and float(m.group(1)) > 30)


def _chat_completion_text(
    client: object,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    base_temperature: float,
    topic_id: str,
) -> str:
    """Call chat completions with retries; return stripped text or empty string."""
    for attempt in range(3):
        temp = min(base_temperature + (0.05 * attempt), 0.5)
        try:
            extra = _completion_extra_body(model)
            resp = client.chat.completions.create(
                model=model,
                temperature=temp,
                max_tokens=max_tokens,
                messages=messages,
                **({"extra_body": extra} if extra else {}),
            )
        except Exception as exc:
            if _should_fail_fast_to_fallback(exc):
                raise
            if attempt < 2:
                log.warning("llm request failed attempt=%s model=%s: %s", attempt + 1, model, exc)
                wait_s = _rate_limit_wait_seconds(exc, attempt)
                time.sleep(wait_s)
                continue
            raise
        choice = (resp.choices[0].message.content or "").strip()
        if choice:
            return choice
        finish = getattr(resp.choices[0], "finish_reason", None)
        log.warning(
            "empty llm completion attempt=%s finish_reason=%s model=%s topic=%r",
            attempt + 1,
            finish,
            model,
            topic_id,
        )
        if attempt < 2:
            time.sleep(2**attempt)
    return ""


def build_lesson(
    *,
    topic_id: str,
    profile: str,
    event: str | None,
    lesson_id: str | None = None,
    top_k: int | None = None,
) -> tuple[str, str, list[str], str, str | None]:
    """
    Full pipeline: retrieve → generate →
    (text, minion_state, chunk_ids, retrieval_topic_id, lesson_id_or_none).

    If lesson_id is set, retrieval uses that lesson's topic_id from curriculum
    (chapter-scoped Chroma filter).
    """
    lesson_title: str | None = None
    resolved_lesson: str | None = None
    grade: int | None = None

    if lesson_id and lesson_id.strip():
        entry = resolve_lesson(lesson_id)
        if not entry:
            raise RuntimeError(
                f"Unknown lesson_id: {lesson_id!r}. See GET /curriculum for valid ids."
            )
        effective_topic = entry.topic_id
        lesson_title = entry.title
        resolved_lesson = entry.lesson_id
        grade = entry.grade
    else:
        mapped = resolve_lesson(topic_id)
        if mapped:
            effective_topic = mapped.topic_id
            lesson_title = mapped.title
            resolved_lesson = mapped.lesson_id
            grade = mapped.grade
        else:
            effective_topic = resolve_chroma_topic_id(topic_id)
            # Fallback regex for topic_id like "G9_S1..." or "g9_ch..."
            m = re.search(r"[gG](\d+)", topic_id)
            if m:
                grade = int(m.group(1))

    prof = normalize_profile(profile)
    k = top_k if top_k is not None else retrieval_top_k(profile)

    q = build_retrieval_query(
        topic_id=effective_topic,
        lesson_title=lesson_title,
        event=event,
    )
    t_chroma = time.perf_counter()
    docs, ids = retrieve_chunks(topic_id=effective_topic, query_hint=q, top_k=k)

    # Strong learners: second retrieval pass for enrichment context (still chapter-scoped).
    if prof == "advanced" and lesson_title and normalize_event(event) == "lesson_start":
        eq = build_enrichment_retrieval_query(
            topic_id=effective_topic,
            lesson_title=lesson_title,
        )
        extra_docs, extra_ids = retrieve_chunks(
            topic_id=effective_topic,
            query_hint=eq,
            top_k=6,
        )
        docs, ids = _merge_chunk_results(docs, ids, extra_docs, extra_ids, cap=k + 6)

    log.info(
        "chroma query topic=%r lesson_id=%r top_k=%s hits=%s %.3fs",
        effective_topic,
        resolved_lesson,
        k,
        len(docs),
        time.perf_counter() - t_chroma,
    )
    if not docs:
        raise RuntimeError(
            "No chunks retrieved. For chapter ingest use Assessment Topic IDs "
            "(e.g. G7_S1_PLA_DIVER) or pass lesson_id from GET /curriculum. "
            "Re-run: python scripts/ingest.py"
        )
    t_llm = time.perf_counter()
    text = generate_lesson_text(
        context_chunks=docs,
        chunk_ids=ids,
        topic_id=effective_topic,
        profile=profile,
        event=event,
        lesson_title=lesson_title,
        grade=grade,
    )
    log.info(
        "llm complete topic=%r profile=%r event=%r out_chars=%s %.3fs",
        effective_topic,
        prof,
        (event or "lesson_start").lower(),
        len(text or ""),
        time.perf_counter() - t_llm,
    )
    return text, _minion_state_for_event(event), ids, effective_topic, resolved_lesson, prof, presentation_mode_for_profile(prof)
