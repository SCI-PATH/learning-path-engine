"""
Generate and manage topic-level image-based AR packs.

Teacher flow (v1):
1) Generate AR pack from published lesson texts (verified content library).
2) Teacher verifies via image gallery (and optionally marker AR preview).
3) Teacher approves the pack.

Unity/student consumption:
- Lesson AR endpoints may fall back to an approved topic pack.
- Unity fetches only approved topic assets via the API.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.ar_images import attach_images_to_payload
from app.ar_topic_store import approve_topic_asset, get_topic_asset, list_topic_assets, upsert_generated_topic_asset
from app.content_library import find_content
from app.curriculum import load_curriculum

log = logging.getLogger("learning_path.ar_topic")


TOPIC_PACKS: dict[str, dict[str, Any]] = {
    "AR_PLANTS": {
        "title": "Plants",
        "curriculum_topic_ids": [
            "G7_S1_PLA_DIVER",
            "G8_S3_PLA_PARTS",
            "G8_S11_PHO_PROCESS",
            "G8_S12_LIF_CYCLES",
            "G9_S7_PLA_GROWTH",
        ],
        "source_lesson_ids": ["g7_sci_01", "g8_sci_03", "g8_sci_11", "g8_sci_12", "g9_sci_07"],
    },
    "AR_HUMAN_BODY": {
        "title": "Human Body",
        "curriculum_topic_ids": [
            "G8_S9_SYS_HUMAN",
            "G9_S6_SYS_CIRCUL",
            "G9_S2_SEN_EYE",
        ],
        "source_lesson_ids": ["g8_sci_09", "g9_sci_06", "g9_sci_02"],
    },
    "AR_ELECTRICITY": {
        "title": "Electricity & Circuits",
        "curriculum_topic_ids": [
            "G6_S8_ELE_CIRCUITS",
            "G7_S2_STA_CHARGES",
            "G7_S3_ELE_SOURCES",
            "G8_S10_ELE_CIRCUIT",
            "G8_S7_ELE_MEASURE",
            "G9_S10_ELE_LYSIS",
        ],
        "source_lesson_ids": ["g6_sci_08", "g7_sci_02", "g7_sci_03", "g8_sci_10", "g8_sci_07", "g9_sci_10"],
    },
    "AR_LIGHT_OPTICS": {
        "title": "Light & Optics",
        "curriculum_topic_ids": [
            "G6_S5_LIG_VISION",
            "G7_S9_LIG_SHADOWS",
            "G7_S10_MIC_LIGHT",
            "G9_S14_LIG_REFRAC",
        ],
        "source_lesson_ids": ["g6_sci_05", "g7_sci_09", "g7_sci_10", "g9_sci_14"],
    },
    "AR_SOUND": {
        "title": "Sound",
        "curriculum_topic_ids": [
            "G6_S6_SOU_HEARING",
            "G7_S11_SOU_PROPAG",
            "G8_S5_SOU_WAVES",
        ],
        "source_lesson_ids": ["g6_sci_06", "g7_sci_11", "g8_sci_05"],
    },
    "AR_MATTER": {
        "title": "Matter",
        "curriculum_topic_ids": [
            "G6_S2_MAT_STATES",
            "G8_S4_MAT_ELEMENTS",
            "G8_S8_CHA_PHYSICAL",
            "G9_S3_NAT_ATOMS",
            "G9_S11_MAT_DENSITY",
        ],
        "source_lesson_ids": ["g6_sci_02", "g8_sci_04", "g8_sci_08", "g9_sci_03", "g9_sci_11"],
    },
}


_TOPIC_KEY_BY_CURRICULUM_TOPIC_ID: dict[str, str] = {}
for _topic_key, _def in TOPIC_PACKS.items():
    for _tid in _def.get("curriculum_topic_ids") or []:
        _TOPIC_KEY_BY_CURRICULUM_TOPIC_ID[str(_tid)] = _topic_key


def get_topic_def(topic_key: str) -> dict[str, Any] | None:
    return TOPIC_PACKS.get(topic_key)


def topic_key_for_topic_id(topic_id: str) -> str | None:
    return _TOPIC_KEY_BY_CURRICULUM_TOPIC_ID.get((topic_id or "").strip())


def topic_key_for_lesson_id(lesson_id: str) -> str | None:
    cur = load_curriculum()
    le = cur.by_lesson_id(lesson_id)
    if not le:
        return None
    return topic_key_for_topic_id(le.topic_id)


def get_approved_topic_payload_for_topic_id(topic_id: str) -> dict[str, Any] | None:
    topic_key = topic_key_for_topic_id(topic_id)
    if not topic_key:
        return None
    asset = get_topic_asset(topic_key)
    if not asset or not asset.get("approved") or not asset.get("payload"):
        return None
    return asset["payload"]


def get_approved_topic_payload_for_lesson_id(lesson_id: str) -> dict[str, Any] | None:
    topic_key = topic_key_for_lesson_id(lesson_id)
    if not topic_key:
        return None
    asset = get_topic_asset(topic_key)
    if not asset or not asset.get("approved") or not asset.get("payload"):
        return None
    return asset["payload"]


def _pick_source_content_texts(*, source_lesson_ids: list[str], profile_order: list[str]) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    used_lesson_ids: list[str] = []
    for lid in source_lesson_ids:
        row = None
        for prof in profile_order:
            row = find_content(lesson_id=lid, profile=prof, event="lesson_start")
            if row and (row.get("lesson_text") or "").strip():
                break
        if not row or not (row.get("lesson_text") or "").strip():
            continue
        texts.append(row["lesson_text"])
        used_lesson_ids.append(lid)
    return texts, used_lesson_ids


def generate_topic_ar_pack(*, topic_key: str) -> dict[str, Any]:
    """
    Generate and store a *non-approved* topic pack (teacher verification step).
    """
    td = get_topic_def(topic_key)
    if not td:
        raise ValueError(f"Unknown topic_key: {topic_key}")

    # Prefer average/weak/strong available in the verified library.
    texts, used_lids = _pick_source_content_texts(
        source_lesson_ids=list(td.get("source_lesson_ids") or []),
        profile_order=["intermediate", "basic", "advanced"],
    )
    if not texts:
        raise ValueError(
            f"No published lesson text found for topic '{topic_key}'. "
            "Ask the teacher to generate + publish the source chapters first."
        )

    combined = "\n\n".join(texts)
    # Keep the prompt small enough for provider limits.
    combined = combined[:12000] + ("\n…" if len(combined) > 12000 else "")

    from app.ar_service import generate_ar_payload_from_text

    payload = generate_ar_payload_from_text(
        lesson_title=str(td.get("title") or topic_key),
        lesson_text=combined,
    )

    payload = attach_images_to_payload(
        lesson_id=topic_key,
        payload=payload,
        force=True,
        lesson_title=str(td.get("title") or topic_key),
    )
    payload["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["topic_key"] = topic_key
    payload["topic_title"] = str(td.get("title") or topic_key)

    asset = upsert_generated_topic_asset(
        topic_key=topic_key,
        title=str(td.get("title") or topic_key),
        payload=payload,
        source_lesson_ids=used_lids,
        approved=False,
    )
    return asset


def approve_topic_ar_pack(*, topic_key: str) -> dict[str, Any] | None:
    return approve_topic_asset(topic_key)


def list_teacher_topic_ar_packs() -> dict[str, Any]:
    """
    Return all predefined topic packs with current generated/approved status.
    """
    items: list[dict[str, Any]] = []
    for topic_key, td in TOPIC_PACKS.items():
        asset = get_topic_asset(topic_key)
        items.append(
            {
                "topic_key": topic_key,
                "title": td.get("title") or topic_key,
                "approved": bool(asset.get("approved")) if asset else False,
                "has_payload": bool(asset.get("payload")) if asset else False,
                "payload": asset.get("payload") if asset else None,
                "source_lesson_ids": asset.get("source_lesson_ids") if asset else [],
                "generated_at": asset.get("generated_at") if asset else None,
                "approved_at": asset.get("approved_at") if asset else None,
            }
        )
    return {"items": items, "count": len(items)}


def get_public_topic_ar_pack(*, topic_key: str) -> dict[str, Any]:
    """
    Public/student/Unity endpoint. Only returns approved packs.
    """
    asset = get_topic_asset(topic_key)
    if not asset or not asset.get("approved") or not asset.get("payload"):
        raise ValueError("Topic AR pack not approved yet.")
    return asset["payload"]

