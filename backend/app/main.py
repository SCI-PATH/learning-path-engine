import logging
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.ar_service import ensure_ar_package, get_or_generate_ar
from app.ar_images import MEDIA_ROOT
from app.ar_store import clear_generated_payload, delete_ar, get_ar, init_ar_db, list_ar, upsert_ar
from app.ar_topic_store import get_topic_asset, init_ar_topic_db
from app.ar_topic_service import (
    approve_topic_ar_pack,
    generate_topic_ar_pack,
    get_approved_topic_payload_for_topic_id,
    list_teacher_topic_ar_packs,
    get_public_topic_ar_pack,
)
from app.lesson_media_store import init_lesson_media_db
from app.lesson_media_service import (
    approve_lesson_summary,
    attach_lesson_image,
    generate_lesson_summary,
    public_media,
    remove_lesson_image,
    set_lesson_videos,
    set_lesson_youtube,
    upload_lesson_image_file,
)
from app.chroma_setup import COLLECTION_NAME, get_chroma_client
from app.content_library import (
    delete_content,
    find_content,
    find_content_with_fallback,
    get_content,
    init_library_db,
    list_content,
    migrate_topic_ids,
    update_content,
    upsert_content,
)
from app.curriculum import curriculum_public_dict, load_curriculum
from app.lesson_service import build_lesson
from app.mastery_profile import (
    profile_display_label,
    resolve_lesson_profile,
)
from app.progress_store import (
    ProgressAction,
    get_progress,
    init_db,
    set_learner_profile,
    set_mastery_score,
    update_progress,
)
from app.prompts import normalize_event, normalize_profile
from app.textbook_ingest import collection_stats
from app.topic_ids import chroma_theory_where, resolve_chroma_topic_id, resolve_lesson, topic_public_dict

load_dotenv()

log = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Service health.",
    },
    {
        "name": "student-lessons",
        "description": (
            "Student learning path: **current lesson** identity (lesson_id, grade, chapter_number), "
            "load published lesson text, resume progress, chapter media & AR."
        ),
    },
    {
        "name": "progress",
        "description": "Learner progress along the ordered curriculum path.",
    },
    {
        "name": "curriculum",
        "description": "Ordered chapters by grade; resolve Assessment / skill / legacy IDs.",
    },
    {
        "name": "analytics",
        "description": "Learner profile (weak | average | strong) and legacy mastery score.",
    },
    {
        "name": "teacher-library",
        "description": "Generate, publish, and edit chapter content per knowledge level.",
    },
    {
        "name": "teacher-media",
        "description": "YouTube + summary infographic per chapter.",
    },
    {
        "name": "ar",
        "description": "Chapter / topic AR packs for students and teachers.",
    },
    {
        "name": "admin-debug",
        "description": "Dev/admin helpers (Chroma stats, topic-id migration).",
    },
]

app = FastAPI(
    title="Learning Path Engine",
    version="0.2.0",
    description=(
        "## SCI-PATH Learning Path Engine\n\n"
        "Curriculum-ordered lessons, teacher-approved content, progress, media, and AR.\n\n"
        "### Cross-service: student name & grade\n"
        "Fetch identity from User Management: "
        "`GET http://localhost:8001/students/{student_id}` → "
        "`{ student_id, full_name, grade }`.\n\n"
        "### Current lesson being taught\n"
        "- `GET /lesson/current?user_id=…` → **lesson_id**, **grade**, **chapter_number**, title, resume step\n"
        "- Add `include_content=true` to also return published **lesson_text** (learning material)\n"
        "- `POST /lesson` with `{ user_id, lesson_id, profile? }` → full learning material for that chapter\n"
        "- `GET /lesson/{lesson_id}/media` → YouTube + summary for the chapter\n"
        "- `GET /progress?user_id=…` → path position + stored grade/profile\n\n"
        "Lesson ids look like `g6_sci_03` (grade 6, chapter 3). "
        "Interactive docs: `/docs` · `/redoc`"
    ),
    openapi_tags=OPENAPI_TAGS,
    contact={"name": "SCI-PATH"},
)

# If true, student POST /lesson may generate when library miss (dev only).
STUDENT_ALLOW_GENERATE = os.getenv("STUDENT_ALLOW_GENERATE", "false").lower() in (
    "1",
    "true",
    "yes",
)

_CHAPTER_FROM_SECTION = re.compile(r"_S(\d+)$", re.I)
_CHAPTER_FROM_LESSON = re.compile(r"_(\d+)$")


def _chapter_number_for_lesson(le: Any) -> int | None:
    """1-based chapter number from skill_section_id (G6_S3) or lesson_id (g6_sci_03)."""
    sid = getattr(le, "skill_section_id", None) or ""
    m = _CHAPTER_FROM_SECTION.search(str(sid))
    if m:
        return int(m.group(1))
    lid = getattr(le, "lesson_id", None) or ""
    m = _CHAPTER_FROM_LESSON.search(str(lid))
    if m:
        return int(m.group(1))
    return None


@app.on_event("startup")
def _startup() -> None:
    init_db()
    init_library_db()
    init_ar_db()
    init_ar_topic_db()
    init_lesson_media_db()
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stored AR photos: backend/data/ar_media/{lesson_id}/{scene}.jpg
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/ar-media", StaticFiles(directory=str(MEDIA_ROOT)), name="ar-media")


class LessonRequest(BaseModel):
    """Student starts a lesson — content comes from the teacher library."""

    user_id: str = Field(..., examples=["demo-1"])
    topic_id: str = Field(
        default="G6_S1_ORG_CHARS",
        examples=["G7_S1_PLA_DIVER", "G7_S1_PLA_CLASSIF", "g7_science_ch01"],
        description="Assessment Topic ID, sibling skill ID, or legacy Chroma id. Ignored when lesson_id is set.",
    )
    lesson_id: str | None = Field(
        default=None,
        examples=["g6_sci_03"],
        description="Chapter / lesson to load from the teacher library.",
    )
    profile: str | None = Field(
        default=None,
        description="weak | average | strong (smart) — optional if stored learner profile exists.",
        examples=["weak"],
    )
    mastery_score: float | None = Field(
        default=None,
        description="Deprecated. Prefer profile category from Learner Profile Analytics.",
        examples=[None],
    )
    use_stored_mastery: bool = Field(
        default=True,
        description="When true, use stored learner profile (weak/average/strong) if not sent.",
    )
    event: str | None = Field(
        default="lesson_start",
        description="lesson_start | game_failed_return | wrap_up_success.",
    )


