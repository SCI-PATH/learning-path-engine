"""
Shared filters so Chroma ingest + retrieval keep lesson/theory text only
(no activities, exercises, quizzes, summaries, or assignment scaffolding).

Important: do NOT reject whole lesson paragraphs just because they briefly mention
"Activity 1.1" or use the word "activities" (e.g. microbial activities).
"""

from __future__ import annotations

import re

# Section headers that start non-lesson blocks (drop from that line until theory resumes).
SECTION_DROP_START = re.compile(
    r"^(?:"
    r"activity\s*\d*(?:\.\d+)*\s*$|"
    r"activity\s+\d+(?:\.\d+)*\b|"
    r"assignment\s*\d*(?:\.\d+)*\s*$|"
    r"assignment\s+\d+(?:\.\d+)*\b|"
    r"exercise\s*\d*(?:\.\d+)*\s*$|"
    r"exercise\s+\d+(?:\.\d+)*\b|"
    r"exercises?\s*$|"
    r"project(?:\s+work)?\s*$|"
    r"worksheet\b|"
    r"homework\b|"
    r"review questions?\b|"
    r"questions?(?:\s+and\s+answers?)?\s*$|"
    r"check your understanding\b|"
    r"test yourself\b|"
    r"self[- ]check\b|"
    r"fill in the blanks?\b|"
    r"true or false\b|"
    r"match the following\b|"
    r"multiple choice\b|"
    r"choose the correct\b|"
    r"answer the following\b|"
    r"group work\b|"
    r"classroom task\b|"
    r"practical(?:\s+work)?\s*$|"
    r"you will need\b|"
    r"materials? needed\b|"
    r"for your extra knowledge\b|"
    r"for extra knowledge\b|"
    r"teacher'?s? notes?\b|"
    r"summary\s*$|"
    r"things to remember\b|"
    r"key points to remember\b|"
    r"glossary\b"
    r")",
    re.I,
)

SECTION_RESUME = re.compile(
    r"^(?:"
    r"chapter\s+\d+|"
    r"unit\s+\d+|"
    r"\d+\.\d+\s+\S|"
    r"introduction\b|"
    r"what is\b|"
    r"types of\b|"
    r"properties of\b|"
    r"importance of\b|"
    r"structure of\b|"
    r"functions? of\b|"
    r"process of\b|"
    r"definition\b|"
    r"effects of\b|"
    r"causes of\b"
    r")",
    re.I,
)

# Whole-chunk reject only when these dominate (materials lists / quiz scaffolding).
HARD_NON_LESSON = (
    "you will need",
    "materials needed",
    "fill in the blank",
    "fill in the blanks",
    "true or false",
    "match the following",
    "multiple choice",
    "choose the correct answer",
    "answer the following",
    "mark true",
    "tick the correct",
    "circle the correct",
    "complete the table",
    "draw and label",
    "classroom task",
    "group work",
    "worksheet",
)

# Sentences containing these are scrubbed, but the rest of the chunk can stay.
SCRUB_SENTENCE_CUES = (
    "let us do",
    "let's try",
    "try this",
    "do this activity",
    "do activity",
    "do assignment",
    "carry out the activity",
    "design and carry out",
    "for your extra knowledge",
    "for extra knowledge",
    "teacher note",
    "teacher's note",
)

ACTIVITY_REF = re.compile(
    r"\b(?:do\s+)?(?:activity|assignment|exercise)\s+\d+(?:\.\d+)*\b",
    re.I,
)
NUMBERED_ACTIVITY_HEADER = re.compile(
    r"^(?:activity|assignment|exercise)\s+\d+(?:\.\d+)*\b",
    re.I,
)

QUESTION_HEAVY = re.compile(r"\?")
NUMBERED_Q = re.compile(r"(?:^|\n)\s*(?:\d+[\).]|[a-d][\).])\s+\S.{8,}\?", re.I | re.M)
MCQ_OPTION = re.compile(r"(?:^|\n)\s*[(\[]?[a-dA-D][)\].:]\s+\S+", re.M)


def _looks_like_theory_resume(ln: str) -> bool:
    if SECTION_RESUME.match(ln):
        return True
    if len(ln) < 70:
        return False
    if not re.match(r"^[A-ZÀ-ÖØ-Þ]", ln):
        return False
    if SECTION_DROP_START.match(ln) or NUMBERED_ACTIVITY_HEADER.match(ln):
        return False
    low = ln.lower()
    if any(cue in low for cue in HARD_NON_LESSON):
        return False
    if low.startswith(("method", "materials", "observation", "conclusion", "step ")):
        return False
    if ln.rstrip().endswith("?") and len(ln) < 120:
        return False
    return True


