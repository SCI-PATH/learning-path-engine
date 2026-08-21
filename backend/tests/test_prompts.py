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
    assert normalize_profile("smart") == "strong"
    assert normalize_profile("weak") == "weak"
    assert normalize_profile("") == "average"


def test_retrieval_query_is_chapter_focused_not_profile_noise():
    q = build_retrieval_query(
        topic_id="G6_S7_MAG_POLES",
        lesson_title="Magnets",
        event="lesson_start",
    )
    assert "G6_S7_MAG_POLES" in q or "g6 s7 mag poles" in q.lower()
    assert "Magnets" in q
    assert "weak" not in q
    assert "strong" not in q


def test_system_message_includes_grounding_and_profile():
    sys = build_system_message(profile="strong", event="lesson_start")
    assert "PASSAGES" in GROUNDING_RULES or "passages" in sys.lower()
    assert "Go deeper" in sys or "strong" in sys.lower()


def test_strong_output_format_includes_go_deeper():
    fmt = build_output_format("strong", "lesson_start")
    assert "Go deeper" in fmt


def test_presentation_mode_by_profile():
    assert presentation_mode_for_profile("weak") == "stepped"
    assert presentation_mode_for_profile("average") == "stepped"
    assert presentation_mode_for_profile("smart") == "stepped"


def test_user_message_lists_passages():
    user = build_user_message(
        topic_id="G6_S7_MAG_POLES",
        lesson_title="Magnets",
        passages=["North pole attracts south pole."],
        profile="average",
        event="lesson_start",
    )
    assert "PASSAGE 1" in user
    assert "North pole" in user
    assert "OUTPUT FORMAT" in user
    assert "LEARNER_LEVEL: average" in user


def test_profiles_are_distinct_in_system_prompts():
    weak = build_system_message(profile="weak", event="lesson_start")
    avg = build_system_message(profile="average", event="lesson_start")
    smart = build_system_message(profile="smart", event="lesson_start")
    assert "12–15" in weak or "12-15" in weak
    assert "only for smart" in avg
    assert "LEARNER LEVEL: strong" in smart or "strong / smart" in smart
    weak_fmt = build_output_format("weak", "lesson_start")
    avg_fmt = build_output_format("average", "lesson_start")
    strong_fmt = build_output_format("strong", "lesson_start")
    assert "OUTPUT FORMAT (weak)" in weak_fmt
    assert "OUTPUT FORMAT (average)" in avg_fmt
    assert "exactly: Go deeper" in strong_fmt
    assert "exactly: Go deeper" not in avg_fmt
    assert "exactly: Go deeper" not in weak_fmt
