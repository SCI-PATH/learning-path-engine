"""Tests for learner profile categories (no score calculator)."""

from app.mastery_profile import profile_display_label, resolve_lesson_profile


def test_profile_display_label_smart():
    assert profile_display_label("strong") == "smart"
    assert profile_display_label("smart") == "smart"
    assert profile_display_label("weak") == "weak"


def test_resolve_prefers_stored_profile():
    prof, src, score = resolve_lesson_profile(
        explicit_profile="weak",
        request_profile="weak",
        stored_profile="strong",
        event="lesson_start",
        prefer_stored=True,
    )
    assert prof == "strong"
    assert src == "stored_profile"
    assert score is None


def test_resolve_uses_request_when_no_stored():
    prof, src, _ = resolve_lesson_profile(
        explicit_profile=None,
        request_profile="smart",
        stored_profile=None,
        event="lesson_start",
    )
    assert prof == "strong"
    assert src == "request_profile"


def test_game_failed_forces_weak():
    prof, src, _ = resolve_lesson_profile(
        explicit_profile="strong",
        request_profile="strong",
        stored_profile="strong",
        event="game_failed_return",
    )
    assert prof == "weak"
    assert src == "explicit"
