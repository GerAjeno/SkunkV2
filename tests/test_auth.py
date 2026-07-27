import asyncio

from skunk_pc.auth import (
    LoginAttemptLimiter,
    LoginBodyLimitMiddleware,
    hash_password,
    verify_password,
)
from skunk_pc import main


def test_password_round_trip() -> None:
    encoded = hash_password("contraseña-segura-123")
    assert verify_password("contraseña-segura-123", encoded)
    assert not verify_password("otra-contraseña", encoded)


def test_login_limiter_blocks_fifth_failure_and_expires() -> None:
    limiter = LoginAttemptLimiter(window=600, max_failures=5, block_seconds=900)

    for index in range(4):
        delay, retry_after = limiter.record_failure("10.1.0.20", now=float(index))
        assert delay > 0
        assert retry_after == 0

    delay, retry_after = limiter.record_failure("10.1.0.20", now=4.0)
    assert delay == 0
    assert retry_after == 900
    assert limiter.retry_after("10.1.0.20", now=5.0) == 899
    assert limiter.retry_after("10.1.0.20", now=905.0) == 0


def test_success_clears_failed_login_attempts() -> None:
    limiter = LoginAttemptLimiter(max_failures=2)
    limiter.record_failure("10.1.0.21", now=1.0)
    limiter.record_success("10.1.0.21")

    _, retry_after = limiter.record_failure("10.1.0.21", now=2.0)

    assert retry_after == 0


def test_login_body_limit_rejects_oversized_body() -> None:
    downstream_called = False
    sent = []

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = LoginBodyLimitMiddleware(downstream, limit=4096)
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/login",
                "headers": [(b"content-length", b"4097")],
            },
            receive,
            send,
        )
    )

    assert not downstream_called
    assert sent[0]["status"] == 413


def test_login_endpoint_uses_limiter_and_bounded_password() -> None:
    source = (main.PACKAGE_DIR / "main.py").read_text()

    assert "login_attempts.retry_after(client_ip)" in source
    assert "login_attempts.record_failure(client_ip)" in source
    assert "login_attempts.record_success(client_ip)" in source
    assert "max_length=256" in source
