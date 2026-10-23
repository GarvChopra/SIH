"""Authentication: PIN hashing, JWT, DB-backed login, login rate-limit."""
from __future__ import annotations


import os
import time
from dataclasses import dataclass, field

import bcrypt
import jwt

from config import Config

_ALG = "HS256"
_ACCESS_TTL = 15 * 60
_REFRESH_TTL = 7 * 24 * 3600


class AuthError(Exception):
    pass


def hash_pin(pin: str) -> str:
    # BCRYPT_ROUNDS lets the test suite run fast; production keeps the default 12.
    rounds = int(os.environ.get("BCRYPT_ROUNDS") or 12)
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_pin(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), (hashed or "").encode())
    except Exception:  # noqa: BLE001
        return False


def create_access_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user["username"], "name": user.get("display_name", user["username"]),
         "role": user["role"], "dept": user.get("department"),
         "iat": now, "exp": now + _ACCESS_TTL, "type": "access"},
        Config.JWT_SECRET, algorithm=_ALG,
    )


def create_refresh_token(user_id: str, days: int = 7) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + days * 24 * 3600, "type": "refresh"},
        Config.JWT_SECRET, algorithm=_ALG,
    )


def _decode(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, Config.JWT_SECRET, algorithms=[_ALG])
    except jwt.ExpiredSignatureError:
        raise AuthError("token expired")
    except jwt.InvalidTokenError as e:  # noqa: BLE001
        raise AuthError(f"invalid token: {e}")
    if payload.get("type") != expected_type:
        raise AuthError("wrong token type")
    return payload


def decode_access_token(token: str) -> dict:
    return _decode(token, "access")


def decode_refresh_token(token: str) -> dict:
    return _decode(token, "refresh")


@dataclass
class LoginResult:
    success: bool
    user: dict = field(default_factory=dict)
    access_token: str = ""
    refresh_token: str = ""
    error: str | None = None


def login(username: str, pin: str) -> LoginResult:
    from db import users
    u = users.get_by_username((username or "").lower().strip())
    if not u or not u.get("is_active", True):
        return LoginResult(False, error="No such account")
    if not verify_pin(pin, u["pin_hash"]):
        return LoginResult(False, error="Incorrect PIN")
    return LoginResult(
        True, user=u,
        access_token=create_access_token(u),
        refresh_token=create_refresh_token(u["username"]),
    )


def refresh(refresh_token: str) -> LoginResult:
    from db import users
    payload = decode_refresh_token(refresh_token)
    u = users.get_by_username(payload["sub"])
    if not u or not u.get("is_active", True):
        return LoginResult(False, error="Account inactive")
    return LoginResult(
        True, user=u,
        access_token=create_access_token(u),
        refresh_token=create_refresh_token(u["username"]),
    )


# ── login rate limit (in-memory sliding window) ──────────────────────────────
_ATTEMPTS: dict[str, list[float]] = {}
_MAX, _WINDOW = 5, 300.0


def check_rate_limit(key: str) -> bool:
    now = time.time()
    bucket = [t for t in _ATTEMPTS.get(key, []) if now - t < _WINDOW]
    bucket.append(now)
    _ATTEMPTS[key] = bucket
    return len(bucket) <= _MAX