def _is_single_prompt_line(ln: str) -> bool:
    """Short line that only points at an activity/assignment — drop line, keep page."""
    low = ln.lower().strip()
    if len(ln) > 160:
        return False
    if ACTIVITY_REF.search(ln) and (
        low.startswith(("let us", "let's", "do ", "try ", "carry out", "design"))
        or "let us do" in low
        or "do assignment" in low
        or "do activity" in low
    ):
        return True
    return False


def strip_non_lesson_sections(raw: str) -> str:
    """Drop activity / exercise / summary blocks; keep lesson prose."""
    lines = [ln.strip() for ln in (raw or "").splitlines()]
    kept: list[str] = []
    dropping = False
    for ln in lines:
        if not ln:
            if not dropping:
                kept.append("")
            continue
        low = ln.lower()
        if re.fullmatch(r"\d{1,4}", low):
            continue
        if "science |" in low:
            continue
        if re.match(r"^fig\.?\s*\d+", low):
            continue

        # One-off "Let us do Activity 1.1 ..." lines — skip line only
        if _is_single_prompt_line(ln):
            continue

        if SECTION_DROP_START.match(ln) or NUMBERED_ACTIVITY_HEADER.match(ln):
            dropping = True
            continue
        if dropping and _looks_like_theory_resume(ln):
            dropping = False
        if dropping:
            continue
        kept.append(ln)
    return "\n".join(kept)


def clean_page_text(raw: str) -> str:
    """Remove headers/footers/captions and non-lesson sections before chunking."""
    return strip_non_lesson_sections(raw)


