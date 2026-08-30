"""Golden-style checks for prompt contracts (no LLM calls)."""

from app.prompts import (
    GROUNDING_RULES,
    build_output_format,
    build_retrieval_query,
    build_system_message,
    build_user_message,
    normalize_profile,
    presentation_mode_for_profile,
)


def test_normalize_profile_aliases():
    assert normalize_profile("smart") == "advanced"
    assert normalize_profile("weak") == "basic"
    assert normalize_profile("basic") == "basic"
    assert normalize_profile("") == "intermediate"
    assert normalize_profile("average") == "intermediate"
    assert normalize_profile("strong") == "advanced"


def test_retrieval_query_is_chapter_focused_not_profile_noise():
    q = build_retrieval_query(
        topic_id="G6_S7_MAG_POLES",
        lesson_title="Magnets",
        event="lesson_start",
    )
    assert "G6_S7_MAG_POLES" in q or "g6 s7 mag poles" in q.lower()
    assert "Magnets" in q
    assert "basic" not in q
    assert "advanced" not in q


def test_system_message_includes_grounding_and_profile():
    sys = build_system_message(profile="advanced", event="lesson_start")
    assert "PASSAGES" in GROUNDING_RULES or "passages" in sys.lower()
    assert "Go deeper" in sys or "advanced" in sys.lower()


def test_advanced_output_format_includes_go_deeper():
    fmt = build_output_format("advanced", "lesson_start")
    assert "Go deeper" in fmt


def test_presentation_mode_by_profile():
    assert presentation_mode_for_profile("basic") == "stepped"
    assert presentation_mode_for_profile("intermediate") == "stepped"
    assert presentation_mode_for_profile("smart") == "stepped"


def test_user_message_lists_passages():
    user = build_user_message(
        topic_id="G6_S7_MAG_POLES",
        lesson_title="Magnets",
        passages=["North pole attracts south pole."],
        profile="intermediate",
        event="lesson_start",
    )
    assert "SOURCE 1" in user
    assert "North pole" in user
    assert "OUTPUT FORMAT" in user
    assert "LEARNER_LEVEL: intermediate" in user


def test_profiles_are_distinct_in_system_prompts():
    basic = build_system_message(profile="basic", event="lesson_start")
    mid = build_system_message(profile="intermediate", event="lesson_start")
    adv = build_system_message(profile="advanced", event="lesson_start")
    assert "12–15" in basic or "12-15" in basic
    assert "only for advanced" in mid
    assert "LEARNER LEVEL: advanced" in adv
    basic_fmt = build_output_format("basic", "lesson_start")
    mid_fmt = build_output_format("intermediate", "lesson_start")
    adv_fmt = build_output_format("advanced", "lesson_start")
    assert "OUTPUT FORMAT (basic)" in basic_fmt
    assert "OUTPUT FORMAT (intermediate)" in mid_fmt
    assert "exactly: Go deeper" in adv_fmt
    assert "exactly: Go deeper" not in mid_fmt
    assert "exactly: Go deeper" not in basic_fmt


def test_grade_9_advanced_enrichment_included():
    sys = build_system_message(profile="advanced", event="lesson_start", grade=9)
    assert "GRADE 9 ADVANCED ENRICHMENT" in sys
    assert "O-Levels" in sys

    sys_g6 = build_system_message(profile="advanced", event="lesson_start", grade=6)
    assert "GRADE 9 ADVANCED ENRICHMENT" not in sys_g6


def test_grade_9_intermediate_enrichment_included():
    sys = build_system_message(profile="intermediate", event="lesson_start", grade=9)
    assert "GRADE 9 INTERMEDIATE" in sys
    assert "O-LEVEL ENTRY" in sys
    assert "Go deeper" not in sys.split("GRADE 9 INTERMEDIATE")[1][:200]

    sys_g7 = build_system_message(profile="intermediate", event="lesson_start", grade=7)
    assert "GRADE 9 INTERMEDIATE" not in sys_g7


def test_grade_9_basic_enrichment_included():
    sys = build_system_message(profile="basic", event="lesson_start", grade=9)
    assert "GRADE 9 BASIC" in sys
    assert "O-LEVEL ENTRY" in sys

    sys_g8 = build_system_message(profile="basic", event="lesson_start", grade=8)
    assert "GRADE 9 BASIC" not in sys_g8


def test_grade_9_user_message_includes_grade_note():
    user_msg_g9 = build_user_message(
        topic_id="G9_S1_XYZ",
        lesson_title="Title",
        passages=["Text"],
        profile="intermediate",
        event="lesson_start",
        grade=9,
    )
    assert "GRADE: 9" in user_msg_g9
    assert "O-Level entry" in user_msg_g9

    user_msg_none = build_user_message(
        topic_id="G9_S1_XYZ",
        lesson_title="Title",
        passages=["Text"],
        profile="advanced",
        event="lesson_start",
        grade=None,
    )
    assert "GRADE:" not in user_msg_none
    assert "O-Level entry" not in user_msg_none
