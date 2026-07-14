"""Crypto core: reversible secrets at rest, AES-256-GCM.

Design: protection.html#crypto-core. Serves every *_enc column across the
schema (ai_providers.api_key_enc, tools.credential_enc; smtp/sources/
notifications arrive with their stages). Ciphertext is self-describing —
"v1:<nonce>:<ciphertext+tag>" (urlsafe base64) — the version prefix is the
key ID hook for rotation (v2: a key map instead of a single key).
"""

import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = "v1"
_KEY_BYTES = 32
_NONCE_BYTES = 12  # GCM standard nonce size
_HKDF_INFO = b"achilles-crypto-core"
_MASK_VISIBLE_CHARS = 4


class CiphertextInvalidError(Exception):
    """Wrong key, tampered data, or a malformed/unknown-version token."""


def derive_crypto_key(*, crypto_key: str, secret_key: str) -> bytes:
    """Resolve the AES key: explicit CRYPTO_KEY, else HKDF from SECRET_KEY.

    An explicit key must be 32 bytes of urlsafe base64. The HKDF fallback
    keeps dev/single-env deploys keyless; production sets CRYPTO_KEY so the
    data key is independent from the JWT-signing secret.
    """
    if crypto_key:
        try:
            raw = base64.urlsafe_b64decode(crypto_key)
        except (binascii.Error, ValueError) as exc:
            msg = "CRYPTO_KEY must be urlsafe base64"
            raise ValueError(msg) from exc
        if len(raw) != _KEY_BYTES:
            msg = f"CRYPTO_KEY must decode to {_KEY_BYTES} bytes, got {len(raw)}"
            raise ValueError(msg)
        return raw
    hkdf = HKDF(algorithm=SHA256(), length=_KEY_BYTES, salt=None, info=_HKDF_INFO)
    return hkdf.derive(secret_key.encode())


def encrypt(plaintext: str, *, key: bytes) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return ":".join(
        (
            _VERSION,
            base64.urlsafe_b64encode(nonce).decode(),
            base64.urlsafe_b64encode(ciphertext).decode(),
        )
    )


def encrypt_optional(plaintext: str | None, *, key: bytes) -> str | None:
    """Write-only secret columns: '' and None both mean "no secret" → NULL."""
    return encrypt(plaintext, key=key) if plaintext else None


def decrypt(token: str, *, key: bytes) -> str:
    version, _, rest = token.partition(":")
    nonce_b64, _, ciphertext_b64 = rest.partition(":")
    if version != _VERSION or not nonce_b64 or not ciphertext_b64:
        raise CiphertextInvalidError
    try:
        nonce = base64.urlsafe_b64decode(nonce_b64)
        ciphertext = base64.urlsafe_b64decode(ciphertext_b64)
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
    except (InvalidTag, binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise CiphertextInvalidError from exc


def mask_secret(plaintext: str) -> str:
    """Display mask for a stored secret: ••••xxxx (last 4 chars).

    Short secrets are fully masked — revealing half of them is worse than
    showing nothing.
    """
    if len(plaintext) <= _MASK_VISIBLE_CHARS:
        return "••••"
    return f"••••{plaintext[-_MASK_VISIBLE_CHARS:]}"


def mask_encrypted(token: str | None, *, key: bytes) -> str | None:
    """Display mask for an *_enc column, or None when it holds no secret.

    A value that fails to decrypt (data key rotated, row corrupted) masks as
    a sentinel rather than raising — one broken secret must not 500 a whole
    admin screen; the visible sentinel tells the admin to re-enter it.
    """
    if not token:
        return None
    try:
        return mask_secret(decrypt(token, key=key))
    except CiphertextInvalidError:
        return "••••????"
