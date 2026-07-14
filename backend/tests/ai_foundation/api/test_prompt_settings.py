"""Prompt AI HTTP contract: effective text, override, reset (admin-panel tests.html)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.prompt_texts import DEFAULT_PROMPTS
from achilles.auth.constants import UserRole
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

URL = "/api/v1/admin/ai-prompt"

# The built-in default follows platform_settings.locale; the seed is 'ru'.
SEED_LOCALE = "ru"


async def test_get_answers_effective_defaults(client: AsyncClient, as_admin: None):
    resp = await client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    defaults = DEFAULT_PROMPTS[SEED_LOCALE]
    assert body["safety"] == {"text": defaults["safety"], "is_default": True}
    assert body["org"] == {"text": defaults["org"], "is_default": True}


async def test_override_and_reset(client: AsyncClient, as_admin: None):
    overridden = await client.patch(URL, json={"org_text": "You serve {org_name} only."})
    assert overridden.status_code == 200
    body = overridden.json()
    assert body["org"] == {"text": "You serve {org_name} only.", "is_default": False}
    assert body["safety"]["is_default"] is True  # untouched field stays default

    reset = await client.patch(URL, json={"org_text": None})
    assert reset.status_code == 200
    assert reset.json()["org"]["is_default"] is True


async def test_empty_string_means_reset(client: AsyncClient, as_admin: None):
    await client.patch(URL, json={"safety_text": "Custom rule."})
    reset = await client.patch(URL, json={"safety_text": ""})
    assert reset.status_code == 200
    assert reset.json()["safety"]["is_default"] is True


async def test_unknown_placeholder_is_422(client: AsyncClient, as_admin: None):
    resp = await client.patch(URL, json={"org_text": "Hello {intruder}"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "UNKNOWN_PLACEHOLDER"


async def test_unknown_field_is_422(client: AsyncClient, as_admin: None):
    resp = await client.patch(URL, json={"engineering_text": "no"})
    assert resp.status_code == 422


async def test_singleton_has_no_post_or_delete(client: AsyncClient, as_admin: None):
    assert (await client.post(URL, json={})).status_code == 405
    assert (await client.delete(URL)).status_code == 405


async def test_member_is_403(client: AsyncClient, db_session: AsyncSession, authorize: AuthorizeFn):
    member = await create_user(db_session, role=UserRole.MEMBER.value)
    await authorize(member.email)
    assert (await client.get(URL)).status_code == 403


async def test_anonymous_is_401(client: AsyncClient):
    assert (await client.get(URL)).status_code == 401