def scrub_chunk_for_lesson(text: str) -> str:
    """Remove leftover activity/question sentences; keep lesson explanations."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"\bFig(?:ure)?\.?\s*\d+(?:\.\d+)?\b.*?(?=\s[A-Z]|$)", "", t, flags=re.I)
    t = re.sub(r"\bScience\s*\|[A-Za-z\s]+\b", "", t, flags=re.I)

    parts = re.split(r"(?<=[.!?])\s+", t)
    kept: list[str] = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if any(cue in low for cue in SCRUB_SENTENCE_CUES):
            continue
        if any(cue in low for cue in HARD_NON_LESSON):
            continue
        if NUMBERED_ACTIVITY_HEADER.match(p) or SECTION_DROP_START.match(p):
            continue
        # Drop short "Do Activity 1.2 ..." / "Do Assignment 1.1 ..." sentences
        if ACTIVITY_REF.search(p) and len(p) < 140:
            continue
        if p.endswith("?") and (
            low.startswith(("what ", "why ", "how ", "which ", "who ", "when ", "where "))
            or "discuss" in low
            or ("explain" in low and len(p) < 120)
        ):
            if len(p) < 100:
                continue
        if re.match(r"^\s*(?:\d+[\).]|[a-d][\).])\s+", p, re.I) and "?" in p:
            continue
        kept.append(p)

    out = " ".join(kept)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def is_non_lesson_chunk(text: str) -> bool:
    """
    True if chunk is mostly activity/exercise/quiz scaffolding.
    Brief mentions of activities inside theory are scrubbed, not rejected.
    """
    scrubbed = scrub_chunk_for_lesson(text)
    t = scrubbed if scrubbed else (text or "").strip()
    if not t:
        return True
    low = t.lower()
    words = re.findall(r"[a-zA-Z]{2,}", low)
    if len(words) < 35:
        return True

    hard_hits = sum(1 for cue in HARD_NON_LESSON if cue in low)
    if hard_hits >= 1 and len(words) < 80:
        return True
    if hard_hits >= 2:
        return True

    # Mostly a materials / method block
    if "you will need" in low or low.startswith("method"):
        return True

    q_count = len(QUESTION_HEAVY.findall(t))
    if q_count >= 4:
        return True
    if q_count >= 3 and ("discuss" in low or "answer the following" in low):
        return True
    if NUMBERED_Q.search(t) and q_count >= 2:
        return True
    if len(MCQ_OPTION.findall(t)) >= 3:
        return True

    first = t.split("\n", 1)[0].strip()
    if NUMBERED_ACTIVITY_HEADER.match(first) or SECTION_DROP_START.match(first):
        return True

    # After scrubbing, almost nothing left vs original → was scaffolding
    if scrubbed and len(scrubbed) < 0.35 * max(len(text or ""), 1) and len(scrubbed) < 200:
        return True

    return False


def content_type_for_chunk(text: str) -> str:
    """Tag chunk as theory | non_theory for Chroma metadata."""
    return "non_theory" if is_non_lesson_chunk(text) else "theory"


def is_usable_lesson_passage(text: str) -> bool:
    t = scrub_chunk_for_lesson(text)
    if len(t) < 120:
        return False
    if is_non_lesson_chunk(t):
        return False
    return True


_PASSAGE_REF = re.compile(
    r"\s*(?:\([Pp]assage\s+\d+\)|\[[Pp]assage\s+\d+\]|\([Ss]ource\s+\d+\))",
    re.I,
)

# Chain-of-thought / planning blocks some reasoning models leak into visible output.
_COT_LINE = re.compile(
    r"(?i)^(?:"
    r"here'?s a thinking process:?|"
    r"\*?\s*\*\*analyze user input\*?\*?:?|"
    r"\*?\s*\*\*deconstruct excerpts.*|"
    r"\*?\s*\*\*structure the lesson.*|"
    r"let'?s draft step[- ]by[- ]step.*|"
    r"\*?\s*\*\*constraint\s*\d+|"
    r"\*?\s*\*\*role:\*\*|"
    r"\*?\s*\*\*contextual data:\*\*|"
    r"\d+\.\s+\*\*(?:analyze|deconstruct|structure)\b"
    r")"
)
_COT_SOURCE_SUMMARY = re.compile(r"^\*\s*Source\s+\d+\s*:", re.I)
_SECTION_ONLY = re.compile(r"^\*[^*\n]+\*$")


def strip_llm_reasoning(text: str) -> str:
    """Drop model planning / chain-of-thought; keep student-facing lesson prose."""
    t = (text or "").strip()
    if not t:
        return ""

    if not (_COT_LINE.search(t) or _COT_SOURCE_SUMMARY.search(t)):
        return t.strip()

    paragraphs = re.split(r"\n\s*\n", t)
    kept: list[str] = []
    started = False

    for para in paragraphs:
        p = para.strip()
        if not p:
            if started:
                kept.append("")
            continue

        first_line = p.split("\n", 1)[0].strip()
        if not started:
            if _COT_LINE.match(first_line) or _COT_SOURCE_SUMMARY.match(first_line):
                continue
            if re.match(r"(?i)^\*?\s*\*\*(?:constraint|role|contextual)\b", first_line):
                continue
            if "**Constraint" in p and not re.search(r"[.!?]\s*$", p.split("\n")[-1].strip()):
                continue
            if _SECTION_ONLY.match(first_line) and "\n" not in p.strip():
                continue
            started = True

        if started and _COT_LINE.match(first_line):
            continue
        if started and _COT_SOURCE_SUMMARY.match(first_line):
            continue
        if started and _SECTION_ONLY.match(first_line) and "\n" not in p.strip():
            continue
        kept.append(p)

    out = "\n\n".join(kept).strip()
    return out if out else t.strip()


def polish_generated_lesson(text: str, *, lesson_title: str | None = None) -> str:
    """Remove titles, passage citations, chain-of-thought, and tidy slide spacing in LLM output."""
    t = strip_llm_reasoning(text)
    if not t:
        return ""

    t = _PASSAGE_REF.sub("", t)
    t = re.sub(r"\s+according to the (?:passages|sources)\.", ".", t, flags=re.I)

    lines = t.split("\n")
    out: list[str] = []
    dropped_title = False
    title_norm = (lesson_title or "").strip().lower()

    for line in lines:
        s = line.strip()
        if not s:
            out.append("")
            continue
        if not dropped_title and title_norm and s.lower().rstrip(".") == title_norm.rstrip("."):
            dropped_title = True
            continue
        if not dropped_title and not out and len(s.split()) <= 8 and not s.endswith((".", "!", "?")):
            # Short standalone first line → likely a title the model added anyway.
            dropped_title = True
            continue
        out.append(line.rstrip())

    polished = "\n".join(out)
    polished = re.sub(r"\n{3,}", "\n\n", polished)
    return polished.strip()
