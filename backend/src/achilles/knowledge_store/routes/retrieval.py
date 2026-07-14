"""Retrieval primitives: any authenticated account, ACL-filtered by construction.

Empty result → 200 + []; bad params → 422; 503 while a restore holds maintenance
or (vector only) while the embedder is silent — hybrid degrades instead. The
hidden-ACL hint is an internal Query Engine contract, not a public field.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends

from achilles.api.problems import ApiError
from achilles.auth.dependencies import CurrentUser
from achilles.db.dependencies import DbSession
from achilles.knowledge_store.constants import CODE_EMBEDDINGS_UNAVAILABLE
from achilles.knowledge_store.retrieval import graph, hybrid, lexical, sql, vector
from achilles.knowledge_store.retrieval.hits import Hit
from achilles.knowledge_store.schemas import (
    FusedHitOut,
    GraphQuery,
    HitOut,
    HybridOut,
    HybridQuery,
    LexicalQuery,
    SqlQuery,
    VectorQuery,
)
from achilles.knowledge_store.services.maintenance import ensure_not_maintenance

router = APIRouter(
    prefix="/retrieval", tags=["retrieval"], dependencies=[Depends(ensure_not_maintenance)]
)


def _out(hits: list[Hit]) -> list[HitOut]:
    return [HitOut(**asdict(h)) for h in hits]


@router.post("/lexical")
async def lexical_search(body: LexicalQuery, user: CurrentUser, session: DbSession) -> list[HitOut]:
    hits = await lexical.search(session, user_id=user.id, query=body.query, top_k=body.top_k)
    return _out(hits)


@router.post("/graph")
async def graph_search(body: GraphQuery, user: CurrentUser, session: DbSession) -> list[HitOut]:
    hits = await graph.search(
        session,
        user_id=user.id,
        start_ids=body.start_ids,
        depth=body.depth,
        rel_types=body.rel_types,  # StrEnum members are str
        weight_min=body.weight_min,
        top_k=body.top_k,
    )
    return _out(hits)


@router.post("/sql")
async def sql_search(body: SqlQuery, user: CurrentUser, session: DbSession) -> list[HitOut]:
    # SqlQuery is the wire face of SqlFilters: same fields plus top_k.
    filters = sql.SqlFilters(**body.model_dump(exclude={"top_k"}))
    hits = await sql.search(session, user_id=user.id, filters=filters, top_k=body.top_k)
    return _out(hits)


@router.post("/vector")
async def vector_search(body: VectorQuery, user: CurrentUser, session: DbSession) -> list[HitOut]:
    embedded = await hybrid.embed_query(session, body.query)
    if embedded is None:
        raise ApiError(
            503,
            CODE_EMBEDDINGS_UNAVAILABLE,
            "Embeddings unavailable",
            "no embedding model assigned or the runtime is unreachable",
        )
    query_vector, model_id = embedded
    hits = await vector.search(
        session,
        user_id=user.id,
        query_vector=query_vector,
        embedding_model=model_id,
        top_k=body.top_k,
    )
    return _out(hits)


@router.post("/hybrid")
async def hybrid_search(body: HybridQuery, user: CurrentUser, session: DbSession) -> HybridOut:
    filters = sql.SqlFilters(**body.filters.model_dump()) if body.filters is not None else None
    result = await hybrid.search(
        session, user_id=user.id, query=body.query, top_k=body.top_k, filters=filters
    )
    return HybridOut(
        hits=[FusedHitOut(**asdict(hit)) for hit in result.hits],
        degraded=result.degraded,
    )
