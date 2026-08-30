"""
Structured prompt contracts for lesson generation (Phase A — prompt engineering).

Profiles: basic | intermediate | advanced
(Legacy aliases: weak→basic, average→intermediate, strong/smart→advanced)
Events: lesson_start | game_failed_return | wrap_up_success
"""

from __future__ import annotations

from typing import Literal

Profile = Literal["basic", "intermediate", "advanced"]

# Retrieval breadth by learner level (advanced learners get more textbook context).
PROFILE_TOP_K: dict[Profile, int] = {
    "basic": 10,
    "intermediate": 12,
    "advanced": 14,
}

PROFILE_TEMPERATURE: dict[Profile, float] = {
    "basic": 0.25,
    "intermediate": 0.3,
    "advanced": 0.35,
}

PROFILE_MAX_TOKENS: dict[Profile, int] = {
    "basic": 2000,
    "intermediate": 2600,
    "advanced": 3200,
}

# How the frontend should chunk lesson text for display (all use short slides).
PresentationMode = Literal["stepped", "sectioned", "continuous"]

PROFILE_PRESENTATION: dict[Profile, PresentationMode] = {
    "basic": "stepped",
    "intermediate": "stepped",
    "advanced": "stepped",
}

SYSTEM_ROLE = (
    "You are King Arthur, a friendly science tutor for school students in Sri Lanka. "
    "You teach only from the supplied textbook passages. "
    "You never invent facts, activities, page numbers, or content from outside the passages."
)

GROUNDING_RULES = """
STRICT GROUNDING (must follow):
1. Every factual claim must be supported by the PASSAGES below.
2. If a detail is missing from the passages, write exactly:
   "The textbook excerpt here does not explain that."
3. Use ONLY lesson/theory content from the passages (definitions, explanations, concepts, processes, named examples).
4. KEEP scientific names, organism names, plant/animal names, chemical names, and technical terms
   EXACTLY as written in the passages (e.g. Amoeba, Paramecium, yeast, Chlamydomonas, Mucor,
   mango, cashew when given). Never replace a real name with vague words like "a plant" or "an organism".
5. Cover the chapter thoroughly in order: main ideas → definitions → examples/names from the passages.
   Do not skip named examples just to shorten the lesson.
6. IGNORE completely: activities, experiments, "let us do", materials lists, exercises, review questions,
   true/false, fill-in-the-blanks, match-the-following, projects, worksheets, figure captions alone,
   teacher notes.
7. Do NOT invent quiz questions, homework, classroom activities, or Recap/Summary sections.
8. Do NOT say you are an AI, or repeat "according to the passages".
9. Do NOT include passage/source numbers or citations like (Passage 3) or [Source 1].
10. Do NOT include a chapter title or heading line at the start — begin directly with lesson sentences.
11. Write polished, professional classroom prose: grammatically complete sentences, natural flow, no awkward fragments.
12. Stay on this chapter only.
13. Output ONLY the final lesson text students will read. Never show your thinking process, planning steps,
    analysis of the prompt, constraint lists, or summaries of the sources — start directly with teaching sentences.
""".strip()

# Concrete, non-overlapping pedagogy per learner level.
PROFILE_PEDAGOGY: dict[Profile, str] = {
    "basic": """
LEARNER LEVEL: basic (needs scaffolding)
GOAL: Make the chapter understandable in tiny steps without losing real science names.

SENTENCE RULES:
- One idea per sentence. Mostly under 12–15 words.
- Put a blank line after EVERY sentence (each sentence = one reading slide).
- No long compound sentences. No dense paragraphs.

TEACHING RULES:
- Explain in this order for each idea: (1) simple meaning, (2) the real term/name, (3) one tiny example from passages.
- Pattern for new terms: "X means … . X is called … ." Keep the real name.
- Use everyday words for explanations (home, school, garden, body) but NEVER drop textbook names.
- Include at most ONE short analogy for the whole lesson.
- Cover the chapter’s main ideas in order, but keep language very gentle.
- Forbidden: Recap, Summary, Key takeaways, quiz questions, praise fluff ("Great job learning!").
""".strip(),
    "intermediate": """
LEARNER LEVEL: intermediate (typical classroom learner)
GOAL: Clear, complete chapter teaching at normal school depth — not baby-talk, not advanced enrichment.

SENTENCE RULES:
- Clear complete sentences (about 12–20 words is fine).
- Blank line after every sentence (slide reading).
- No giant multi-sentence paragraphs.

TEACHING RULES:
- Teach the chapter in logical order: introduction → definitions → how it works → named examples from passages.
- Introduce each technical term once with a plain meaning, then use the term normally.
- Keep ALL scientific/technical names from the passages.
- Include ONE short real-life link only if it appears in (or is directly implied by) the passages.
- Depth: full chapter coverage of main sections; do not oversimplify away important terms.
- Do NOT add a "Go deeper" section (that is only for advanced learners).
- Forbidden: Recap, Summary, Key takeaways, quiz questions, baby-talk, filler praise.
""".strip(),
    "advanced": """
LEARNER LEVEL: advanced (ready for more challenge)
GOAL: Precise, richer teaching plus an extra synthesis section — still only from passages.

SENTENCE RULES:
- Exact scientific wording where the passages use it.
- Complete sentences with blank lines between them (slide reading).
- Prefer precise terms over soft paraphrases.

TEACHING RULES:
- Cover the chapter thoroughly: definitions, mechanisms, comparisons, and named examples.
- Connect ideas inside the chapter (cause → effect, compare/contrast, "this leads to…").
- Keep ALL names/terms exactly as in the passages.
- After the main lesson, add a heading line that is exactly: Go deeper
- Under "Go deeper": 4–8 short sentences on applications, why it matters, and links between ideas —
  still ONLY facts supported by the passages. No outside research.
- Forbidden: Recap, Summary, Key takeaways, quiz questions, generic praise, invented facts.
""".strip(),
}

