"""argon2id + password policy unit cases — tests.html (P0, unit)."""

import hashlib
import logging

import pytest
import respx
from httpx import Response, TimeoutException

from achilles.auth.security.passwords import (
    HIBP_RANGE_URL,
    dummy_verify,
    hash_password,
    is_password_compromised,
    validate_password,
    verify_password,
)
from tests.factories.users import DEFAULT_PASSWORD as STRONG_PASSWORD

pytestmark = [pytest.mark.unit, pytest.mark.p0]


def _hibp_route(respx_mock: respx.MockRouter, password: str, *, compromised: bool):
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    lines = ["0000000000000000000000000000000000A:5"]
    if compromised:
        lines.append(f"{suffix}:1337")
    return respx_mock.get(HIBP_RANGE_URL + prefix).mock(
        return_value=Response(200, text="\r\n".join(lines))
    )


def test_hash_and_verify():
    hashed = hash_password(STRONG_PASSWORD)
    assert hashed != STRONG_PASSWORD
    assert verify_password(hashed, STRONG_PASSWORD)


def test_wrong_password_fails():
    assert not verify_password(hash_password(STRONG_PASSWORD), "wrong-password")


def test_garbage_hash_fails_closed():
    assert not verify_password("not-an-argon2-hash", STRONG_PASSWORD)


def test_argon2_parameters_in_hash():
    assert "m=19456,t=2,p=1" in hash_password(STRONG_PASSWORD)


def test_dummy_verify_never_raises():
    dummy_verify()


@respx.mock
async def test_too_short(respx_mock: respx.MockRouter):
    assert await validate_password("Ab1!x2") == ["password must be at least 8 characters"]
    assert not respx_mock.calls, "cheap checks run before HIBP"


async def test_too_long():
    assert await validate_password("x" * 129) == ["password must be at most 128 characters"]


async def test_weak_rejected_by_zxcvbn():
    assert await validate_password("password123") == ["password is too weak"]


@respx.mock
async def test_compromised_rejected(respx_mock: respx.MockRouter):
    _hibp_route(respx_mock, STRONG_PASSWORD, compromised=True)
    assert await validate_password(STRONG_PASSWORD) == ["password appears in known breaches"]


@respx.mock
async def test_clean_password_passes(respx_mock: respx.MockRouter):
    _hibp_route(respx_mock, STRONG_PASSWORD, compromised=False)
    assert await validate_password(STRONG_PASSWORD) == []


@respx.mock
async def test_hibp_unavailable_fails_open(
    respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
):
    respx_mock.get(url__startswith=HIBP_RANGE_URL).mock(side_effect=TimeoutException("slow"))
    with caplog.at_level(logging.WARNING):
        assert not await is_password_compromised(STRONG_PASSWORD)
    assert any("HIBP unavailable" in r.message for r in caplog.records)