class AssessmentScope(BaseModel):
    topic_id: str
    chunk_ids: list[str] = Field(default_factory=list)
    lesson_id: str | None = None


class LessonResponse(BaseModel):
    """Published (or generated) learning material for a chapter + knowledge level."""

    lesson_text: str = Field(default="", description="Learning material body (markdown/plain).")
    minion_state: str = "idle"
    source_chunk_ids: list[str] = Field(default_factory=list)
    assessment_scope: AssessmentScope | None = None
    lesson_id: str | None = Field(
        default=None,
        description="Curriculum lesson id, e.g. g6_sci_03.",
        examples=["g6_sci_03"],
    )
    grade: int | None = Field(default=None, description="Grade 6–9 for this chapter.", examples=[6])
    chapter_number: int | None = Field(
        default=None,
        description="1-based chapter number within the grade path.",
        examples=[3],
    )
    lesson_title: str | None = Field(default=None, description="Chapter title.")
    retrieval_topic_id: str | None = None
    profile: str | None = Field(default=None, description="weak | average | strong")
    presentation_mode: str | None = None
    status: str = Field(
        default="ready",
        description="ready | unavailable — student only learns when ready.",
    )
    content_id: str | None = None
    message: str | None = None
    mastery_score_used: float | None = None
    profile_source: str | None = None


class MasteryScoreRequest(BaseModel):
    """Deprecated score payload — prefer LearnerProfileRequest."""

    user_id: str = Field(..., examples=["demo-1"])
    mastery_score: float = Field(..., ge=0, description="Deprecated 0–100 score")
    source: str = Field(default="learner_profile_analytics", max_length=120)
    lesson_id: str | None = Field(default=None)


class MasteryScoreResponse(BaseModel):
    """Legacy response shape; use profile field, ignore score/thresholds."""

    user_id: str
    mastery_score: float | None = None
    profile: str
    profile_label: str
    thresholds: dict[str, float] | None = None
    source: str
    updated_at: str


class LearnerProfileRequest(BaseModel):
    """Learner Profile Analytics → store category directly (no score math)."""

    user_id: str = Field(..., examples=["demo-1"])
    profile: str = Field(
        ...,
        description="weak | average | strong | smart",
        examples=["strong"],
    )
    source: str = Field(default="learner_profile_analytics", max_length=120)
    lesson_id: str | None = Field(
        default=None,
        description="Optional chapter context (logged only).",
    )
    grade: int | None = Field(
        default=None,
        description="Optional grade from user session (6–9). Stored when provided.",
        examples=[7],
    )


class LearnerProfileResponse(BaseModel):
    user_id: str
    profile: str
    profile_label: str
    grade: int | None = None
    source: str
    updated_at: str


class TeacherGenerateRequest(BaseModel):
    lesson_id: str = Field(..., examples=["g7_sci_01"])
    profile: str = Field(..., examples=["weak"])
    event: str = Field(default="lesson_start")
    teacher_id: str = Field(default="teacher-1")


class TeacherPublishRequest(BaseModel):
    """Approve generated (or edited) text into the permanent library."""

    lesson_id: str
    profile: str
    event: str = "lesson_start"
    lesson_text: str
    topic_id: str | None = None
    minion_state: str | None = "idle"
    presentation_mode: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    teacher_id: str = "teacher-1"


class TeacherUpdateRequest(BaseModel):
    lesson_text: str | None = None
    lesson_title: str | None = None
    minion_state: str | None = None
    presentation_mode: str | None = None
    teacher_id: str = "teacher-1"


class TeacherARUpsertRequest(BaseModel):
    model_url: str = Field(..., description="HTTPS URL to a .glb / .gltf model")
    caption: str | None = None
    poster_url: str | None = None
    title: str | None = None
    grade: int | None = None


class TeacherLessonMediaRequest(BaseModel):
    youtube_url: str = Field(default="", description="YouTube watch or share URL")
    teacher_id: str = Field(default="teacher-1")


class LessonVideoItem(BaseModel):
    title: str = Field(default="", max_length=120)
    url: str = Field(..., description="YouTube watch, share, shorts, or embed URL")


class TeacherLessonVideosRequest(BaseModel):
    videos: list[LessonVideoItem] = Field(
        default_factory=list,
        max_length=20,
        description="Ordered video library for this chapter.",
    )
    teacher_id: str = Field(default="teacher-1")


class TeacherSummaryApproveRequest(BaseModel):
    teacher_id: str = Field(default="teacher-1")


class TeacherLessonImageRequest(BaseModel):
    image_url: str = Field(..., min_length=1, description="Public or /ar-media image URL")
    caption: str = Field(default="", max_length=200)
    teacher_id: str = Field(default="teacher-1")


class ProgressResponse(BaseModel):
    """Learner position on the ordered curriculum path."""

    user_id: str
    current_lesson_id: str = Field(..., description="Lesson currently being taught.", examples=["g6_sci_03"])
    completed_lesson_ids: list[str]
    quiz_by_lesson: dict[str, Any]
    updated_at: str
    current_index: int | None = Field(default=None, description="0-based index in the grade path.")
    lessons_total: int = 0
    next_lesson_id: str | None = None
    resume_step_index: int = 0
    grade: int | None = Field(default=None, description="Stored learner grade 6–9.")
    chapter_number: int | None = Field(
        default=None,
        description="Chapter number for current_lesson_id.",
    )
    mastery_score: float | None = None
    derived_profile: str | None = None
    profile_label: str | None = None


