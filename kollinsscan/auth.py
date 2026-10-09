"""Single-user login. Only hashes of session tokens are stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from .db import DB

COOKIE = "kollinsscan_session"


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, db: DB, user: str, password: str, session_hours: int):
        self.db = db
        self.user = user
        self._salt = secrets.token_bytes(16)
        self._pw = self._hash(password) if password else None
        self.session_seconds = session_hours * 3600
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _hash(self, password: str) -> bytes:
        return hashlib.scrypt(password.encode(), salt=self._salt, n=2**14, r=8, p=1)

    def rate_limited(self, client: str) -> bool:
        """At most 10 failed sign-ins per client per 15 minutes."""
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if t > time.time() - 900]
            self._failures[client] = recent
            return len(recent) >= 10

    def check_password(self, client: str, user: str, password: str) -> bool:
        user_ok = hmac.compare_digest(user.encode(), self.user.encode())
        pw_ok = self._pw is not None and hmac.compare_digest(self._hash(password), self._pw)
        if user_ok and pw_ok:
            return True
        with self._lock:
            self._failures.setdefault(client, []).append(time.time())
        return False

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        self.db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        self.db.execute("INSERT INTO sessions VALUES (?, ?)",
                        (_sha256(token), now + self.session_seconds))
        return token

    def valid_session(self, token: str | None) -> bool:
        return bool(token) and self.db.one(
            "SELECT 1 FROM sessions WHERE token_hash = ? AND expires > ?",
            (_sha256(token), time.time())) is not None

    def end_session(self, token: str | None) -> None:
        if token:
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))
