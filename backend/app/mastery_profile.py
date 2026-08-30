"""
Learner knowledge level: basic | intermediate | advanced.

Learner Profile Analytics sends the category directly — we do not compute levels from a score.
Legacy aliases (weak/average/strong/smart) are normalized via prompts.normalize_profile.
"""

from __future__ import annotations

from typing import Literal

from app.prompts import Profile, normalize_profile

ProfileSource = Literal[
    "request_profile",
    "stored_profile",
    "explicit",
    "default",
]


def profile_display_label(profile: Profile | str | None) -> str:
    p = normalize_profile(profile or "intermediate")
    return {
        "basic": "Basic",
        "intermediate": "Intermediate",
        "advanced": "Advanced",
    }.get(p, p)


def resolve_lesson_profile(
    *,
    explicit_profile: str | None = None,
    request_profile: str | None = None,
    stored_profile: str | None = None,
    event: str | None = None,
    # Deprecated — ignored; kept so older callers do not break.
    prefer_stored: bool = True,
    mastery_score: float | None = None,
    stored_mastery_score: float | None = None,
    prefer_mastery: bool = True,
) -> tuple[Profile, ProfileSource, float | None]:
    """
    Pick the profile used to load library lesson content.

    Priority:
      1) game_failed_return / wrong_answer → basic (remediation)
      2) request / explicit profile from client (fresh IAE or analytics resolution)
      3) stored profile on LPE progress (last saved level when analytics is unreachable)
      4) intermediate (default)

    Scores are not used. Third return value is always None (legacy field).
    """
    del mastery_score, stored_mastery_score, prefer_stored, prefer_mastery

    evt = (event or "lesson_start").strip().lower()
    if evt in ("wrong_answer", "game_failed_return"):
        return "basic", "explicit", None

    chosen = request_profile or explicit_profile
    if chosen and str(chosen).strip():
        return normalize_profile(chosen), "request_profile", None

    if stored_profile:
        return normalize_profile(stored_profile), "stored_profile", None

    return "intermediate", "default", None