class CurrentLessonResponse(BaseModel):
    """
    Identity + optional learning material for the lesson currently being taught.
    Use `include_content=true` to attach published `lesson_text`.
    """

    user_id: str = Field(..., examples=["demo-1"])
    current_lesson_id: str = Field(
        ...,
        description="Curriculum lesson id for the chapter in progress.",
        examples=["g6_sci_03"],
    )
    current_lesson_title: str | None = Field(default=None, examples=["Water as a Natural Resource"])
    grade: int | None = Field(default=None, description="Grade 6–9.", examples=[6])
    chapter_number: int | None = Field(
        default=None,
        description="1-based chapter number (e.g. 3 for g6_sci_03 / G6_S3).",
        examples=[3],
    )
    topic_id: str | None = Field(
        default=None,
        description="Assessment primary topic id for this chapter.",
        examples=["G6_S3_WAT_RESOUR"],
    )
    skill_section_id: str | None = Field(default=None, examples=["G6_S3"])
    next_lesson_id: str | None = None
    current_index: int | None = None
    lessons_total: int = 0
    updated_at: str
    resume_step_index: int = 0
    # Optional published learning material (when include_content=true)
    lesson_text: str | None = Field(
        default=None,
        description="Published learning material when include_content=true.",
    )
    content_status: str | None = Field(
        default=None,
        description="ready | unavailable | omitted — set when include_content=true.",
    )
    content_id: str | None = None
    profile: str | None = Field(default=None, description="Profile used to pick library text.")
    message: str | None = None


class ProgressUpdateRequest(BaseModel):
    user_id: str
    action: ProgressAction
    lesson_id: str = Field(
        default="",
        description="Required for most actions; optional for set_grade.",
        examples=["g6_sci_03"],
    )
    score: float | None = Field(
        default=None,
        description="0..1 for record_quiz; if >= server threshold, lesson is marked complete and path advances.",
    )
    step_index: int | None = Field(
        default=None,
        description="Current step/page index in lesson stage for resume support.",
    )
    grade: int | None = Field(
        default=None,
        description="Learner grade (6–9). Used by set_grade; also stored on set_current when lesson implies a grade.",
        examples=[7],
    )


@app.get("/health", tags=["health"], summary="Health check")
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
        description="Filter metadata topic_id. Accepts Assessment IDs (G6_S3_WAT_RESOUR), sibling skills, legacy (g6_science_ch03), or lesson_id.",
        examples=["G6_S3_WAT_RESOUR", "g6_science_ch03"],
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
        return resolve_chroma_topic_id(body.topic_id)
    if body.lesson_id:
        le = resolve_lesson(body.lesson_id)
        if not le:
            raise HTTPException(status_code=400, detail=f"Unknown lesson_id: {body.lesson_id!r}")
        return le.topic_id
    return None


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
    kwargs["where"] = chroma_theory_where(tid)

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


@app.get(
    "/curriculum",
    tags=["curriculum"],
    summary="List ordered chapters (optionally by grade)",
)
def get_curriculum(grade: int | None = None) -> dict:
    """
    Ordered lesson path for the UI.
    Pass ?grade=7 to get only Grade 7 (Parts I+II merged into one path).
    """
    cur = load_curriculum()
    if grade is not None and grade not in cur.available_grades():
        raise HTTPException(
            status_code=400,
            detail=f"Unknown grade {grade}. Available: {cur.available_grades()}",
        )
    return curriculum_public_dict(cur, grade=grade)


@app.get(
    "/curriculum/resolve/{topic_or_skill_id}",
    tags=["curriculum"],
    summary="Resolve topic/skill/legacy id → lesson + chroma topic",
)
def resolve_topic(topic_or_skill_id: str) -> dict:
    """
    Map Assessment / Excel / legacy / lesson ids → Chroma primary topic_id.
    For other components (Assessment, Analytics) that share Chroma Topic IDs.
    """
    le = resolve_lesson(topic_or_skill_id)
    if not le:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown topic/skill/lesson id: {topic_or_skill_id!r}",
        )
    return {
        "input": topic_or_skill_id,
        "chroma_topic_id": le.topic_id,
        **topic_public_dict(le),
    }


@app.post("/admin/migrate-topic-ids")
def admin_migrate_topic_ids() -> dict:
    """Rewrite content_library topic_id columns from legacy → Assessment-style IDs."""
    return migrate_topic_ids()


def _enrich_progress(state: dict) -> ProgressResponse:
    cur = load_curriculum()
    cid = state["current_lesson_id"]
    entry = cur.by_lesson_id(cid)
    grade = state.get("grade")
    if grade is None and entry and entry.grade is not None:
        grade = entry.grade
    grade = int(grade) if grade is not None else None
    pool = cur.lessons_for_grade(grade) if grade is not None else cur.lessons
    idx = cur.index_of(cid)
    nxt = cur.next_lesson_id(cid)
    session = dict(state.get("session_state") or {})
    resume_step = int(session.get("step_index") or 0)
    derived = state.get("derived_profile")
    return ProgressResponse(
        user_id=state["user_id"],
        current_lesson_id=cid,
        completed_lesson_ids=list(state["completed_lesson_ids"]),
        quiz_by_lesson=dict(state["quiz_by_lesson"]),
        updated_at=state["updated_at"],
        current_index=idx,
        lessons_total=len(pool),
        next_lesson_id=nxt,
        resume_step_index=resume_step,
        grade=grade,
        chapter_number=_chapter_number_for_lesson(entry) if entry else None,
        mastery_score=state.get("mastery_score"),
        derived_profile=derived,
        profile_label=profile_display_label(derived) if derived else None,
    )


@app.get(
    "/progress",
    response_model=ProgressResponse,
    tags=["progress"],
    summary="Get learner progress (current lesson, grade, chapter)",
)
def read_progress(user_id: str = Query(..., description="Same as user-management student_id.")) -> ProgressResponse:
    st = get_progress(user_id)
    return _enrich_progress(st)


