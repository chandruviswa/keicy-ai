"""Passwords (scrypt, stdlib) and access tokens (JWT, HS256)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings

log = logging.getLogger("procura.security")

# scrypt parameters (n=2^14, r=8, p=1 ≈ 16 MB, ~50 ms): OWASP-recommended minimum for interactive logins.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
_ALGO = "HS256"
_ISSUER = "procura-ai"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # Burn comparable time so unknown users and users without a password look the same.
        hashlib.scrypt(password.encode(), salt=b"0" * 16, n=_N, r=_R, p=_P, dklen=_DKLEN)
        return False
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(dk)))
        return hmac.compare_digest(candidate, _unb64(dk))
    except (ValueError, TypeError):
        return False


_ephemeral_secret: str | None = None


def _secret() -> str:
    global _ephemeral_secret
    s = get_settings()
    if s.jwt_secret:
        return s.jwt_secret
    if s.app_env == "production":
        raise RuntimeError("JWT_SECRET must be set in production")
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(48)
        log.warning("JWT_SECRET not set — using a random per-process secret; sessions end when the server restarts")
    return _ephemeral_secret


def create_access_token(user_id: uuid.UUID, role: str, *, minutes: int | None = None) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=minutes if minutes is not None else get_settings().access_token_minutes)
    token = jwt.encode({"sub": str(user_id), "role": role, "iat": now, "exp": exp, "iss": _ISSUER, "jti": secrets.token_hex(8)},
                       _secret(), algorithm=_ALGO)
    return token, exp


class TokenError(Exception):
    pass


def decode_token(token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALGO], issuer=_ISSUER, options={"require": ["sub", "exp", "iat"]})
        return uuid.UUID(claims["sub"])
    except jwt.ExpiredSignatureError as e:
        raise TokenError("Session expired — please sign in again") from e
    except (jwt.PyJWTError, ValueError, KeyError) as e:
        raise TokenError("Invalid access token") from e


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
