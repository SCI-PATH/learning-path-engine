"""Tests for rate limiting and teacher JWT gate."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from jose import jwt

from app.http_security import (
    SlidingWindowLimiter,
    check_lpe_rate_limit,
    verify_teacher_bearer,
)


def test_rate_limiter_blocks_after_limit():
    async def _run():
        limiter = SlidingWindowLimiter()
        key = "test-ip"
        for _ in range(5):
            assert await limiter.allow(key, limit=5, window_seconds=60.0)
        assert not await limiter.allow(key, limit=5, window_seconds=60.0)

    asyncio.run(_run())


def test_lpe_teacher_generate_rate_rule():
    async def _run():
        ip = "127.0.0.1"
        for _ in range(12):
            assert await check_lpe_rate_limit("/teacher/generate", "POST", ip)
        assert not await check_lpe_rate_limit("/teacher/generate", "POST", ip)

    asyncio.run(_run())


def test_teacher_auth_off_allows_without_token(monkeypatch):
    monkeypatch.setenv("LPE_TEACHER_AUTH", "off")
    request = MagicMock()
    request.headers = {}
    status, msg = verify_teacher_bearer(request)
    assert status is None


def test_teacher_auth_required_blocks_without_token(monkeypatch):
    monkeypatch.setenv("LPE_TEACHER_AUTH", "required")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-at-least-32-characters-long")
    request = MagicMock()
    request.headers = {}
    status, msg = verify_teacher_bearer(request)
    assert status == 401


def test_teacher_auth_accepts_educator_jwt(monkeypatch):
    secret = "test-secret-key-at-least-32-characters-long"
    monkeypatch.setenv("LPE_TEACHER_AUTH", "required")
    monkeypatch.setenv("JWT_SECRET", secret)
    token = jwt.encode({"sub": "t1", "role": "teacher"}, secret, algorithm="HS256")
    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"}
    status, msg = verify_teacher_bearer(request)
    assert status is None
