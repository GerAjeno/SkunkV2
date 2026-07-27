from __future__ import annotations

import base64
import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from fastapi import HTTPException, Request, status
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from .config import settings


SCRYPT_MAX_MEMORY = 64 * 1024 * 1024
LOGIN_BODY_LIMIT = 4096
LOGIN_ATTEMPT_WINDOW = 10 * 60
LOGIN_MAX_FAILURES = 5
LOGIN_BLOCK_SECONDS = 15 * 60
LOGIN_MAX_TRACKED_CLIENTS = 2048


@dataclass
class _LoginAttempt:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    last_seen: float = 0.0


class LoginAttemptLimiter:
    """Bound failed login attempts without storing credentials."""

    def __init__(
        self,
        *,
        window: int = LOGIN_ATTEMPT_WINDOW,
        max_failures: int = LOGIN_MAX_FAILURES,
        block_seconds: int = LOGIN_BLOCK_SECONDS,
        max_clients: int = LOGIN_MAX_TRACKED_CLIENTS,
    ) -> None:
        self.window = window
        self.max_failures = max_failures
        self.block_seconds = block_seconds
        self.max_clients = max_clients
        self._attempts: dict[str, _LoginAttempt] = {}
        self._lock = threading.Lock()

    def retry_after(self, client: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        with self._lock:
            attempt = self._attempts.get(client)
            if attempt is None:
                return 0
            self._expire_failures(attempt, current)
            if attempt.blocked_until > current:
                attempt.last_seen = current
                return math.ceil(attempt.blocked_until - current)
            if not attempt.failures:
                self._attempts.pop(client, None)
            return 0

    def record_failure(
        self,
        client: str,
        *,
        now: float | None = None,
    ) -> tuple[float, int]:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._prune(current)
            attempt = self._attempts.setdefault(client, _LoginAttempt())
            self._expire_failures(attempt, current)
            attempt.failures.append(current)
            attempt.last_seen = current
            failure_count = len(attempt.failures)
            if failure_count >= self.max_failures:
                attempt.blocked_until = current + self.block_seconds
                return 0.0, self.block_seconds
            # 250 ms, 500 ms, 1 s and 2 s before the hard block.
            return min(0.25 * (2 ** (failure_count - 1)), 2.0), 0

    def record_success(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()

    def _expire_failures(self, attempt: _LoginAttempt, now: float) -> None:
        cutoff = now - self.window
        while attempt.failures and attempt.failures[0] < cutoff:
            attempt.failures.popleft()
        if attempt.blocked_until <= now:
            attempt.blocked_until = 0.0

    def _prune(self, now: float) -> None:
        stale_before = now - max(self.window, self.block_seconds)
        stale = [
            client
            for client, attempt in self._attempts.items()
            if attempt.last_seen < stale_before and attempt.blocked_until <= now
        ]
        for client in stale:
            self._attempts.pop(client, None)
        if len(self._attempts) < self.max_clients:
            return
        oldest = min(
            self._attempts,
            key=lambda client: self._attempts[client].last_seen,
        )
        self._attempts.pop(oldest, None)


login_attempts = LoginAttemptLimiter()


class LoginBodyLimitMiddleware:
    """Reject oversized login bodies before Starlette parses the form."""

    def __init__(self, app: Callable, limit: int = LOGIN_BODY_LIMIT) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/login"
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            content_length = self.limit + 1
        if content_length > self.limit:
            response = PlainTextResponse(
                "Solicitud de inicio de sesión demasiado grande",
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 10:
        raise ValueError("La contraseña debe tener al menos 10 caracteres")
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        dklen=32,
        maxmem=SCRYPT_MAX_MEMORY,
    )
    return "scrypt$32768$8$1${}${}".format(
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, expected_b64 = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_b64.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
            maxmem=SCRYPT_MAX_MEMORY,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def require_authenticated(request: Request) -> None:
    if settings.dev_auth_bypass:
        return
    if request.session.get("authenticated") is not True:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


def require_csrf(request: Request) -> None:
    require_authenticated(request)
    expected = request.session.get("csrf")
    received = request.headers.get("X-CSRF-Token")
    if not expected or not received or not hmac.compare_digest(expected, received):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token CSRF inválido",
        )
