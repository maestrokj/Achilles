"""AI registry HTTP contract: CRUD, locks, dictionaries, access (tests.html, P1)."""

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.ai_foundation.constants import EMBEDDING_DIM
from achilles.auth.constants import UserRole
from tests.auth.integration.conftest import AuthorizeFn
from tests.factories.ai import create_model, create_provider, get_builtin_model
from tests.factories.users import create_user

pytestmark = [pytest.mark.api, pytest.mark.p1]

BASE = "/api/v1/admin/ai"


def _entry(model_id: int, *, enabled: bool = True) -> dict[str, object]:
    """One chat/agent allow-list item as the assignments API expects it."""
    return {"id": model_id, "is_enabled": enabled}


@pytest.fixture
async def as_member(db_session: AsyncSession, authorize: AuthorizeFn) -> None:
    member = await create_user(db_session, role=UserRole.MEMBER.value)
    await authorize(member.email)


# --- Providers ---


async def test_provider_crud_roundtrip(client: AsyncClient, as_admin: None):
    created = await client.post(
        f"{BASE}/providers",
        json={"name": "OpenAI", "adapter": "openai", "api_key": "sk-test-1234abcd"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["api_key_mask"] == "••••abcd"  # write-only: only the mask comes back
    assert "api_key" not in body
    provider_id = body["id"]

    listed = await client.get(f"{BASE}/providers")
    assert listed.status_code == 200
    assert {p["name"] for p in listed.json()} == {"Platform", "OpenAI"}

    patched = await client.patch(f"{BASE}/providers/{provider_id}", json={"name": "OpenAI 2"})
    assert patched.status_code == 200
    assert patched.json()["name"] == "OpenAI 2"

    deleted = await client.delete(f"{BASE}/providers/{provider_id}")
    assert deleted.status_code == 204


async def test_local_provider_without_base_url_is_422(client: AsyncClient, as_admin: None):
    resp = await client.post(
        f"{BASE}/providers", json={"name": "Local", "kind": "local", "adapter": "ollama"}
    )
    assert resp.status_code == 422


async def test_unknown_adapter_is_422(client: AsyncClient, as_admin: None):
    resp = await client.post(f"{BASE}/providers", json={"name": "X", "adapter": "webhook"})
    assert resp.status_code == 422


async def test_system_provider_delete_is_409(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    platform_id = (await get_builtin_model(db_session)).provider_id
    resp = await client.delete(f"{BASE}/providers/{platform_id}")
    assert resp.status_code == 409
    assert resp.json()["code"] == "SYSTEM_PROVIDER_PROTECTED"


async def test_system_provider_base_url_patch_is_422(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    platform_id = (await get_builtin_model(db_session)).provider_id
    resp = await client.patch(
        f"{BASE}/providers/{platform_id}", json={"base_url": "http://rogue:80"}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "SYSTEM_PROVIDER_PROTECTED"


async def test_unknown_provider_is_404(client: AsyncClient, as_admin: None):
    assert (await client.get(f"{BASE}/providers/99999")).status_code == 404


# --- Models ---


async def test_model_create_defaults_display_name(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    provider = await create_provider(db_session)
    resp = await client.post(
        f"{BASE}/models",
        json={"provider_id": provider.id, "model_id": "gpt-4o", "model_type": "chat"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["display_name"] == "gpt-4o"
    assert body["origin"] == "manual"
    assert body["is_enabled"] is True


async def test_model_create_accepts_discovered_origin(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    provider = await create_provider(db_session)
    resp = await client.post(
        f"{BASE}/models",
        json={
            "provider_id": provider.id,
            "model_id": "gpt-4o",
            "model_type": "chat",
            "origin": "discovered",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["origin"] == "discovered"


async def test_model_create_builtin_origin_is_422(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    provider = await create_provider(db_session)
    resp = await client.post(
        f"{BASE}/models",
        json={
            "provider_id": provider.id,
            "model_id": "gpt-4o",
            "model_type": "chat",
            "origin": "builtin",
        },
    )
    assert resp.status_code == 422


async def test_model_type_change_of_free_model(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    model = await create_model(db_session, model_type="chat")
    resp = await client.patch(f"{BASE}/models/{model.id}", json={"model_type": "embedding"})
    assert resp.status_code == 200
    assert resp.json()["model_type"] == "embedding"


async def test_model_type_change_of_used_model_is_409(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    model = await create_model(db_session, model_type="chat")
    listed = await client.patch(
        f"{BASE}/assignments",
        json={"chat_models": {"items": [_entry(model.id)], "default": model.id}},
    )
    assert listed.status_code == 200

    resp = await client.patch(f"{BASE}/models/{model.id}", json={"model_type": "embedding"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "MODEL_IN_USE"


async def test_model_meta_patch_merges(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    """Editing one intrinsic keeps the others — meta is merged, not replaced."""
    model = await create_model(
        db_session,
        model_type="embedding",
        meta={"embedding_dim": 1024, "max_input_tokens": 8192},
    )
    resp = await client.patch(f"{BASE}/models/{model.id}", json={"meta": {"embedding_dim": 768}})
    assert resp.status_code == 200
    assert resp.json()["meta"] == {"embedding_dim": 768, "max_input_tokens": 8192}


async def test_model_on_unknown_provider_is_404(client: AsyncClient, as_admin: None):
    resp = await client.post(
        f"{BASE}/models",
        json={"provider_id": 99999, "model_id": "gpt-4o", "model_type": "chat"},
    )
    assert resp.status_code == 404


async def test_duplicate_model_is_409(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    model = await create_model(db_session)
    resp = await client.post(
        f"{BASE}/models",
        json={
            "provider_id": model.provider_id,
            "model_id": model.model_id,
            "model_type": "chat",
        },
    )
    assert resp.status_code == 409


async def test_assigned_model_delete_is_409(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    builtin = await get_builtin_model(db_session)
    assign = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert assign.status_code == 200

    resp = await client.delete(f"{BASE}/models/{builtin.id}")
    assert resp.status_code == 409
    assert resp.json()["code"] == "MODEL_IN_USE"


async def test_disable_of_assigned_model_is_409(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    builtin = await get_builtin_model(db_session)
    await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})

    resp = await client.patch(f"{BASE}/models/{builtin.id}", json={"is_enabled": False})
    assert resp.status_code == 409
    assert resp.json()["code"] == "MODEL_IN_USE"


async def test_provider_with_used_model_delete_is_409(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    model = await create_model(db_session)
    listed = await client.patch(
        f"{BASE}/assignments",
        json={"chat_models": {"items": [_entry(model.id)], "default": model.id}},
    )
    assert listed.status_code == 200

    resp = await client.delete(f"{BASE}/providers/{model.provider_id}")
    assert resp.status_code == 409
    assert resp.json()["code"] == "MODEL_IN_USE"


# --- Assignments ---


async def test_assignments_start_empty(client: AsyncClient, as_admin: None):
    resp = await client.get(f"{BASE}/assignments")
    assert resp.status_code == 200
    assert resp.json() == {
        "harvester_embedding": None,
        "chat_models": {"items": [], "default": None},
        "agent_models": {"items": [], "default": None},
        "embedding_dim": EMBEDDING_DIM,
    }


async def test_assignment_type_gating(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    chat = await create_model(db_session, model_type="chat")
    embedding = await get_builtin_model(db_session)

    wrong_embed = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": chat.id})
    assert wrong_embed.status_code == 422
    assert wrong_embed.json()["code"] == "MODEL_TYPE_MISMATCH"

    wrong_chat = await client.patch(
        f"{BASE}/assignments",
        json={
            "chat_models": {
                "items": [_entry(embedding.id)],
                "default": embedding.id,
            }
        },
    )
    assert wrong_chat.status_code == 422
    assert wrong_chat.json()["code"] == "MODEL_TYPE_MISMATCH"


async def test_assignment_roundtrip_and_unassign(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    builtin = await get_builtin_model(db_session)
    assigned = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": builtin.id})
    assert assigned.status_code == 200
    assert assigned.json()["harvester_embedding"] == builtin.id

    # The assignment kicked a re-embed run; while it is active the function is
    # locked (REEMBED_IN_PROGRESS) — let it finish before the unassign.
    await _finish_reembed_runs(db_session)

    cleared = await client.patch(f"{BASE}/assignments", json={"harvester_embedding": None})
    assert cleared.status_code == 200
    assert cleared.json()["harvester_embedding"] is None


async def _finish_reembed_runs(db_session: AsyncSession) -> None:
    await db_session.execute(
        sa.text("UPDATE curation_runs SET state = 'succeeded', finished_at = now()")
    )
    await db_session.commit()


async def test_chat_list_needs_default(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    model = await create_model(db_session)
    resp = await client.patch(
        f"{BASE}/assignments",
        json={"chat_models": {"items": [_entry(model.id)]}},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "LAST_DEFAULT_PROTECTED"


async def test_chat_default_swap(client: AsyncClient, db_session: AsyncSession, as_admin: None):
    first = await create_model(db_session)
    second = await create_model(db_session)
    items = [_entry(first.id), _entry(second.id)]

    initial = await client.patch(
        f"{BASE}/assignments", json={"chat_models": {"items": items, "default": first.id}}
    )
    assert initial.status_code == 200
    assert initial.json()["chat_models"]["default"] == first.id

    swapped = await client.patch(
        f"{BASE}/assignments", json={"chat_models": {"items": items, "default": second.id}}
    )
    assert swapped.status_code == 200
    assert swapped.json()["chat_models"] == {"items": items, "default": second.id}


async def test_paused_entry_stays_but_cannot_be_default(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    """Pausing keeps a model on the list; a paused model can never be the default."""
    first = await create_model(db_session)
    second = await create_model(db_session)
    items = [_entry(first.id, enabled=False), _entry(second.id)]

    ok = await client.patch(
        f"{BASE}/assignments", json={"chat_models": {"items": items, "default": second.id}}
    )
    assert ok.status_code == 200
    assert ok.json()["chat_models"] == {"items": items, "default": second.id}

    bad = await client.patch(
        f"{BASE}/assignments", json={"chat_models": {"items": items, "default": first.id}}
    )
    assert bad.status_code == 422


async def test_disabled_model_cannot_be_assigned(
    client: AsyncClient, db_session: AsyncSession, as_admin: None
):
    model = await create_model(db_session, is_enabled=False)
    resp = await client.patch(
        f"{BASE}/assignments",
        json={"chat_models": {"items": [_entry(model.id)], "default": model.id}},
    )
    assert resp.status_code == 422


# --- Access ---

_WRITE_CALLS = [
    ("post", "/providers"),
    ("post", "/providers/check-config"),
    ("patch", "/providers/1"),
    ("delete", "/providers/1"),
    ("post", "/models"),
    ("patch", "/models/1"),
    ("delete", "/models/1"),
    ("patch", "/assignments"),
]


@pytest.mark.parametrize(("method", "path"), _WRITE_CALLS)
async def test_anonymous_is_401(client: AsyncClient, method: str, path: str):
    resp = await client.request(method, f"{BASE}{path}", json={})
    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path"), [*_WRITE_CALLS, ("get", "/providers")])
async def test_member_is_403(client: AsyncClient, as_member: None, method: str, path: str):
    resp = await client.request(method, f"{BASE}{path}", json={})
    assert resp.status_code == 403
