"""Sign-in with one shared password and a name each user types, and the cookie that remembers it.

Roadmap D5: everybody signs in with the same password and says who they are. The name is
*self-declared* - the password proves someone is allowed in, not who they are - and it is
what the audit trail and the usage ledger record.

Built for a serverless deployment, where nothing survives a request but what the browser
brings back:

* **the password is configuration**: ``APP_PASSWORD_HASH`` holds its hash, never the
  password (``python -m core.classify --hash-password`` makes one). Changing it is how access
  is revoked, and it signs everybody out.
* **the name is normalised** by :func:`normalize_name` - lower case, no diacritics, single
  spaces - so "Jan Novák" and "jan novak" are one person in the ledger.
* **the session is a signed cookie** (HMAC-SHA256 with ``SESSION_SECRET``) carrying the name
  and an expiry. Nothing is stored server-side. The signature also covers a fingerprint of
  the password hash, so a new password ends every session.
* **it fails closed**: a password hash without a ``SESSION_SECRET``, or a hash that cannot be
  read, means nobody can sign in, never that everybody is let in.

Passwords are hashed with PBKDF2-SHA256 from the standard library (no new dependency); the
slow hash is also the only brute-force brake, since a serverless function keeps no counter
of failed attempts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass
from typing import Final

#: The name recorded for spending no signed-in user can be credited with, including every
#: call recorded before sign-in existed.
UNKNOWN_USER: Final[str] = "unknown"

SESSION_COOKIE: Final[str] = "nace_esa_session"

NAME_LIMIT: Final[int] = 60

_ALGORITHM: Final[str] = "pbkdf2_sha256"
#: OWASP's 2023 recommendation for PBKDF2-HMAC-SHA256.
_ITERATIONS: Final[int] = 600_000
_NOT_NAME: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9 .\-]+")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """``pbkdf2_sha256$<iterations>$<salt>$<hash>``, safe to put in an environment variable."""
    if not password:
        raise ValueError("an empty password cannot be hashed")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def is_password_hash(encoded: str) -> bool:
    """True when ``encoded`` has the shape :func:`hash_password` produces."""
    parts = encoded.split("$")
    if len(parts) != 4 or parts[0] != _ALGORITHM or not parts[1].isdigit():
        return False
    try:
        return bool(_unb64(parts[2])) and bool(_unb64(parts[3]))
    except ValueError:
        return False


def verify_password(password: str, encoded: str) -> bool:
    """True when ``password`` matches; a malformed hash is a mismatch, never an error."""
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(iterations)
        )
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError):
        return False


def normalize_name(raw: str) -> str | None:
    """The name as the ledger keeps it: ``"  Jan  Novák "`` -> ``"jan novak"``.

    Lower case, diacritics removed, runs of spaces made one, and nothing but letters, digits,
    spaces, dots and hyphens. ``None`` when nothing is left, or when the result is the
    reserved ``unknown``.
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = _NOT_NAME.sub(" ", ascii_only.casefold())
    name = " ".join(cleaned.split())[:NAME_LIMIT].strip()
    if not name or name == UNKNOWN_USER:
        return None
    return name


@dataclass(frozen=True, slots=True)
class SessionSigner:
    """Issues and checks the session cookie."""

    secret: bytes
    max_age_seconds: int
    #: The configured password hash; a fingerprint of it is signed, so a new password
    #: ends every session.
    password_hash: str

    def _signature(self, payload: str) -> str:
        fingerprint = hashlib.sha256(self.password_hash.encode("utf-8")).hexdigest()[:16]
        message = f"{payload}.{fingerprint}".encode()
        return _b64(hmac.new(self.secret, message, hashlib.sha256).digest())

    def issue(self, name: str, *, now: float | None = None) -> str:
        expires = int((time.time() if now is None else now) + self.max_age_seconds)
        payload = _b64(f"{expires}:{name}".encode())
        return f"{payload}.{self._signature(payload)}"

    def verify(self, token: str | None, *, now: float | None = None) -> str | None:
        """The signed-in name, or ``None`` for a missing, forged, expired or stale cookie."""
        if not token or token.count(".") != 1:
            return None
        payload, signature = token.split(".")
        if not hmac.compare_digest(signature, self._signature(payload)):
            return None
        try:
            expires_text, _, name = _unb64(payload).decode("utf-8").partition(":")
            expires = int(expires_text)
        except (ValueError, UnicodeDecodeError):
            return None
        if expires < (time.time() if now is None else now):
            return None
        return normalize_name(name)


__all__ = [
    "NAME_LIMIT",
    "SESSION_COOKIE",
    "UNKNOWN_USER",
    "SessionSigner",
    "hash_password",
    "is_password_hash",
    "normalize_name",
    "verify_password",
]
