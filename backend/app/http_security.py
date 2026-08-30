"""Security middleware: rate limits, response headers, optional teacher JWT."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict

from jose import ExpiredSignatureError, JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class SlidingWindowLimiter:
    """In-process sliding-window limiter (fine for single-worker demo / EC2)."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, *, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        async with self._lock:
            bucket = [t for t in self._hits[key] if t > now - window_seconds]
            if len(bucket) >= limit:
                self._hits[key] = bucket
                return False
            bucket.append(now)
            self._hits[key] = bucket
            return True


_limiter = SlidingWindowLimiter()

# (path prefix or exact, method or *, limit, window_seconds)
LPE_RATE_RULES: list[tuple[str, str, int, float]] = [
    ("/teacher/generate", "POST", 12, 60.0),
    ("/teacher/", "*", 90, 60.0),
    ("/admin/", "*", 30, 60.0),
    ("/debug/", "*", 30, 60.0),
    ("/client-log", "POST", 40, 60.0),
    ("/analytics/profile", "POST", 60, 60.0),
]


def _rule_matches(path: str, method: str, prefix: str, rule_method: str) -> bool:
    if rule_method != "*" and method.upper() != rule_method.upper():
        return False
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix or path.startswith(prefix + "/")


async def check_lpe_rate_limit(path: str, method: str, ip: str) -> bool:
    for prefix, rule_method, limit, window in LPE_RATE_RULES:
        if _rule_matches(path, method, prefix, rule_method):
            key = f"{ip}:{method}:{prefix}"
            return await _limiter.allow(key, limit=limit, window_seconds=window)
    return True


def _teacher_auth_mode() -> str:
    return (os.getenv("LPE_TEACHER_AUTH") or "off").strip().lower()


def _jwt_settings() -> tuple[str, str]:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    algorithm = (os.getenv("JWT_ALGORITHM") or "HS256").strip()
    return secret, algorithm


def _service_key_ok(request: Request) -> bool:
    expected = (os.getenv("LPE_SERVICE_KEY") or "").strip()
    if not expected:
        return False
    provided = (request.headers.get("x-service-key") or "").strip()
    return provided == expected


def verify_teacher_bearer(request: Request) -> tuple[int | None, str | None]:
    """
    Returns (status_code, message) when auth fails; (None, None) when OK or skipped.
    """
    mode = _teacher_auth_mode()
    if mode == "off":
        return None, None
    if _service_key_ok(request):
        return None, None

    secret, algorithm = _jwt_settings()
    if not secret:
        if mode == "required":
            log.warning("LPE_TEACHER_AUTH=%s but JWT_SECRET unset — allowing request", mode)
        return None, None

    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        if mode == "required":
            return 401, "Teacher login required."
        return None, None

    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except ExpiredSignatureError:
        return 401, "Session expired. Please log in again."
    except JWTError:
        return 401, "Invalid session."

    role = (payload.get("role") or "").lower()
    if role not in ("teacher", "educator"):
        return 403, "Teacher role required."
    return None, None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        ip = _client_ip(request)
        if not await check_lpe_rate_limit(request.url.path, request.method, ip):
            log.warning("rate_limit path=%s ip=%s", request.url.path, ip)
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please wait a moment and try again."},
            )
        return await call_next(request)


class TeacherAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith("/teacher/") or path.startswith("/admin/"):
            status, message = verify_teacher_bearer(request)
            if status is not None:
                log.warning("teacher_auth_denied path=%s status=%s", path, status)
                return JSONResponse(status_code=status, content={"detail": message})
        return await call_next(request)