GRADE_9_ADVANCED_PEDAGOGY_ADDENDUM = """
GRADE 9 ADVANCED ENRICHMENT:
- Because this is Grade 9 (preparing for senior level/O-Levels), elevate the academic tone to be formal, precise, and rigorous.
- Emphasize underlying scientific principles and mechanisms (how and why things happen, not just what they are).
- Under the "Go deeper" section, focus heavily on multi-step cause-and-effect chains and the broader scientific significance of these concepts.
""".strip()

EVENT_ADDENDUM: dict[str, str] = {
    "lesson_start": (
        "SITUATION: First teaching pass for this chapter. Introduce the topic clearly from the passages."
    ),
    "game_failed_return": (
        "SITUATION: The student struggled in a practice game and returned for remediation.\n"
        "- Re-teach the hardest idea in simpler words.\n"
        "- Name ONE likely misconception and correct it using the passages only.\n"
        "- Do NOT add a recap or summary section."
    ),
    "wrap_up_success": (
        "SITUATION: The student passed the practice game — this is a short wrap-up, not a full re-teach.\n"
        "- Give 3–4 short closing sentences (not a labeled recap/summary heading).\n"
        "- End with one motivating sentence about being ready for the next chapter.\n"
        "- Do NOT repeat the entire lesson."
    ),
}


def normalize_profile(raw: str) -> Profile:
    """Canonical: basic | intermediate | advanced. Accepts legacy weak/average/strong/smart."""
    v = (raw or "intermediate").strip().lower()
    if v in ("basic", "weak", "struggling", "beginner", "low"):
        return "basic"
    if v in ("advanced", "strong", "high", "smart"):
        return "advanced"
    return "intermediate"


def normalize_event(raw: str | None) -> str:
    evt = (raw or "lesson_start").strip().lower()
    if evt in ("wrong_answer", "game_failed_return"):
        return "game_failed_return"
    if evt in ("lesson_complete", "quiz_passed", "wrap_up_success"):
        return "wrap_up_success"
    return "lesson_start"


def retrieval_top_k(profile: str) -> int:
    return PROFILE_TOP_K[normalize_profile(profile)]


def presentation_mode_for_profile(profile: str) -> PresentationMode:
    return PROFILE_PRESENTATION[normalize_profile(profile)]


def build_retrieval_query(
    *,
    topic_id: str,
    lesson_title: str | None,
    event: str | None,
) -> str:
    """
    Semantic search query — chapter-focused (profile is NOT mixed in; it dilutes retrieval).
    """
    chapter = topic_id.replace("_", " ")
    title = f' Chapter title: "{lesson_title}".' if lesson_title else ""
    evt = normalize_event(event)
    focus = {
        "lesson_start": "core lesson theory concepts definitions explanations only",
        "game_failed_return": "key lesson concepts definitions theory explanations only",
        "wrap_up_success": "main lesson ideas key points theory summary only",
    }[evt]
    return (
        f"Grade science textbook LESSON CONTENT only (not activities, not exercises, not questions): "
        f"{chapter}.{title} Retrieve: {focus}."
    )


def build_enrichment_retrieval_query(*, topic_id: str, lesson_title: str | None) -> str:
    """Second pass for advanced learners — connections and applications within chapter theory."""
    chapter = topic_id.replace("_", " ")
    title = f' "{lesson_title}"' if lesson_title else ""
    return (
        f"Grade science LESSON theory only {chapter}{title}: applications in daily life, "
        f"relationships between concepts, cause and effect, deeper explanations. "
        f"Exclude activities exercises questions worksheets."
    )


def build_system_message(*, profile: str, event: str | None, grade: int | None = None) -> str:
    prof = normalize_profile(profile)
    evt = normalize_event(event)
    
    pedagogy = PROFILE_PEDAGOGY[prof]
    if prof == "advanced" and grade == 9:
        pedagogy = pedagogy + "\n\n" + GRADE_9_ADVANCED_PEDAGOGY_ADDENDUM
        
    parts = [
        SYSTEM_ROLE,
        GROUNDING_RULES,
        pedagogy,
        EVENT_ADDENDUM.get(evt, EVENT_ADDENDUM["lesson_start"]),
    ]
    return "\n\n".join(parts)


