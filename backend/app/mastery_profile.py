"""
Learner knowledge level: weak | average | strong (UI: Smart).

Learner Profile Analytics sends the category directly — we do not compute levels from a score.
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
    p = normalize_profile(profile or "average")
    return {"weak": "weak", "average": "average", "strong": "smart"}.get(p, p)


def resolve_lesson_profile(
    *,
    explicit_profile: str | None = None,
    request_profile: str | None = None,
    stored_profile: str | None = None,
    event: str | None = None,
    prefer_stored: bool = True,
    # Deprecated score args kept so older callers do not break; ignored.
    mastery_score: float | None = None,
    stored_mastery_score: float | None = None,
    prefer_mastery: bool = True,
) -> tuple[Profile, ProfileSource, float | None]:
    """
    Pick the profile used to load library lesson content.

    Priority:
      1) game_failed_return → always weak
      2) stored profile from Learner Analytics (if prefer_stored)
      3) request / explicit profile from client
      4) average (default)

    Scores are not used. Third return value is always None (legacy field).
    """
    del mastery_score, stored_mastery_score  # unused
    prefer_stored = prefer_stored or prefer_mastery

    evt = (event or "lesson_start").strip().lower()
    if evt in ("wrong_answer", "game_failed_return"):
        return "weak", "explicit", None

    if prefer_stored and stored_profile:
        return normalize_profile(stored_profile), "stored_profile", None

    # request_profile from analytics passthrough, else client explicit
    if request_profile:
        return normalize_profile(request_profile), "request_profile", None
    if explicit_profile:
        return normalize_profile(explicit_profile), "explicit", None
    return "average", "default", None
