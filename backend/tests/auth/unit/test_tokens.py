"""One-time token material unit cases — tests.html (P0, unit)."""

import pytest

from achilles.auth.constants import API_KEY_DISPLAY_PREFIX_LEN, LINK_CODE_ALPHABET, LINK_CODE_LENGTH
from achilles.auth.security.tokens import (
    generate_api_key,
    generate_link_code,
    generate_token,
    hash_token,
    looks_like_link_code,
    normalize_link_code,
    tokens_match,
)

pytestmark = [pytest.mark.unit, pytest.mark.p0]


def test_token_is_long_urlsafe():
    token = generate_token()
    # token_urlsafe(32) → 256 bits → 43 base64url chars
    assert len(token) >= 43
    assert all(c.isalnum() or c in "-_" for c in token)


def test_tokens_are_unique():
    tokens = {generate_token() for _ in range(100)}
    assert len(tokens) == 100


def test_hash_differs_from_raw():
    token = generate_token()
    hashed = hash_token(token)
    assert hashed != token
    assert len(hashed) == 64  # sha256 hex


def test_hash_is_deterministic():
    token = generate_token()
    assert hash_token(token) == hash_token(token)


def test_tokens_match_constant_time_semantics():
    token = generate_token()
    assert tokens_match(hash_token(token), hash_token(token))
    assert not tokens_match(hash_token(token), hash_token(generate_token()))


def test_link_code_is_short_grouped_and_unambiguous():
    code = generate_link_code()
    # "K7P2-9XQ4" shape: two groups joined by a dash.
    body = code.replace("-", "")
    assert len(body) == LINK_CODE_LENGTH
    assert all(c in LINK_CODE_ALPHABET for c in body)
    assert "-" in code


def test_link_codes_are_unique():
    codes = {generate_link_code() for _ in range(100)}
    assert len(codes) == 100


def test_normalize_link_code_forgives_case_and_separators():
    code = generate_link_code()
    canonical = normalize_link_code(code)
    assert canonical == code.replace("-", "").upper()
    # Lower-case, no dash, stray spaces — all normalize to the same thing.
    assert normalize_link_code(code.lower()) == canonical
    assert normalize_link_code(f" {code.lower().replace('-', '')} ") == canonical


def test_looks_like_link_code_matches_shape_not_prose():
    assert looks_like_link_code(generate_link_code())
    assert looks_like_link_code(generate_link_code().lower())
    assert not looks_like_link_code("what is our roadmap?")
    assert not looks_like_link_code("SHORT")
    assert not looks_like_link_code(generate_token())  # the long URL token is not a link code
    # A bare word of code-alphabet letters must NOT be mistaken for a code — the
    # dash is the disambiguator, so an ordinary short DM stays a question.
    assert not looks_like_link_code("DEADBEEF")
    assert not looks_like_link_code(generate_link_code().replace("-", ""))


def test_api_key_shape():
    key, key_hash, prefix = generate_api_key()
    assert key.startswith("ach_")
    assert key_hash == hash_token(key)
    assert prefix == key[:API_KEY_DISPLAY_PREFIX_LEN]
    assert len(prefix) == API_KEY_DISPLAY_PREFIX_LEN