@app.get(
    "/lesson/current",
    response_model=CurrentLessonResponse,
    tags=["student-lessons"],
    summary="Current lesson being taught (id, grade, chapter + optional material)",
    response_description=(
        "Returns lesson_id, grade, chapter_number, topic_id. "
        "With include_content=true also returns published lesson_text."
    ),
)
def read_current_lesson(
    user_id: str = Query(..., description="Learner id (user-management student_id).", example="demo-1"),
    include_content: bool = Query(
        False,
        description="If true, attach published learning material (lesson_text) for the current chapter.",
    ),
    profile: str | None = Query(
        None,
        description="Knowledge level for library lookup when include_content=true (weak|average|strong). "
        "Defaults to stored learner profile, then average fallback.",
    ),
) -> CurrentLessonResponse:
    st = get_progress(user_id)
    enriched = _enrich_progress(st)
    cur = load_curriculum()
    lesson = cur.by_lesson_id(enriched.current_lesson_id)
    title = None
    topic_id = None
    skill_section_id = None
    chapter_number = enriched.chapter_number
    grade = enriched.grade
    if lesson:
        title = lesson.title
        topic_id = lesson.topic_id
        skill_section_id = lesson.skill_section_id or None
        chapter_number = _chapter_number_for_lesson(lesson)
        if grade is None and lesson.grade is not None:
            grade = lesson.grade

    out = CurrentLessonResponse(
        user_id=enriched.user_id,
        current_lesson_id=enriched.current_lesson_id,
        current_lesson_title=title,
        grade=grade,
        chapter_number=chapter_number,
        topic_id=topic_id,
        skill_section_id=skill_section_id,
        next_lesson_id=enriched.next_lesson_id,
        current_index=enriched.current_index,
        lessons_total=enriched.lessons_total,
        updated_at=enriched.updated_at,
        resume_step_index=enriched.resume_step_index,
    )

    if include_content:
        stored = st.get("derived_profile")
        use_profile = (profile or stored or "average")
        use_profile = normalize_profile(use_profile) if use_profile else "average"
        row, used_profile = find_content_with_fallback(
            lesson_id=enriched.current_lesson_id,
            profile=use_profile,
            event="lesson_start",
        )
        if row:
            out.lesson_text = row.get("lesson_text") or ""
            out.content_status = "ready"
            out.content_id = row.get("content_id")
            out.profile = used_profile or use_profile
            out.message = "Loaded from teacher library."
        else:
            out.lesson_text = ""
            out.content_status = "unavailable"
            out.profile = use_profile
            out.message = (
                "No teacher-approved lesson yet for this chapter. "
                "Ask your teacher to generate and save it first."
            )
    return out


