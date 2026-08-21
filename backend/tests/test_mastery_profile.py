from app.mastery_profile import profile_display_label, resolve_lesson_profile


def test_profile_display_label():
    assert profile_display_label("advanced") == "Advanced"
    assert profile_display_label("smart") == "Advanced"
    assert profile_display_label("strong") == "Advanced"
    assert profile_display_label("basic") == "Basic"
    assert profile_display_label("weak") == "Basic"
    assert profile_display_label("intermediate") == "Intermediate"
    assert profile_display_label("average") == "Intermediate"


def test_prefer_stored_over_request():
    prof, src, _ = resolve_lesson_profile(
        explicit_profile="basic",
        request_profile="basic",
        stored_profile="advanced",
        prefer_stored=True,
    )
    assert prof == "advanced"
    assert src == "stored_profile"


def test_request_profile_when_no_stored():
    prof, src, _ = resolve_lesson_profile(
        request_profile="smart",
        prefer_stored=True,
    )
    assert prof == "advanced"
    assert src == "request_profile"


def test_game_failed_forces_basic():
    prof, src, _ = resolve_lesson_profile(
        explicit_profile="advanced",
        request_profile="advanced",
        stored_profile="advanced",
        event="game_failed_return",
    )
    assert prof == "basic"
    assert src == "explicit"
