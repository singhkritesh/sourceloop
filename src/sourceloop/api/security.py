"""Passwords (argon2), JWT access tokens, opaque refresh tokens, CSRF tokens, text sanitising, mandate signing."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from sourceloop.api.config import Settings
from sourceloop.audit.canonical import dumps

_CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff]")


def sanitize_text(s: str) -> str:
    """NFKC-normalise and strip control / zero-width characters before storage."""
    return _CTRL.sub("", unicodedata.normalize("NFKC", s)).strip()


class Passwords:
    def __init__(self, s: Settings) -> None:
        self._h = PasswordHasher(time_cost=s.argon_time_cost, memory_cost=s.argon_memory_cost,
                                 parallelism=s.argon_parallelism)
        self._dummy = self._h.hash("dummy-password-for-timing")

    def hash(self, pw: str) -> str:
        return self._h.hash(pw)

    def verify(self, hashed: str | None, pw: str) -> bool:
        """Constant-ish time: unknown users are verified against a dummy hash."""
        try:
            return self._h.verify(hashed or self._dummy, pw) and hashed is not None
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256-bit opaque


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


def csrf_ok(cookie: str | None, header: str | None) -> bool:
    return bool(cookie) and bool(header) and hmac.compare_digest(cookie, header)  # type: ignore[arg-type]


def make_access(s: Settings, now: datetime, user_id: uuid.UUID, role: str, org_id: uuid.UUID) -> tuple[str, str, datetime]:
    jti = uuid.uuid4().hex
    exp = now + timedelta(minutes=s.access_ttl_minutes)
    tok = jwt.encode({"sub": str(user_id), "role": role, "org": str(org_id), "jti": jti, "iat": int(now.timestamp()),
                      "exp": int(exp.timestamp())}, s.jwt_secret, algorithm="HS256")
    return tok, jti, exp


def decode_access(s: Settings, token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub", "jti"]})
    except jwt.PyJWTError:
        return None


def sign_payload(s: Settings, org_id: uuid.UUID, payload: object) -> str:
    key = hashlib.sha256(f"{s.org_signing_secret}|{org_id}".encode()).digest()
    return hmac.new(key, dumps(payload).encode(), hashlib.sha256).hexdigest()


def supplier_audit_digest(s: Settings, org_id: uuid.UUID, project_id: str, view: object) -> str:
    """Per-supplier-org digest of a supplier-VISIBLE audit record. It replaces the main chain's prev_hash/hash in supplier
    views, because those hashes cover events the supplier must not learn about. Keyed with the server secret, so it neither
    can be forged nor reveals anything about hidden events; the supplier can still detect that a record it saw changed."""
    key = hashlib.sha256(f"{s.org_signing_secret}|audit-view|{org_id}|{project_id}".encode()).digest()
    return hmac.new(key, dumps(view).encode(), hashlib.sha256).hexdigest()
