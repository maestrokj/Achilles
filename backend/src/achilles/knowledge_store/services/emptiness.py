"""Progressive-value property (hybrid-search.html#emptiness).

`is_empty` is derived — true iff there are no live fragments to search. Never a
stored flag: the first accepted chunk flips it automatically. Consumers (Query
Engine / Agent Engine harness, stage 4+) drop KS tools from the model schema
while the store is empty.
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from achilles.knowledge_store.models import Chunk


async def is_empty(session: AsyncSession) -> bool:
    has_chunks = await session.scalar(
        sa.select(sa.exists().where(sa.not_(Chunk.is_deleted)).select_from(Chunk))
    )
    return not has_chunks
