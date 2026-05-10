import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.chroma_setup import COLLECTION_NAME, get_chroma_client
from app.curriculum import curriculum_public_dict, load_curriculum
from app.lesson_service import build_lesson
from app.progress_store import ProgressAction, get_progress, init_db, update_progress
from app.textbook_ingest import collection_stats

load_dotenv()

log = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

app = FastAPI(title="Learning Path Engine", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LessonRequest(BaseModel):
    """What the frontend (or another service) sends when starting / continuing a lesson."""

    user_id: str = Field(..., examples=["demo-1"])
    topic_id: str = Field(
        default="g6_science",
        examples=["g6_science", "g6_science_ch03"],
        description="Chroma metadata topic_id. Ignored when lesson_id is set (curriculum resolves scope).",
    )
    lesson_id: str | None = Field(
        default=None,
        examples=["g6_sci_03"],
        description="If set, retrieval is limited to that lesson's chapter (see GET /curriculum).",
    )
    profile: str = Field(
        ...,
        description="weak | average | strong — later this comes from analytics.",
        examples=["weak"],
    )
    event: str | None = Field(
        default="lesson_start",
        description=(
            "lesson_start | game_failed_return | wrap_up_success. "
            "game_failed_return asks for extra support after wrong game answers; "
            "wrap_up_success is the closing pass state."
        ),
    )


class AssessmentScope(BaseModel):
    topic_id: str
    chunk_ids: list[str] = Field(default_factory=list)
    lesson_id: str | None = None


class LessonResponse(BaseModel):
    """What your React app renders; question-bank / chatbot can reuse assessment_scope."""

    lesson_text: str
    minion_state: str
    source_chunk_ids: list[str]
    assessment_scope: AssessmentScope
    lesson_id: str | None = None
    retrieval_topic_id: str | None = Field(
        default=None,
        description="Effective Chroma topic_id used for retrieval.",
    )


class ProgressResponse(BaseModel):
    user_id: str
    current_lesson_id: str
    completed_lesson_ids: list[str]
    quiz_by_lesson: dict[str, Any]
    updated_at: str
    current_index: int | None = None
    lessons_total: int = 0
    next_lesson_id: str | None = None
    resume_step_index: int = 0


class CurrentLessonResponse(BaseModel):
    user_id: str
    current_lesson_id: str
    current_lesson_title: str | None = None
    next_lesson_id: str | None = None
    current_index: int | None = None
    lessons_total: int = 0
    updated_at: str
    resume_step_index: int = 0


class ProgressUpdateRequest(BaseModel):
    user_id: str
    action: ProgressAction
    lesson_id: str
    score: float | None = Field(
        default=None,
        description="0..1 for record_quiz; if >= server threshold, lesson is marked complete and path advances.",
    )
    step_index: int | None = Field(
        default=None,
        description="Current step/page index in lesson stage for resume support.",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


class ClientLogRequest(BaseModel):
    """Browser-reported failures; logged server-side only (not returned to learners)."""

    context: str = Field(default="unknown", max_length=160)
    message: str = Field(default="", max_length=2000)
    detail: str | None = Field(default=None, max_length=8000)
    user_id: str | None = Field(default=None, max_length=160)
    offline: bool = False
    user_agent: str | None = Field(default=None, max_length=520)
    component_stack: str | None = Field(default=None, max_length=8000)


@app.post("/client-log")
def client_log(body: ClientLogRequest) -> Response:
    log.warning(
        "client_report context=%r user_id=%r offline=%s msg=%.400s ua=%.200s stack=%.300s detail=%.300s",
        body.context,
        body.user_id,
        body.offline,
        body.message or "",
        body.user_agent or "",
        body.component_stack or "",
        body.detail or "",
    )
    return Response(status_code=204)


class SearchRequest(BaseModel):
    """Try retrieval without the LLM — proves Chroma + PDF ingest work."""

    query: str = Field(..., examples=["photosynthesis and sunlight"])
    topic_id: str | None = Field(
        default=None,
        description="If set, filter metadata topic_id (e.g. g6_science_ch03).",
    )
    lesson_id: str | None = Field(
        default=None,
        description="If set (and topic_id omitted), resolves topic_id from curriculum.",
    )
    top_k: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    id: str
    text: str
    distance: float | None = None
    metadata: dict


class SearchResponse(BaseModel):
    hits: list[SearchHit]


@app.get("/debug/chroma-stats")
def debug_chroma_stats() -> dict:
    """How many chunks are stored (0 if ingest not run yet)."""
    return collection_stats()


def _resolve_search_topic_id(body: SearchRequest) -> str | None:
    if body.topic_id:
        return body.topic_id
    if body.lesson_id:
        cur = load_curriculum()
        le = cur.by_lesson_id(body.lesson_id)
        if not le:
            raise HTTPException(status_code=400, detail=f"Unknown lesson_id: {body.lesson_id!r}")
        return le.topic_id
    return None


def _theory_where(topic_id: str | None) -> dict:
    if topic_id:
        return {"$and": [{"topic_id": topic_id}, {"content_type": "theory"}]}
    return {"content_type": "theory"}


@app.post("/debug/search", response_model=SearchResponse)
def debug_search(body: SearchRequest) -> SearchResponse:
    client = get_chroma_client()
    try:
        col = client.get_collection(COLLECTION_NAME)
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=503,
            detail=f"Chroma collection missing. Run: python scripts/ingest.py ({exc})",
        ) from exc

    kwargs: dict = {
        "query_texts": [body.query],
        "n_results": body.top_k,
        "include": ["documents", "metadatas", "distances"],
    }
    tid = _resolve_search_topic_id(body)
    kwargs["where"] = _theory_where(tid)

    raw = col.query(**kwargs)
    ids = (raw.get("ids") or [[]])[0]
    docs = (raw.get("documents") or [[]])[0]
    metas = (raw.get("metadatas") or [[]])[0]
    dists = (raw.get("distances") or [[]])[0]

    hits: list[SearchHit] = []
    for i, cid in enumerate(ids):
        text = docs[i] if i < len(docs) else ""
        meta = metas[i] if i < len(metas) else {}
        dist = dists[i] if i < len(dists) else None
        hits.append(SearchHit(id=cid, text=text or "", distance=dist, metadata=meta or {}))
    return SearchResponse(hits=hits)


@app.get("/curriculum")
def get_curriculum() -> dict:
    """Ordered lesson path + page ranges (for chapter-scoped ingest and UI)."""
    return curriculum_public_dict(load_curriculum())


def _enrich_progress(state: dict) -> ProgressResponse:
    cur = load_curriculum()
    cid = state["current_lesson_id"]
    idx = cur.index_of(cid)
    nxt = cur.next_lesson_id(cid)
    session = dict(state.get("session_state") or {})
    resume_step = int(session.get("step_index") or 0)
    return ProgressResponse(
        user_id=state["user_id"],
        current_lesson_id=cid,
        completed_lesson_ids=list(state["completed_lesson_ids"]),
        quiz_by_lesson=dict(state["quiz_by_lesson"]),
        updated_at=state["updated_at"],
        current_index=idx,
        lessons_total=len(cur.lessons),
        next_lesson_id=nxt,
        resume_step_index=resume_step,
    )


@app.get("/progress", response_model=ProgressResponse)
def read_progress(user_id: str) -> ProgressResponse:
    st = get_progress(user_id)
    return _enrich_progress(st)


@app.get("/lesson/current", response_model=CurrentLessonResponse)
def read_current_lesson(user_id: str) -> CurrentLessonResponse:
    st = get_progress(user_id)
    enriched = _enrich_progress(st)
    cur = load_curriculum()
    lesson = cur.by_lesson_id(enriched.current_lesson_id)
    return CurrentLessonResponse(
        user_id=enriched.user_id,
        current_lesson_id=enriched.current_lesson_id,
        current_lesson_title=lesson.title if lesson else None,
        next_lesson_id=enriched.next_lesson_id,
        current_index=enriched.current_index,
        lessons_total=enriched.lessons_total,
        updated_at=enriched.updated_at,
        resume_step_index=enriched.resume_step_index,
    )


@app.post("/progress", response_model=ProgressResponse)
def write_progress(body: ProgressUpdateRequest) -> ProgressResponse:
    st = update_progress(
        body.user_id,
        action=body.action,
        lesson_id=body.lesson_id,
        score=body.score,
        step_index=body.step_index,
    )
    return _enrich_progress(st)


@app.post("/lesson", response_model=LessonResponse)
def post_lesson(body: LessonRequest) -> LessonResponse:
    """
    RAG + LLM: Chroma retrieval, then Groq (if GROQ_API_KEY) or xAI (if XAI_API_KEY) chat completions.
    """
    t0 = time.perf_counter()
    log.info(
        "lesson request user=%r lesson_id=%r profile=%r event=%r",
        body.user_id,
        body.lesson_id,
        body.profile,
        body.event,
    )
    if not (
        os.getenv("GROQ_API_KEY")
        or os.getenv("XAI_API_KEY")
        or os.getenv("GROK_API_KEY")
    ):
        raise HTTPException(
            status_code=501,
            detail="Set GROQ_API_KEY (Groq) or XAI_API_KEY / GROK_API_KEY (xAI) in backend/.env.",
        )
    try:
        text, minion_state, chunk_ids, retrieval_topic, resolved_lesson = build_lesson(
            topic_id=body.topic_id,
            profile=body.profile,
            event=body.event,
            lesson_id=body.lesson_id,
        )
    except Exception as exc:
        log.exception(
            "lesson pipeline failed user=%r lesson_id=%r after %.3fs",
            body.user_id,
            body.lesson_id,
            time.perf_counter() - t0,
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if resolved_lesson:
        try:
            update_progress(
                body.user_id,
                action="set_current",
                lesson_id=resolved_lesson,
            )
        except Exception as exc_update:
            log.warning("set_current failed user=%r: %s", body.user_id, exc_update)

    dt = time.perf_counter() - t0
    log.info(
        "lesson ok user=%r resolved_lesson=%r chunks=%s text_chars=%s topic=%s minion=%s %.3fs",
        body.user_id,
        resolved_lesson,
        len(chunk_ids),
        len(text or ""),
        retrieval_topic,
        minion_state,
        dt,
    )

    return LessonResponse(
        lesson_text=text,
        minion_state=minion_state,
        source_chunk_ids=chunk_ids,
        assessment_scope=AssessmentScope(
            topic_id=retrieval_topic,
            chunk_ids=chunk_ids,
            lesson_id=resolved_lesson,
        ),
        lesson_id=resolved_lesson,
        retrieval_topic_id=retrieval_topic,
    )
