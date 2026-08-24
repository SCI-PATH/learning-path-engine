"""S3 storage for teacher chapter gallery images.

When S3_BUCKET is set, uploads go to:
  s3://{bucket}/{S3_LESSON_PREFIX}/{lesson_id}/{filename}

Public URL is stored in Neon. Unset S3_BUCKET keeps local /ar-media files.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import unquote, urlparse

log = logging.getLogger("learning_path.s3_gallery")

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _safe_id(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "lesson").strip())[:48]
    return s or "lesson"


def s3_enabled() -> bool:
    return bool(os.getenv("S3_BUCKET", "").strip())


def _bucket() -> str:
    return os.getenv("S3_BUCKET", "").strip()


def _region() -> str:
    return os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-south-1")).strip() or "ap-south-1"


def _prefix() -> str:
    return os.getenv("S3_LESSON_PREFIX", "lesson-media").strip().strip("/") or "lesson-media"


def _client():
    import boto3

    return boto3.client("s3", region_name=_region())


def gallery_object_key(lesson_id: str, filename: str) -> str:
    return f"{_prefix()}/{_safe_id(lesson_id)}/{filename}"


def public_object_url(key: str) -> str:
    bucket = _bucket()
    region = _region()
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def upload_gallery_bytes(lesson_id: str, filename: str, content: bytes, *, ext: str) -> str:
    """Put object and return the public HTTPS URL."""
    key = gallery_object_key(lesson_id, filename)
    content_type = _CONTENT_TYPES.get(ext.lower(), "application/octet-stream")
    _client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=content,
        ContentType=content_type,
        CacheControl="public, max-age=31536000",
    )
    url = public_object_url(key)
    log.info("gallery uploaded s3://%s/%s", _bucket(), key)
    return url


def delete_gallery_url(image_url: str | None) -> None:
    """Delete an object if it belongs to this bucket. Ignore missing keys."""
    raw = (image_url or "").strip()
    if not raw or not s3_enabled():
        return
    key = _key_from_url(raw)
    if not key:
        return
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
        log.info("gallery deleted s3://%s/%s", _bucket(), key)
    except Exception:
        log.warning("could not delete S3 gallery object %s", raw, exc_info=True)


def _key_from_url(url: str) -> str | None:
    bucket = _bucket()
    if not bucket:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    path = unquote((parsed.path or "").lstrip("/"))
    if not path:
        return None
    prefixes = (
        f"{bucket}.s3.{_region()}.amazonaws.com",
        f"{bucket}.s3.amazonaws.com",
        f"s3.{_region()}.amazonaws.com",
        "s3.amazonaws.com",
    )
    if host == prefixes[0] or host == prefixes[1]:
        return path
    if host in prefixes[2:] or host.startswith("s3.") and host.endswith(".amazonaws.com"):
        if path.startswith(f"{bucket}/"):
            return path[len(bucket) + 1 :]
    return None
