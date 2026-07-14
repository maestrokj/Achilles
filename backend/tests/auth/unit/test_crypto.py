"""Crypto-core unit cases — protection.html#crypto-core (P0, unit)."""

import base64
import os

import pytest

from achilles.auth.security.crypto import (
    CiphertextInvalidError,
    decrypt,
    derive_crypto_key,
    encrypt,
    mask_secret,
)

pytestmark = [pytest.mark.unit, pytest.mark.p0]

KEY = derive_crypto_key(crypto_key="", secret_key="unit-test-secret")
OTHER_KEY = derive_crypto_key(crypto_key="", secret_key="another-secret")


def test_roundtrip():
    token = encrypt("sk-ant-api03-secret", key=KEY)
    assert decrypt(token, key=KEY) == "sk-ant-api03-secret"


def test_format_is_versioned():
    version, nonce_b64, ciphertext_b64 = encrypt("x", key=KEY).split(":")
    assert version == "v1"
    assert len(base64.urlsafe_b64decode(nonce_b64)) == 12
    assert base64.urlsafe_b64decode(ciphertext_b64)


def test_nonce_is_unique_per_call():
    assert encrypt("same", key=KEY) != encrypt("same", key=KEY)


def test_tampered_ciphertext_rejected():
    version, nonce_b64, ciphertext_b64 = encrypt("secret", key=KEY).split(":")
    raw = bytearray(base64.urlsafe_b64decode(ciphertext_b64))
    raw[0] ^= 0x01
    tampered = ":".join((version, nonce_b64, base64.urlsafe_b64encode(bytes(raw)).decode()))
    with pytest.raises(CiphertextInvalidError):
        decrypt(tampered, key=KEY)


def test_tampered_nonce_rejected():
    version, nonce_b64, ciphertext_b64 = encrypt("secret", key=KEY).split(":")
    raw = bytearray(base64.urlsafe_b64decode(nonce_b64))
    raw[0] ^= 0x01
    tampered = ":".join((version, base64.urlsafe_b64encode(bytes(raw)).decode(), ciphertext_b64))
    with pytest.raises(CiphertextInvalidError):
        decrypt(tampered, key=KEY)


def test_wrong_key_rejected():
    token = encrypt("secret", key=KEY)
    with pytest.raises(CiphertextInvalidError):
        decrypt(token, key=OTHER_KEY)


@pytest.mark.parametrize("bad", ["", "v1", "v1:only-one-part", "v2:AAAA:AAAA", "garbage"])
def test_malformed_token_rejected(bad: str):
    with pytest.raises(CiphertextInvalidError):
        decrypt(bad, key=KEY)


def test_hkdf_is_deterministic():
    assert derive_crypto_key(crypto_key="", secret_key="s") == derive_crypto_key(
        crypto_key="", secret_key="s"
    )
    assert KEY != OTHER_KEY


def test_explicit_key_accepted():
    raw = os.urandom(32)
    encoded = base64.urlsafe_b64encode(raw).decode()
    assert derive_crypto_key(crypto_key=encoded, secret_key="ignored") == raw


@pytest.mark.parametrize("bad", ["not-base64!!!", base64.urlsafe_b64encode(b"short").decode()])
def test_explicit_key_validated(bad: str):
    with pytest.raises(ValueError, match="CRYPTO_KEY"):
        derive_crypto_key(crypto_key=bad, secret_key="")


def test_mask_shows_only_tail():
    assert mask_secret("sk-ant-api03-abcdWXYZ") == "••••WXYZ"


@pytest.mark.parametrize("short", ["", "ab", "abcd"])
def test_mask_hides_short_secrets(short: str):
    assert mask_secret(short) == "••••"
