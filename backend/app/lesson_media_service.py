"""
Generate lesson summary mindmap + infographic image from published library text.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.ar_images import MEDIA_ROOT, STYLE_SUFFIX, generate_cartoon_image, media_dir, public_image_path
from app.content_library import find_content, list_content
from app.curriculum import load_curriculum
from app.lesson_media_store import (
    add_lesson_image,
    approve_summary,
    delete_lesson_image,
    get_media,
    list_lesson_images,
    save_summary_draft,
    upsert_links,
    upsert_videos,
    upsert_youtube,
)
from app.lesson_service import _llm_client_and_model

log = logging.getLogger("learning_path.lesson_media")

SUMMARY_SYSTEM = """You create a simple educational mindmap summary for a science lesson.
Return ONLY valid JSON (no markdown fences) with this shape:
{
  "title": "short summary title",
  "headline": "one sentence overview",
  "branches": [
    {
      "id": "snake_case",
      "label": "Main idea (3-6 words)",
      "points": ["short point", "short point"]
    }
  ],
  "image_prompt": "cute educational cartoon infographic of ... (no text in image)"
}

Rules:
- Ground everything ONLY in the lesson text. Do not invent unrelated facts.
- 4 to 6 branches. Each label under 8 words. Each point under 14 words.
- image_prompt under 40 words, cartoon textbook style, white soft background, no readable text.
"""


def youtube_embed_url(url: str) -> str | None:
    """Convert watch / youtu.be / shorts URL to embed URL."""
    raw = (url or "").strip()
    if not raw:
        return None
    if "youtube.com/embed/" in raw:
        return raw.split("?")[0]
    try:
        u = urlparse(raw)
    except Exception:
        return None
    host = (u.netloc or "").lower().replace("www.", "")
    vid = ""
    if host in ("youtu.be", "www.youtu.be"):
        vid = (u.path or "").strip("/").split("/")[0]
    elif "youtube.com" in host or "youtube-nocookie.com" in host:
        if "/embed/" in (u.path or ""):
            vid = (u.path or "").split("/embed/")[-1].split("/")[0]
        elif "/shorts/" in (u.path or ""):
            vid = (u.path or "").split("/shorts/")[-1].split("/")[0]
        else:
            vid = (parse_qs(u.query).get("v") or [""])[0]
    if not vid or not re.match(r"^[\w-]{6,}$", vid):
        return None
    return f"https://www.youtube.com/embed/{vid}"


def _pick_library_row(lesson_id: str) -> dict[str, Any] | None:
    for profile in ("intermediate", "advanced", "basic"):
        row = find_content(lesson_id=lesson_id, profile=profile, event="lesson_start")
        if row and (row.get("lesson_text") or "").strip():
            return row
    rows = list_content(lesson_id=lesson_id, limit=20)
    for row in rows:
        if (row.get("lesson_text") or "").strip():
            return row
    return None


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise ValueError("Model did not return JSON for summary")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("Summary JSON root must be an object")
    return data


def _normalize_summary(data: dict[str, Any], *, fallback_title: str) -> dict[str, Any]:
    branches_in = data.get("branches") or data.get("nodes") or []
    if not isinstance(branches_in, list):
        branches_in = []
    branches: list[dict[str, Any]] = []
    for i, b in enumerate(branches_in[:6]):
        if not isinstance(b, dict):
            continue
        label = str(b.get("label") or b.get("title") or f"Idea {i + 1}").strip()[:80]
        points_raw = b.get("points") or b.get("facts") or []
        points: list[str] = []
        if isinstance(points_raw, list):
            for p in points_raw:
                t = str(p).strip()
                if t:
                    points.append(t[:120])
                if len(points) >= 3:
                    break
        if not label:
            continue
        sid = str(b.get("id") or label.lower().replace(" ", "_"))[:40]
        branches.append({"id": sid, "label": label, "points": points or [label]})
    if not branches:
        raise ValueError("Summary had no usable branches")
    return {
        "title": str(data.get("title") or fallback_title).strip()[:80],
        "headline": str(data.get("headline") or data.get("summary") or "").strip()[:240],
        "branches": branches,
        "image_prompt": str(data.get("image_prompt") or "").strip()[:300],
    }


def public_media(lesson_id: str, *, student: bool = False) -> dict[str, Any]:
    """
    Public package for a lesson.
    Students only get approved summary image/structure; teachers see drafts too.
    """
    cur = load_curriculum()
    le = cur.by_lesson_id(lesson_id)
    row = get_media(lesson_id) or {}
    videos_in = row.get("videos") if isinstance(row.get("videos"), list) else []
    videos = [
        {
            "title": str(item.get("title") or f"Video {i + 1}"),
            "url": str(item.get("url") or ""),
            "embed_url": youtube_embed_url(str(item.get("url") or "")),
        }
        for i, item in enumerate(videos_in)
        if isinstance(item, dict) and youtube_embed_url(str(item.get("url") or ""))
    ]
    yt = (row.get("youtube_url") or "").strip()
    embed = videos[0]["embed_url"] if videos else youtube_embed_url(yt)
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else None
    approved = bool(row.get("summary_approved"))
    if student and (not approved or not summary):
        summary_out = None
        image_out = None
    else:
        summary_out = summary
        image_out = row.get("summary_image_url")

    youtube_urls = [v["url"] for v in videos if v.get("url")]
    youtube_embed_urls = [v.get("embed_url") for v in videos]
    gallery = list_lesson_images(lesson_id)
    if student:
        gallery = [img for img in gallery if img.get("image_type") == "gallery"]

    links_in = row.get("links") if isinstance(row.get("links"), list) else []
    additional_materials = [
        {
            "title": str(item.get("title") or f"Resource {i + 1}"),
            "url": str(item.get("url") or ""),
        }
        for i, item in enumerate(links_in)
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]

    return {
        "lesson_id": lesson_id,
        "title": (le.title if le else None) or lesson_id,
        "grade": le.grade if le else None,
        "youtube_url": yt or None,
        "youtube_embed_url": embed,
        "youtube_urls": youtube_urls,
        "youtube_embed_urls": youtube_embed_urls,
        "videos": videos,
        "summary": summary_out,
        "summary_image_url": image_out,
        "gallery_images": gallery,
        "additional_materials": additional_materials,
        "summary_approved": approved,
        "summary_generated_at": row.get("summary_generated_at"),
        "summary_approved_at": row.get("summary_approved_at"),
        "updated_at": row.get("updated_at"),
        "message": None
        if (videos or yt or (summary_out and (approved or not student)))
        else "No video or approved summary yet for this chapter.",
    }


def set_lesson_youtube(
    lesson_id: str,
    *,
    youtube_url: str,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise KeyError(f"Unknown lesson_id: {lesson_id}")
    upsert_youtube(lesson_id, youtube_url=youtube_url, teacher_id=teacher_id)
    return public_media(lesson_id, student=False)


def set_lesson_videos(
    lesson_id: str,
    *,
    videos: list[dict[str, str]],
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise KeyError(f"Unknown lesson_id: {lesson_id}")
    for item in videos:
        url = str(item.get("url") or "").strip()
        if url and not youtube_embed_url(url):
            raise ValueError(f"Invalid YouTube URL: {url}")
    upsert_videos(lesson_id, videos=videos, teacher_id=teacher_id)
    return public_media(lesson_id, student=False)


def set_lesson_links(
    lesson_id: str,
    *,
    links: list[dict[str, str]],
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise KeyError(f"Unknown lesson_id: {lesson_id}")
    upsert_links(lesson_id, links=links, teacher_id=teacher_id)
    return public_media(lesson_id, student=False)


def generate_lesson_summary(
    lesson_id: str,
    *,
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    cur = load_curriculum()
    le = cur.by_lesson_id(lesson_id)
    if not le:
        raise KeyError(f"Unknown lesson_id: {lesson_id}")

    lib = _pick_library_row(lesson_id)
    if not lib:
        raise ValueError(
            "No published lesson text yet. Generate and save the chapter text first."
        )

    title = lib.get("lesson_title") or le.title
    text = (lib.get("lesson_text") or "").strip()
    if len(text) > 7000:
        text = text[:7000] + "\n…"

    client, model = _llm_client_and_model()
    user = (
        f"Lesson title: {title}\n\n"
        f"Lesson text:\n{text}\n\n"
        "Build a mindmap-style summary and image_prompt for an infographic."
    )
    resp = client.chat.completions.create(
        model=model,
        temperature=0.3,
        max_tokens=1200,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    choice = resp.choices[0].message.content
    if not choice:
        raise RuntimeError("Empty summary completion from model")

    payload = _normalize_summary(_extract_json(choice), fallback_title=title)
    # Prefer a clean infographic of the mindmap theme (optional — mindmap SVG still works without it)
    prompt = (
        payload.get("image_prompt")
        or f"clean educational mindmap infographic poster about {title}, soft colors, no text"
    )
    full_prompt = f"{prompt}. educational infographic style, flat illustration, soft white background, no readable text"
    dest = media_dir(lesson_id) / "summary_infographic.jpg"
    image_url = None
    if generate_cartoon_image(full_prompt, dest):
        image_url = public_image_path(lesson_id, "summary_infographic", "jpg")
        log.info("summary image stored lesson=%s", lesson_id)
    else:
        log.warning(
            "summary image failed lesson=%s — draft still saved; SVG mindmap covers student UI",
            lesson_id,
        )

    payload["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_summary_draft(
        lesson_id,
        summary=payload,
        summary_image_url=image_url,
        teacher_id=teacher_id,
    )
    return public_media(lesson_id, student=False)


def approve_lesson_summary(lesson_id: str, *, teacher_id: str = "teacher-1") -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise KeyError(f"Unknown lesson_id: {lesson_id}")
    row = approve_summary(lesson_id, teacher_id=teacher_id)
    if not row:
        raise ValueError("No draft summary to approve. Generate one first.")
    return public_media(lesson_id, student=False)


ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _unlink_local_media(image_url: str | None) -> None:
    raw = (image_url or "").strip()
    if not raw.startswith("/ar-media/"):
        return
    rel = raw[len("/ar-media/") :]
    path = MEDIA_ROOT / rel
    try:
        resolved = path.resolve()
        if resolved.is_file() and resolved.is_relative_to(MEDIA_ROOT.resolve()):
            resolved.unlink()
    except OSError:
        log.warning("could not delete gallery file %s", path)


def attach_lesson_image(
    lesson_id: str,
    *,
    image_url: str,
    caption: str = "",
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise KeyError(f"Unknown lesson_id: {lesson_id}")
    url = (image_url or "").strip()
    if not url:
        raise ValueError("image_url is required")
    add_lesson_image(
        lesson_id,
        image_url=url,
        caption=caption,
        image_type="gallery",
        teacher_id=teacher_id,
    )
    return public_media(lesson_id, student=False)


def upload_lesson_image_file(
    lesson_id: str,
    *,
    filename: str,
    content: bytes,
    caption: str = "",
    teacher_id: str = "teacher-1",
) -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise KeyError(f"Unknown lesson_id: {lesson_id}")
    if not content:
        raise ValueError("Choose an image file.")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large (max 8 MB).")
    ext = Path(filename or "").suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in ALLOWED_IMAGE_EXT:
        raise ValueError("Use a JPG, PNG, WEBP, or GIF image.")
    import uuid

    stem = f"gallery_{uuid.uuid4().hex[:12]}"
    dest = media_dir(lesson_id) / f"{stem}{ext}"
    dest.write_bytes(content)
    image_url = public_image_path(lesson_id, stem, ext.lstrip("."))
    add_lesson_image(
        lesson_id,
        image_url=image_url,
        caption=caption,
        image_type="gallery",
        teacher_id=teacher_id,
    )
    return public_media(lesson_id, student=False)


def remove_lesson_image(lesson_id: str, image_id: str) -> dict[str, Any]:
    cur = load_curriculum()
    if not cur.by_lesson_id(lesson_id):
        raise KeyError(f"Unknown lesson_id: {lesson_id}")
    row = delete_lesson_image(lesson_id, image_id)
    if not row:
        raise KeyError("Image not found for this chapter.")
    _unlink_local_media(row.get("image_url"))
    return public_media(lesson_id, student=False)