@app.post(
    "/progress",
    response_model=ProgressResponse,
    tags=["progress"],
    summary="Update progress (set_current, set_grade, record_quiz, …)",
)
def write_progress(body: ProgressUpdateRequest) -> ProgressResponse:
    try:
        st = update_progress(
            body.user_id,
            action=body.action,
            lesson_id=body.lesson_id,
            score=body.score,
            step_index=body.step_index,
            grade=body.grade,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _enrich_progress(st)


def _lesson_meta(lesson_id: str | None) -> tuple[str | None, int | None, str | None]:
    """Return (title, grade, topic_id) for a curriculum lesson."""
    if not lesson_id:
        return None, None, None
    cur = load_curriculum()
    le = cur.by_lesson_id(lesson_id)
    if not le:
        return None, None, None
    title = getattr(le, "display_title", None) or le.title
    return title, le.grade, le.topic_id


def _require_llm_keys() -> None:
    if not (
        os.getenv("GROQ_API_KEY")
        or os.getenv("XAI_API_KEY")
        or os.getenv("GROK_API_KEY")
    ):
        raise HTTPException(
            status_code=501,
            detail="Set GROQ_API_KEY (Groq) or XAI_API_KEY / GROK_API_KEY (xAI) in backend/.env.",
        )


def _response_from_library(
    row: dict[str, Any],
    *,
    profile: str | None = None,
    profile_source: str | None = None,
    mastery_score_used: float | None = None,
    message: str | None = None,
) -> LessonResponse:
    topic = row.get("topic_id") or "G6_S1_ORG_CHARS"
    # Normalize legacy rows so Assessment consumers always see primary IDs.
    topic = resolve_chroma_topic_id(topic)
    chunk_ids = list(row.get("chunk_ids") or [])
    lid = row.get("lesson_id")
    title, grade, _topic = _lesson_meta(lid)
    cur = load_curriculum()
    le = cur.by_lesson_id(lid) if lid else None
    return LessonResponse(
        lesson_text=row.get("lesson_text") or "",
        minion_state=row.get("minion_state") or "idle",
        source_chunk_ids=chunk_ids,
        assessment_scope=AssessmentScope(
            topic_id=topic,
            chunk_ids=chunk_ids,
            lesson_id=lid,
        ),
        lesson_id=lid,
        grade=grade if grade is not None else row.get("grade"),
        chapter_number=_chapter_number_for_lesson(le) if le else None,
        lesson_title=row.get("lesson_title") or title,
        retrieval_topic_id=topic,
        profile=profile or row.get("profile"),
        presentation_mode=row.get("presentation_mode"),
        status="ready",
        content_id=row.get("content_id"),
        message=message or "Loaded from teacher library.",
        profile_source=profile_source,
        mastery_score_used=mastery_score_used,
    )


def _public_library_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "content_id": row["content_id"],
        "grade": row.get("grade"),
        "lesson_id": row.get("lesson_id"),
        "lesson_title": row.get("lesson_title"),
        "profile": row.get("profile"),
        "event": row.get("event"),
        "topic_id": row.get("topic_id"),
        "lesson_text": row.get("lesson_text"),
        "minion_state": row.get("minion_state"),
        "presentation_mode": row.get("presentation_mode"),
        "chunk_ids": row.get("chunk_ids") or [],
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@app.post(
    "/analytics/profile",
    response_model=LearnerProfileResponse,
    tags=["analytics"],
    summary="Store learner profile category (weak|average|strong)",
)
def post_analytics_profile(body: LearnerProfileRequest) -> LearnerProfileResponse:
    """
    Learner Profile Analytics → store weak | average | strong (smart alias) directly.
    Student lesson loads use stored profile when use_stored_mastery=true.
    """
    try:
        st = set_learner_profile(
            body.user_id,
            body.profile,
            source=body.source,
            grade=body.grade,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    profile = st.get("derived_profile") or "average"
    log.info(
        "analytics profile user=%r profile=%s source=%r lesson=%r grade=%s",
        body.user_id,
        profile,
        body.source,
        body.lesson_id,
        st.get("grade"),
    )
    return LearnerProfileResponse(
        user_id=body.user_id,
        profile=profile,
        profile_label=profile_display_label(profile),
        grade=st.get("grade"),
        source=body.source,
        updated_at=st.get("mastery_updated_at") or st.get("updated_at") or "",
    )


@app.get(
    "/analytics/profile/{user_id}",
    response_model=LearnerProfileResponse | None,
    tags=["analytics"],
    summary="Get stored learner profile",
)
def get_analytics_profile(user_id: str) -> LearnerProfileResponse | None:
    st = get_progress(user_id)
    if not st.get("derived_profile"):
        return None
    profile = st.get("derived_profile") or "average"
    return LearnerProfileResponse(
        user_id=user_id,
        profile=profile,
        profile_label=profile_display_label(profile),
        grade=st.get("grade"),
        source=st.get("mastery_source") or "stored",
        updated_at=st.get("mastery_updated_at") or st.get("updated_at") or "",
    )


@app.post(
    "/analytics/mastery",
    response_model=MasteryScoreResponse,
    tags=["analytics"],
    summary="Store mastery score (deprecated — prefer /analytics/profile)",
    deprecated=True,
)
def post_analytics_mastery(body: MasteryScoreRequest) -> MasteryScoreResponse:
    """Deprecated: prefer POST /analytics/profile with profile category."""
    try:
        st = set_mastery_score(
            body.user_id,
            body.mastery_score,
            source=body.source,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    profile = st["derived_profile"] or "average"
    log.info(
        "analytics mastery(deprecated) user=%r profile=%s source=%r lesson=%r",
        body.user_id,
        profile,
        body.source,
        body.lesson_id,
    )
    return MasteryScoreResponse(
        user_id=body.user_id,
        mastery_score=None,
        profile=profile,
        profile_label=profile_display_label(profile),
        thresholds=None,
        source=body.source,
        updated_at=st["mastery_updated_at"] or st["updated_at"],
    )


@app.get(
    "/analytics/mastery/{user_id}",
    response_model=MasteryScoreResponse | None,
    tags=["analytics"],
    summary="Get mastery score (deprecated)",
    deprecated=True,
)
def get_analytics_mastery(user_id: str) -> MasteryScoreResponse | None:
    """Deprecated: prefer GET /analytics/profile/{user_id}."""
    st = get_progress(user_id)
    if not st.get("derived_profile"):
        return None
    profile = st.get("derived_profile") or "average"
    return MasteryScoreResponse(
        user_id=user_id,
        mastery_score=st.get("mastery_score"),
        profile=profile,
        profile_label=profile_display_label(profile),
        thresholds=None,
        source=st.get("mastery_source") or "stored",
        updated_at=st.get("mastery_updated_at") or st["updated_at"],
    )


@app.post(
    "/lesson",
    response_model=LessonResponse,
    tags=["student-lessons"],
    summary="Load learning material for a chapter (lesson_id + grade/chapter in response)",
)
def post_lesson(body: LessonRequest) -> LessonResponse:
    """
    Student path: load teacher-approved content for grade chapter + knowledge level.
    Profile comes from request category or stored learner profile (weak/average/strong).
    Falls back to any published profile for that chapter when exact match is missing.
    Does not generate unless STUDENT_ALLOW_GENERATE=true (dev escape hatch).

    Response includes `lesson_id`, `grade`, `chapter_number`, and `lesson_text`.
    """
    event = normalize_event(body.event)
    prog = get_progress(body.user_id)
    stored = prog.get("derived_profile") if body.use_stored_mastery else None

    # Content is chosen from the client's selected level first.
    # Analytics-stored profile only fills in when the client sends no profile.
    if body.profile and str(body.profile).strip():
        profile, profile_source, mastery_used = resolve_lesson_profile(
            explicit_profile=body.profile,
            request_profile=body.profile,
            stored_profile=None,
            event=event,
            prefer_stored=False,
        )
    else:
        profile, profile_source, mastery_used = resolve_lesson_profile(
            explicit_profile=None,
            request_profile=None,
            stored_profile=stored,
            event=event,
            prefer_stored=True,
        )

    log.info(
        "lesson request user=%r lesson_id=%r profile=%r (source=%s) event=%r",
        body.user_id,
        body.lesson_id,
        profile,
        profile_source,
        event,
    )

    if not body.lesson_id:
        raise HTTPException(status_code=400, detail="lesson_id is required")

    title, grade, topic = _lesson_meta(body.lesson_id)
    if title is None and grade is None and topic is None:
        raise HTTPException(status_code=400, detail=f"Unknown lesson_id: {body.lesson_id!r}")

    row, used_profile = find_content_with_fallback(
        lesson_id=body.lesson_id,
        profile=profile,
        event=event,
    )
    if row and used_profile:
        try:
            update_progress(body.user_id, action="set_current", lesson_id=body.lesson_id)
        except Exception as exc_update:
            log.warning("set_current failed user=%r: %s", body.user_id, exc_update)
        msg = "Loaded from teacher library."
        out_source = profile_source
        if used_profile != profile:
            msg = (
                f"Loaded the {profile_display_label(used_profile)} version "
                f"(no {profile_display_label(profile)} text published yet)."
            )
            out_source = "library_fallback"
        return _response_from_library(
            row,
            profile=used_profile,
            profile_source=out_source,
            mastery_score_used=mastery_used,
            message=msg,
        )

    if not STUDENT_ALLOW_GENERATE:
        level = profile_display_label(profile)
        cur = load_curriculum()
        le = cur.by_lesson_id(body.lesson_id)
        return LessonResponse(
            lesson_text="",
            lesson_id=body.lesson_id,
            grade=grade,
            chapter_number=_chapter_number_for_lesson(le) if le else None,
            lesson_title=title,
            profile=profile,
            status="unavailable",
            profile_source=profile_source,
            mastery_score_used=mastery_used,
            message=(
                f"No teacher-approved lesson yet for this chapter (tried {level} and fallbacks). "
                "Ask your teacher to generate and save it first."
            ),
        )

    # Dev-only: generate on the fly when library miss
    _require_llm_keys()
    try:
        text, minion_state, chunk_ids, retrieval_topic, resolved_lesson, eff_profile, pres_mode = (
            build_lesson(
                topic_id=body.topic_id,
                profile=profile,
                event=event,
                lesson_id=body.lesson_id,
            )
        )
    except Exception as exc:
        log.exception("lesson generate fallback failed user=%r", body.user_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if resolved_lesson:
        try:
            update_progress(body.user_id, action="set_current", lesson_id=resolved_lesson)
        except Exception as exc_update:
            log.warning("set_current failed user=%r: %s", body.user_id, exc_update)

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
        profile=eff_profile,
        presentation_mode=pres_mode,
        status="ready",
        message="Generated on the fly (STUDENT_ALLOW_GENERATE).",
        profile_source=profile_source,
        mastery_score_used=mastery_used,
    )


@app.post(
    "/teacher/generate",
    tags=["teacher-library"],
    summary="Generate chapter preview (not saved until publish)",
)
def teacher_generate(body: TeacherGenerateRequest) -> dict[str, Any]:
    """Teacher generates a preview from Chroma + LLM. Not saved until publish."""
    _require_llm_keys()
    profile = normalize_profile(body.profile)
    event = normalize_event(body.event)
    title, grade, topic = _lesson_meta(body.lesson_id)
    if title is None:
        raise HTTPException(status_code=400, detail=f"Unknown lesson_id: {body.lesson_id!r}")

    existing = find_content(lesson_id=body.lesson_id, profile=profile, event=event)
    t0 = time.perf_counter()
    try:
        text, minion_state, chunk_ids, retrieval_topic, resolved_lesson, eff_profile, pres_mode = (
            build_lesson(
                topic_id=topic or "G6_S1_ORG_CHARS",
                profile=profile,
                event=event,
                lesson_id=body.lesson_id,
            )
        )
    except Exception as exc:
        log.exception("teacher generate failed lesson=%r", body.lesson_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    log.info(
        "teacher generate ok lesson=%s profile=%s event=%s chars=%s %.3fs by=%s",
        resolved_lesson,
        eff_profile,
        event,
        len(text or ""),
        time.perf_counter() - t0,
        body.teacher_id,
    )
    return {
        "preview": True,
        "published": False,
        "existing_content_id": existing["content_id"] if existing else None,
        "grade": grade,
        "lesson_id": resolved_lesson or body.lesson_id,
        "lesson_title": title,
        "profile": eff_profile,
        "event": event,
        "topic_id": retrieval_topic,
        "lesson_text": text,
        "minion_state": minion_state,
        "presentation_mode": pres_mode,
        "chunk_ids": chunk_ids,
        "message": (
            "Preview ready. Review and click Approve to store permanently for students."
            + (" This will replace the existing library entry." if existing else "")
        ),
    }


@app.post(
    "/teacher/library",
    tags=["teacher-library"],
    summary="Publish chapter learning material to library",
)
def teacher_publish(body: TeacherPublishRequest) -> dict[str, Any]:
    """Approve / save content permanently (upsert by lesson + profile + event)."""
    profile = normalize_profile(body.profile)
    event = normalize_event(body.event)
    title, grade, topic = _lesson_meta(body.lesson_id)
    if title is None:
        raise HTTPException(status_code=400, detail=f"Unknown lesson_id: {body.lesson_id!r}")
    try:
        row = upsert_content(
            grade=grade,
            lesson_id=body.lesson_id,
            lesson_title=title,
            profile=profile,
            event=event,
            topic_id=resolve_chroma_topic_id(body.topic_id or topic or body.lesson_id),
            lesson_text=body.lesson_text,
            minion_state=body.minion_state,
            presentation_mode=body.presentation_mode,
            chunk_ids=body.chunk_ids,
            teacher_id=body.teacher_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info(
        "teacher published content=%s lesson=%s profile=%s event=%s by=%s",
        row["content_id"],
        body.lesson_id,
        profile,
        event,
        body.teacher_id,
    )
    out = _public_library_item(row)
    # Lesson text is enough for students. Do not auto-build diagram packs here
    # (Pollinations/LLM can hang or fail and block save). Teacher Diagrams tab can build on demand.
    out["ar"] = {
        "built": False,
        "cached": False,
        "scenes": 0,
        "message": "Lesson text saved. Optional diagram pack: Teacher → Diagrams → Regenerate.",
        "skipped": True,
    }
    return out


@app.get(
    "/teacher/library",
    tags=["teacher-library"],
    summary="Browse published chapter content",
)
def teacher_list_library(
    grade: int | None = None,
    profile: str | None = None,
    lesson_id: str | None = None,
    event: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Browse permanent library filtered by grade / level / chapter."""
    prof = normalize_profile(profile) if profile else None
    evt = normalize_event(event) if event else None
    items = list_content(
        grade=grade,
        profile=prof,
        lesson_id=lesson_id,
        event=evt,
        limit=limit,
    )
    return {"count": len(items), "items": [_public_library_item(i) for i in items]}


@app.get("/teacher/library/{content_id}")
def teacher_get_library_item(content_id: str) -> dict[str, Any]:
    row = get_content(content_id)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown content_id")
    return _public_library_item(row)


@app.put("/teacher/library/{content_id}")
def teacher_update_library(content_id: str, body: TeacherUpdateRequest) -> dict[str, Any]:
    try:
        row = update_content(
            content_id,
            lesson_text=body.lesson_text,
            lesson_title=body.lesson_title,
            minion_state=body.minion_state,
            presentation_mode=body.presentation_mode,
            teacher_id=body.teacher_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _public_library_item(row)


@app.delete("/teacher/library/{content_id}")
def teacher_delete_library(content_id: str) -> dict[str, Any]:
    if not delete_content(content_id):
        raise HTTPException(status_code=404, detail="Unknown content_id")
    return {"ok": True, "content_id": content_id}


@app.post("/teacher/library/{content_id}/regenerate")
def teacher_regenerate(content_id: str, teacher_id: str = "teacher-1") -> dict[str, Any]:
    """Regenerate a preview for an existing library row (does not overwrite until publish)."""
    row = get_content(content_id)
    if not row:
        raise HTTPException(status_code=404, detail="Unknown content_id")
    preview = teacher_generate(
        TeacherGenerateRequest(
            lesson_id=row["lesson_id"],
            profile=row["profile"],
            event=row.get("event") or "lesson_start",
            teacher_id=teacher_id,
        )
    )
    preview["existing_content_id"] = content_id
    return preview


@app.get("/teacher/ar-topics")
def teacher_list_ar_topics() -> dict[str, Any]:
    """
    Predefined topic-level AR packs (Plants, Electricity, etc.).
    Teacher can generate, verify, then approve them.
    """
    return list_teacher_topic_ar_packs()


@app.get("/teacher/ar-topic/{topic_key}")
def teacher_get_ar_topic(topic_key: str) -> dict[str, Any]:
    asset = get_topic_asset(topic_key)
    if not asset:
        td = {"topic_key": topic_key, "title": None, "approved": False, "has_payload": False, "payload": None}
        return td
    return {
        "topic_key": asset.get("topic_key"),
        "title": asset.get("title"),
        "approved": bool(asset.get("approved")),
        "has_payload": bool(asset.get("payload")),
        "payload": asset.get("payload"),
        "source_lesson_ids": asset.get("source_lesson_ids") or [],
        "generated_at": asset.get("generated_at"),
        "approved_at": asset.get("approved_at"),
    }


@app.post("/teacher/ar-topic/{topic_key}/generate")
def teacher_generate_ar_topic(topic_key: str) -> dict[str, Any]:
    _require_llm_keys()
    try:
        asset = generate_topic_ar_pack(topic_key=topic_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "topic_key": asset.get("topic_key"),
        "title": asset.get("title"),
        "approved": bool(asset.get("approved")),
        "has_payload": bool(asset.get("payload")),
        "payload": asset.get("payload"),
        "source_lesson_ids": asset.get("source_lesson_ids") or [],
        "generated_at": asset.get("generated_at"),
    }


@app.post("/teacher/ar-topic/{topic_key}/approve")
def teacher_approve_ar_topic(topic_key: str) -> dict[str, Any]:
    try:
        asset = approve_topic_ar_pack(topic_key=topic_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not asset:
        raise HTTPException(status_code=404, detail="No generated topic AR pack found (generate first).")
    return {
        "topic_key": asset.get("topic_key"),
        "title": asset.get("title"),
        "approved": bool(asset.get("approved")),
        "has_payload": bool(asset.get("payload")),
        "payload": asset.get("payload"),
        "approved_at": asset.get("approved_at"),
    }


@app.get("/ar-topic/{topic_key}")
def get_public_ar_topic(topic_key: str) -> dict[str, Any]:
    try:
        payload = get_public_topic_ar_pack(topic_key=topic_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return payload


def _public_ar_item(row: dict[str, Any] | None, *, lesson_id: str) -> dict[str, Any]:
    cur = load_curriculum()
    le = cur.by_lesson_id(lesson_id)
    if not row:
        # Fallback to an approved topic pack (teacher-approved generalized AR).
        if le:
            topic_payload = get_approved_topic_payload_for_topic_id(le.topic_id)
            if topic_payload:
                payload = dict(topic_payload)
                payload["title"] = le.title
                return {
                    "lesson_id": lesson_id,
                    "available": True,
                    "cached": False,
                    "grade": le.grade if le else None,
                    "title": le.title,
                    "model_url": None,
                    "poster_url": None,
                    "caption": "",
                    "payload": payload,
                    "source": "topic",
                    "generated_at": payload.get("built_at"),
                    "source_content_id": None,
                    "updated_at": None,
                    "message": None,
                }

        return {
            "lesson_id": lesson_id,
            "available": False,
            "cached": False,
            "grade": le.grade if le else None,
            "title": (le.title if le else None),
            "model_url": None,
            "poster_url": None,
            "caption": None,
            "payload": None,
            "source": None,
            "message": "AR not generated yet for this lesson.",
        }
    model_url = (row.get("model_url") or "").strip()
    payload = row.get("payload")
    if (not payload or not isinstance(payload, dict)) and le:
        topic_payload = get_approved_topic_payload_for_topic_id(le.topic_id)
        if topic_payload:
            payload = dict(topic_payload)
            payload["title"] = row.get("title") or le.title
    available = bool(payload) or bool(model_url)
    return {
        "lesson_id": row.get("lesson_id") or lesson_id,
        "available": available,
        "cached": bool(payload),
        "grade": row.get("grade") or (le.grade if le else None),
        "title": row.get("title") or (le.title if le else None),
        "model_url": model_url or None,
        "poster_url": (row.get("poster_url") or None) or None,
        "caption": row.get("caption") or "",
        "payload": payload,
        "source": row.get("source"),
        "generated_at": row.get("generated_at"),
        "source_content_id": row.get("source_content_id"),
        "updated_at": row.get("updated_at"),
        "message": None if available else "AR not ready yet.",
    }


@app.get("/lesson/{lesson_id}/ar", tags=["ar"], summary="Get AR pack for a chapter")
def get_lesson_ar(
    lesson_id: str,
    force: bool = False,
    generate: bool = False,
) -> dict[str, Any]:
    """
    Get AR for this chapter (same for all knowledge levels).

    Default generate=false: read stored package only (Unity / students).
    Pass generate=true only from teacher tooling when needed.
    """
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise HTTPException(status_code=404, detail=f"Unknown lesson_id: {lesson_id!r}")
    if not generate and not force:
        return _public_ar_item(get_ar(lesson_id), lesson_id=lesson_id)
    try:
        return get_or_generate_ar(lesson_id, force=force)
    except RuntimeError as exc:
        existing = get_ar(lesson_id)
        if existing and existing.get("payload"):
            return _public_ar_item(existing, lesson_id=lesson_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("AR get_or_generate failed lesson=%s", lesson_id)
        raise HTTPException(status_code=500, detail=f"AR generation failed: {exc}") from exc


@app.get("/teacher/ar")
def teacher_list_ar(grade: int | None = None) -> dict[str, Any]:
    items = list_ar(grade=grade)
    return {
        "count": len(items),
        "items": [_public_ar_item(i, lesson_id=i["lesson_id"]) for i in items],
    }


@app.post("/teacher/ar/{lesson_id}/regenerate")
def teacher_regenerate_ar(lesson_id: str) -> dict[str, Any]:
    """Force regenerate AR scenes from the published lesson library text."""
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise HTTPException(status_code=404, detail=f"Unknown lesson_id: {lesson_id!r}")
    _require_llm_keys()
    clear_generated_payload(lesson_id)
    try:
        return ensure_ar_package(lesson_id, force=True)
    except Exception as exc:
        log.exception("teacher AR regenerate failed lesson=%s", lesson_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/teacher/ar/{lesson_id}")
def teacher_upsert_ar(lesson_id: str, body: TeacherARUpsertRequest) -> dict[str, Any]:
    cur = load_curriculum()
    le = cur.by_lesson_id(lesson_id)
    if not le:
        raise HTTPException(status_code=404, detail=f"Unknown lesson_id: {lesson_id!r}")
    try:
        row = upsert_ar(
            lesson_id,
            grade=body.grade if body.grade is not None else le.grade,
            title=body.title or le.title,
            model_url=body.model_url,
            poster_url=body.poster_url,
            caption=body.caption,
            source="teacher",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("teacher AR upsert lesson=%s", lesson_id)
    return _public_ar_item(row, lesson_id=lesson_id)


@app.delete("/teacher/ar/{lesson_id}")
def teacher_delete_ar(lesson_id: str) -> dict[str, Any]:
    if not delete_ar(lesson_id):
        raise HTTPException(status_code=404, detail="No AR asset for this lesson")
    return {"ok": True, "lesson_id": lesson_id}


@app.get(
    "/lesson/{lesson_id}/media",
    tags=["student-lessons"],
    summary="Chapter media (YouTube + summary) for a lesson_id",
)
def get_lesson_media(lesson_id: str, preview: bool = False) -> dict[str, Any]:
    """
    Static chapter media: YouTube + summary infographic.
    Students (preview=false) only get approved summary. Teachers can pass preview=true.
    """
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise HTTPException(status_code=404, detail=f"Unknown lesson_id: {lesson_id!r}")
    return public_media(lesson_id, student=not preview)


@app.get(
    "/teacher/media/{lesson_id}",
    tags=["teacher-media"],
    summary="Get chapter YouTube + summary (teacher view)",
)
def teacher_get_media(lesson_id: str) -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise HTTPException(status_code=404, detail=f"Unknown lesson_id: {lesson_id!r}")
    return public_media(lesson_id, student=False)


@app.put(
    "/teacher/media/{lesson_id}/youtube",
    tags=["teacher-media"],
    summary="Set YouTube URL for a chapter",
)
def teacher_set_youtube(lesson_id: str, body: TeacherLessonMediaRequest) -> dict[str, Any]:
    try:
        return set_lesson_youtube(
            lesson_id,
            youtube_url=body.youtube_url,
            teacher_id=body.teacher_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put(
    "/teacher/media/{lesson_id}/videos",
    tags=["teacher-media"],
    summary="Replace the ordered video library for a chapter",
)
def teacher_set_videos(
    lesson_id: str,
    body: TeacherLessonVideosRequest,
) -> dict[str, Any]:
    try:
        return set_lesson_videos(
            lesson_id,
            videos=[item.model_dump() for item in body.videos],
            teacher_id=body.teacher_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/teacher/media/{lesson_id}/summary/generate",
    tags=["teacher-media"],
    summary="Generate chapter summary infographic",
)
def teacher_generate_summary(
    lesson_id: str,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    _require_llm_keys()
    try:
        return generate_lesson_summary(lesson_id, teacher_id=teacher_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("summary generate failed lesson=%s", lesson_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(
    "/teacher/media/{lesson_id}/summary/approve",
    tags=["teacher-media"],
    summary="Approve chapter summary for students",
)
def teacher_approve_summary(
    lesson_id: str,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    try:
        return approve_lesson_summary(lesson_id, teacher_id=teacher_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/teacher/media/{lesson_id}/images",
    tags=["teacher-media"],
    summary="Attach an image URL to the chapter gallery",
)
def teacher_add_image(lesson_id: str, body: TeacherLessonImageRequest) -> dict[str, Any]:
    try:
        return attach_lesson_image(
            lesson_id,
            image_url=body.image_url,
            caption=body.caption,
            teacher_id=body.teacher_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post(
    "/teacher/media/{lesson_id}/images/upload",
    tags=["teacher-media"],
    summary="Upload an image file to the chapter gallery",
)
async def teacher_upload_image(
    lesson_id: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
    teacher_id: str = Form("teacher-1"),
) -> dict[str, Any]:
    try:
        content = await file.read()
        return upload_lesson_image_file(
            lesson_id,
            filename=file.filename or "image.jpg",
            content=content,
            caption=caption,
            teacher_id=teacher_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete(
    "/teacher/media/{lesson_id}/images/{image_id}",
    tags=["teacher-media"],
    summary="Remove an image from the chapter gallery",
)
def teacher_delete_image(lesson_id: str, image_id: str) -> dict[str, Any]:
    try:
        return remove_lesson_image(lesson_id, image_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
