from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Header, HTTPException, Request


Role = Literal["viewer", "trader", "admin"]


@dataclass(frozen=True)
class Principal:
    name: str
    role: Role
    session_id: str | None = None


@dataclass
class Session:
    id: str
    principal: Principal
    expires_at: float


class SessionAuth:
    """Small local-session auth layer for a single-user/private deployment.

    Production internet-facing deployments should replace this with an external
    identity provider and durable shared session storage. MT5 credentials are
    never accepted from the browser.
    """

    def __init__(self) -> None:
        self.admin_user = os.getenv("TRAID_ADMIN_USER", "admin")
        self.admin_password_hash = os.getenv("TRAID_ADMIN_PASSWORD_HASH")
        self.admin_password = os.getenv("TRAID_ADMIN_PASSWORD")
        self.viewer_user = os.getenv("TRAID_VIEWER_USER")
        self.viewer_password_hash = os.getenv("TRAID_VIEWER_PASSWORD_HASH")
        self.viewer_password = os.getenv("TRAID_VIEWER_PASSWORD")
        self.legacy_key = os.getenv("TRAID_TRADING_API_KEY")
        self.ttl_seconds = int(os.getenv("TRAID_SESSION_TTL_SECONDS", "28800"))
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()

    @staticmethod
    def hash_password(password: str, salt: str | None = None) -> str:
        salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310000)
        return f"pbkdf2_sha256$310000${salt}${digest.hex()}"

    @staticmethod
    def verify_password(password: str, encoded: str | None, plain_fallback: str | None = None) -> bool:
        if encoded:
            try:
                algorithm, iterations, salt, expected = encoded.split("$", 3)
                if algorithm != "pbkdf2_sha256":
                    return False
                digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
                return hmac.compare_digest(digest, expected)
            except (ValueError, TypeError):
                return False
        return bool(plain_fallback and hmac.compare_digest(password, plain_fallback))

    def login(self, username: str, password: str) -> dict[str, Any]:
        role: Role | None = None
        if hmac.compare_digest(username, self.admin_user) and self.verify_password(
            password, self.admin_password_hash, self.admin_password
        ):
            role = "admin"
        elif self.viewer_user and hmac.compare_digest(username, self.viewer_user) and self.verify_password(
            password, self.viewer_password_hash, self.viewer_password
        ):
            role = "viewer"
        if role is None:
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        token = secrets.token_urlsafe(36)
        session_id = secrets.token_hex(12)
        principal = Principal(name=username, role=role, session_id=session_id)
        with self._lock:
            self._sessions[token] = Session(id=session_id, principal=principal, expires_at=time.time() + self.ttl_seconds)
        return {"token": token, "expires_in": self.ttl_seconds, "principal": principal.__dict__}

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def resolve(self, authorization: str | None, legacy_key: str | None = None) -> Principal | None:
        if self.legacy_key and legacy_key and hmac.compare_digest(self.legacy_key, legacy_key):
            return Principal(name="legacy-api-key", role="admin")
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        token = authorization.split(" ", 1)[1].strip()
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if session.expires_at <= time.time():
                self._sessions.pop(token, None)
                return None
            return session.principal

    def require(self, minimum: Role = "viewer"):
        order = {"viewer": 0, "trader": 1, "admin": 2}

        async def dependency(
            authorization: str | None = Header(default=None),
            x_traid_key: str | None = Header(default=None, alias="X-Traid-Key"),
        ) -> Principal:
            principal = self.resolve(authorization, x_traid_key)
            if principal is None:
                raise HTTPException(status_code=401, detail="Authentication required.")
            if order[principal.role] < order[minimum]:
                raise HTTPException(status_code=403, detail=f"{minimum.title()} access is required.")
            return principal

        return dependency


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._buckets: dict[str, list[float]] = {}

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = [stamp for stamp in self._buckets.get(key, []) if stamp >= cutoff]
            if len(bucket) >= limit:
                retry = max(1, int(window_seconds - (now - bucket[0])))
                raise HTTPException(status_code=429, detail="Too many requests.", headers={"Retry-After": str(retry)})
            bucket.append(now)
            self._buckets[key] = bucket


AUTH = SessionAuth()
LIMITER = RateLimiter()


def client_key(request: Request, suffix: str = "") -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{suffix}"