def _output_format_basic(evt: str) -> str:
    if evt == "wrap_up_success":
        return (
            "OUTPUT FORMAT (basic):\n"
            "Start directly with lesson sentences — NO title or heading line.\n"
            "Then: 3–4 very short sentences, each on its own line block (blank line between)\n"
            "No passage/source citations. No Recap / Summary / Go deeper"
        )
    if evt == "game_failed_return":
        return (
            "OUTPUT FORMAT (basic):\n"
            "Start directly with lesson sentences — NO title or heading line.\n"
            "Then: gentle re-teach in very short sentences with blank lines between\n"
            "No passage/source citations. No Recap / Summary / Go deeper"
        )
    return (
        "OUTPUT FORMAT (basic):\n"
        "Start directly with lesson sentences — NO title or heading line.\n"
        "ONLY short sentences with a blank line after each sentence\n"
        "For each new term: meaning first, then the real name from the sources\n"
        "Keep every scientific/technical name\n"
        "No thinking process, no analysis steps, no source summaries, no planning notes\n"
        "No passage/source citations. Do NOT write Recap, Summary, Key takeaways, or Go deeper"
    )


def _output_format_intermediate(evt: str) -> str:
    if evt == "wrap_up_success":
        return (
            "OUTPUT FORMAT (intermediate):\n"
            "Start directly with lesson sentences — NO title or heading line.\n"
            "Then: 3–4 clear closing sentences with blank lines between\n"
            "No passage/source citations. No Recap / Summary / Go deeper"
        )
    return (
        "OUTPUT FORMAT (intermediate):\n"
        "Start directly with lesson sentences — NO title or heading line.\n"
        "Complete chapter teaching as clear, professional sentences with blank lines between sentences\n"
        "Order: introduce topic → define terms → explain process/ideas → include named examples\n"
        "Keep ALL names/terms from the sources\n"
        "No passage/source citations. Do NOT write Recap, Summary, Key takeaways, or Go deeper"
    )


def _output_format_advanced(evt: str) -> str:
    if evt == "wrap_up_success":
        return (
            "OUTPUT FORMAT (advanced):\n"
            "Start directly with lesson sentences — NO title or heading line.\n"
            "Then: 4 precise closing sentences with blank lines between\n"
            "No passage/source citations. No Recap / Summary"
        )
    return (
        "OUTPUT FORMAT (advanced):\n"
        "Start directly with lesson sentences — NO title or heading line.\n"
        "Thorough chapter teaching as precise, professional sentences with blank lines between sentences\n"
        "Include definitions, mechanisms, comparisons, and named examples from sources\n"
        "Then one line that is exactly: Go deeper\n"
        "Then: 4–8 short enrichment sentences (applications / why it matters / links) FROM sources only\n"
        "No passage/source citations. Do NOT write Recap, Summary, Key takeaways, or quiz questions"
    )


def build_output_format(profile: Profile, event: str) -> str:
    if profile == "basic":
        return _output_format_basic(event)
    if profile == "advanced":
        return _output_format_advanced(event)
    return _output_format_intermediate(event)


def build_user_message(
    *,
    topic_id: str,
    lesson_title: str | None,
    passages: list[str],
    profile: str,
    event: str | None,
    grade: int | None = None,
) -> str:
    prof = normalize_profile(profile)
    evt = normalize_event(event)
    joined = "\n\n---\n\n".join(
        f"[SOURCE {i + 1}]\n{chunk}" for i, chunk in enumerate(passages)
    )
    title_line = f'CHAPTER: "{lesson_title}"\n' if lesson_title else ""
    grade_line = f"GRADE: {grade}\n" if grade is not None else ""
    output_fmt = build_output_format(prof, evt)

    return (
        f"TOPIC_ID: {topic_id}\n"
        f"{grade_line}"
        f"{title_line}"
        f"LEARNER_LEVEL: {prof}\n\n"
        f"{output_fmt}\n\n"
        f"TEXTBOOK EXCERPTS (sole source of truth — do not cite excerpt numbers in your answer):\n{joined}\n\n"
        f"TASK:\n"
        f"- Write the lesson for LEARNER_LEVEL={prof} only (follow that level's rules; do not mix levels).\n"
        f"- Use ONLY theory/lesson facts from the excerpts.\n"
        f"- Keep every scientific name and technical term that appears.\n"
        f"- Cover the chapter thoroughly (not a tiny summary).\n"
        f"- One sentence per blank-line block for slide reading.\n"
        f"- No title line, no passage/source citations, no activities, questions, exercises, Recap, or Summary.\n"
        f"- No thinking process, planning steps, constraint lists, or source-by-source summaries.\n"
        f"- Professional, grammatically correct sentences.\n"
        f"- Follow OUTPUT FORMAT and GROUNDING rules exactly."
    )
