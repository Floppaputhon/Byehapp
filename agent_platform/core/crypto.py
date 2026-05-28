"""Encryption helpers.

Everything sensitive at rest is encrypted with a Fernet key derived from a
single master PIN provided by the operator (``MASTER_PIN`` env var). The key
is **never persisted** — if the operator loses the PIN, the data is lost.

The derivation uses PBKDF2-HMAC-SHA256 with a per-database salt that we
store in the ``meta`` table. The salt is generated on first launch and never
changes thereafter, which means the same PIN always yields the same key for
a given database file.
"""

from __future__ import annotations

import base64
import os
import secrets
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16


class CryptoError(RuntimeError):
    """Raised when encryption or decryption fails."""


def generate_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def derive_key(pin: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from ``pin`` + ``salt``."""
    if not pin:
        raise CryptoError("master PIN must not be empty")
    if len(salt) < 8:
        raise CryptoError("salt is too short")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(pin.encode("utf-8")))


class Vault:
    """Symmetric encryption wrapper around a Fernet key."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_pin(cls, pin: str, salt: bytes) -> "Vault":
        return cls(derive_key(pin, salt))

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            raise CryptoError("cannot encrypt None")
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("ascii")

    def decrypt(self, token: str) -> str:
        if not token:
            raise CryptoError("cannot decrypt empty token")
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise CryptoError("invalid token (wrong master PIN?)") from exc

    def try_decrypt(self, token: str) -> Optional[str]:
        try:
            return self.decrypt(token)
        except CryptoError:
            return None
