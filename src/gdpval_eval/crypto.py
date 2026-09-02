"""Manifest encryption: a thin wrapper over cryptography.Fernet (spec T2).

No cryptographic primitives are implemented here — Fernet does all the
authenticated encryption. This module only adapts its API (bytes/str
plumbing) and normalizes its errors into `ManifestCryptoError`, whose
message never carries plaintext data or key material.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from gdpval_eval.models import GdpvalEvalError


class ManifestCryptoError(GdpvalEvalError):
    """Invalid/mismatched manifest key, or a corrupted/tampered token.

    Never carries the plaintext data or key material in its message.
    """


def generate_key() -> str:
    """Generate a fresh Fernet key, ready to pass to encrypt_bytes/decrypt_bytes."""
    return Fernet.generate_key().decode("ascii")


def _fernet(key: str) -> Fernet:
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ManifestCryptoError("invalid manifest encryption key") from exc


def encrypt_bytes(data: bytes, key: str) -> bytes:
    return _fernet(key).encrypt(data)


def decrypt_bytes(token: bytes, key: str) -> bytes:
    try:
        return _fernet(key).decrypt(token)
    except InvalidToken as exc:
        raise ManifestCryptoError("manifest token failed decryption") from exc
