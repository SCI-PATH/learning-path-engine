"""
Generate and store cartoon AR images from lesson scene prompts.

Flow per scene:
1) Build a cartoon image prompt from lesson-derived scene (label + facts + visual)
2) Call image API (Pollinations by default — no extra key)
3) Save under backend/data/ar_media/{lesson_id}/{scene_id}.jpg
4) Attach image_url on the scene payload for the frontend

No Wikipedia / Openverse photo search.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.chroma_setup import BACKEND_ROOT

log = logging.getLogger("learning_path.ar_images")

MEDIA_ROOT = BACKEND_ROOT / "data" / "ar_media"
USER_AGENT = "SCI-PATH-LearningEngine/1.0 (educational cartoon AR)"

# Pollinations is free for demos; override with AR_IMAGE_BASE if needed
POLLINATIONS_BASE = os.getenv(
    "AR_IMAGE_BASE",
    "https://image.pollinations.ai/prompt",
).rstrip("/")

STYLE_SUFFIX = (
    "cute educational cartoon illustration for school science textbook, "
    "bright clean colors, simple shapes, friendly kids style, "
    "no text, no watermark, no photo realism, white soft background"
)

VISUAL_HINTS = {
    "monocot": "cartoon monocot plant with parallel leaf veins and fibrous roots",
    "dicot": "cartoon dicot plant with net-like leaf veins and a thick taproot",
    "plant": "cartoon green plant in a pot",
    "leaf": "cartoon leaf showing clear vein pattern",
    "flower": "cartoon flower plant",
    "root": "cartoon plant roots underground in soil",
    "magnet": "cartoon red horseshoe magnet with N and S poles",
    "animal": "cute cartoon animal for science class",
    "cell": "cartoon plant cell with labeled nucleus style but no text",
    "water": "cartoon water droplet and blue waves",
    "earth": "cartoon planet Earth",
    "machine": "cartoon simple machine gears or lever",
    "electric": "cartoon lightning bolt and simple circuit",
    "space": "cartoon sun and planets",
    "generic": "cute cartoon science classroom object",
}


def _safe_id(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "scene").strip())[:48]
    return s or "scene"


def media_dir(lesson_id: str) -> Path:
    d = MEDIA_ROOT / _safe_id(lesson_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def public_image_path(lesson_id: str, scene_id: str, ext: str = "jpg") -> str:
    return f"/ar-media/{_safe_id(lesson_id)}/{_safe_id(scene_id)}.{ext}"


def build_image_prompt(scene: dict[str, Any], *, lesson_title: str = "") -> str:
    """Cartoon prompt grounded in the lesson scene."""
    explicit = str(scene.get("image_prompt") or scene.get("photo_query") or "").strip()
    label = str(scene.get("label") or "").strip()
    visual = str(scene.get("visual") or "generic").strip().lower()
    facts = scene.get("facts") or []
    fact_bit = ""
    if isinstance(facts, list) and facts:
        fact_bit = "; ".join(str(f).strip() for f in facts[:3] if str(f).strip())

    hint = VISUAL_HINTS.get(visual, VISUAL_HINTS["generic"])
    core = explicit or f"{label}: {hint}"
    if fact_bit:
        core = f"{core}. Key ideas: {fact_bit}"
    if lesson_title:
        core = f"{core}. Lesson: {lesson_title}"

    prompt = f"{core}. {STYLE_SUFFIX}"
    # Keep URL length sane
    return re.sub(r"\s+", " ", prompt).strip()[:480]


def _download_bytes(url: str, *, timeout: float = 120.0) -> bytes | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        log.warning("image generate download failed err=%s url=%s", exc, url[:120])
        return None
    if len(data) < 1500:
        return None
    if "svg" in ctype and len(data) < 5000:
        return None
    return data


def generate_cartoon_image(prompt: str, dest: Path) -> bool:
    """
    Generate an image from prompt and write to dest.
    Tries Pollinations with a few URL variants (Flux sometimes 500s).
    """
    seed = int(hashlib.md5(prompt.encode("utf-8")).hexdigest()[:8], 16) % 1_000_000
    short = re.sub(r"\s+", " ", prompt).strip()[:220]
    attempts = [
        f"{POLLINATIONS_BASE}/{urllib.parse.quote(prompt, safe='')}?width=1024&height=768&nologo=true&seed={seed}",
        f"{POLLINATIONS_BASE}/{urllib.parse.quote(short, safe='')}?width=768&height=768&nologo=true&seed={seed}",
        f"{POLLINATIONS_BASE}/{urllib.parse.quote(short + ', simple clean education diagram', safe='')}?width=768&height=768&nologo=true",
    ]
    log.info("generating image → %s", dest.name)
    data = None
    for i, url in enumerate(attempts):
        data = _download_bytes(url, timeout=100.0)
        if data:
            break
        if i < len(attempts) - 1:
            time.sleep(1.2)
    if not data:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest.is_file() and dest.stat().st_size > 1500


def resolve_and_store_scene_image(
    *,
    lesson_id: str,
    scene: dict[str, Any],
    lesson_title: str = "",
    force: bool = False,
) -> dict[str, Any]:
    scene = dict(scene)
    sid = _safe_id(str(scene.get("id") or scene.get("label") or "scene"))
    dest = media_dir(lesson_id) / f"{sid}.jpg"
    public = public_image_path(lesson_id, sid, "jpg")
    prompt = build_image_prompt(scene, lesson_title=lesson_title)
    scene["image_prompt"] = prompt

    # Reuse file only if it was generated for this pipeline and not force
    if (
        not force
        and dest.is_file()
        and dest.stat().st_size > 2000
        and (scene.get("image_source") == "generated" or scene.get("image_url") == public)
    ):
        scene["image_url"] = public
        scene.setdefault("image_credit", "AI cartoon from lesson")
        scene.setdefault("image_source", "generated")
        return scene

    if generate_cartoon_image(prompt, dest):
        scene["image_url"] = public
        scene["image_credit"] = "AI cartoon from lesson"
        scene["image_source"] = "generated"
        log.info(
            "stored cartoon AR lesson=%s scene=%s bytes=%s",
            lesson_id,
            sid,
            dest.stat().st_size,
        )
        return scene

    log.warning("cartoon generate failed lesson=%s scene=%s", lesson_id, sid)
    scene.pop("image_url", None)
    return scene


def attach_images_to_payload(
    lesson_id: str,
    payload: dict[str, Any],
    *,
    force: bool = False,
    lesson_title: str = "",
) -> dict[str, Any]:
    title = lesson_title or str(payload.get("title") or "")
    scenes = payload.get("scenes") or []
    updated: list[dict[str, Any]] = []
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        updated.append(
            resolve_and_store_scene_image(
                lesson_id=lesson_id,
                scene=scene,
                lesson_title=title,
                force=force,
            )
        )
        if i < len(scenes) - 1:
            time.sleep(0.6)
    out = dict(payload)
    out["scenes"] = updated
    return out


def payload_missing_images(payload: dict[str, Any] | None) -> bool:
    if not payload or not isinstance(payload.get("scenes"), list):
        return True
    for s in payload["scenes"]:
        if not isinstance(s, dict):
            return True
        # Prefer generated cartoons; older Wikipedia pulls count as missing
        if (s.get("image_source") or "") != "generated":
            return True
        url = (s.get("image_url") or "").strip()
        if not url:
            return True
        if url.startswith("/ar-media/"):
            rel = url[len("/ar-media/") :]
            path = MEDIA_ROOT / rel
            if not path.is_file() or path.stat().st_size < 2000:
                return True
    return False
