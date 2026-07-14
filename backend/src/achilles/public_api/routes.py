"""POST /public/v1/search — the external tier's only v1 endpoint (ask is v2)."""

from fastapi import APIRouter, Depends

from achilles.db.dependencies import DbSession
from achilles.knowledge_store.services.maintenance import ensure_not_maintenance
from achilles.public_api import service
from achilles.public_api.dependencies import RequestKey
from achilles.public_api.schemas import SearchIn, SearchOut

router = APIRouter(tags=["public"], dependencies=[Depends(ensure_not_maintenance)])


@router.post("/search")
async def search(body: SearchIn, principal: RequestKey, session: DbSession) -> SearchOut:
    return await service.search_for_key(
        session,
        user_id=principal.user.id,
        scope=principal.scope,
        query=body.query,
        limit=body.limit,
    )
