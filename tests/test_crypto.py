"""Tests for gdpval_eval.crypto (spec T2).

Fixtures are synthetic byte strings standing in for manifest content —
never real criterion text, task_ids, or hashes.
"""

from __future__ import annotations

import pytest

from gdpval_eval.crypto import ManifestCryptoError, decrypt_bytes, encrypt_bytes, generate_key


def test_crypto_roundtrip_preserves_data():
    key = generate_key()
    data = b"synthetic manifest payload \x00\xff with binary bytes"

    token = encrypt_bytes(data, key)

    assert token != data
    assert decrypt_bytes(token, key) == data


def test_crypto_generate_key_produces_directly_usable_key():
    key = generate_key()

    assert isinstance(key, str)
    assert key != ""

    data = b"synthetic-fixture-payload"
    token = encrypt_bytes(data, key)

    assert decrypt_bytes(token, key) == data


def test_crypto_invalid_or_mismatched_key_raises_manifest_crypto_error():
    good_key = generate_key()
    other_key = generate_key()
    secret_marker = b"synthetic-secret-marker-should-not-leak"
    token = encrypt_bytes(secret_marker, good_key)

    with pytest.raises(ManifestCryptoError) as mismatched:
        decrypt_bytes(token, other_key)
    assert "synthetic-secret-marker-should-not-leak" not in str(mismatched.value)

    with pytest.raises(ManifestCryptoError) as malformed:
        encrypt_bytes(secret_marker, "not-a-valid-fernet-key")
    assert "synthetic-secret-marker-should-not-leak" not in str(malformed.value)


def test_crypto_corrupted_token_raises_manifest_crypto_error():
    key = generate_key()
    secret_marker = b"synthetic-fixture-data-for-corruption"
    token = bytearray(encrypt_bytes(secret_marker, key))
    token[-1] ^= 0xFF  # flip the last byte to break the token's authentication tag

    with pytest.raises(ManifestCryptoError) as excinfo:
        decrypt_bytes(bytes(token), key)
    assert "synthetic-fixture-data-for-corruption" not in str(excinfo.value)
