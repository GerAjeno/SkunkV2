from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, status

from .config import settings


SCRYPT_MAX_MEMORY = 64 * 1024 * 1024


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
