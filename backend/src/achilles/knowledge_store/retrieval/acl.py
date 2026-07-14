"""The one ACL pre-filter every primitive composes (hybrid-search.html#acl-prefilter).

Resolve chain: users → identity.user_id → source_principal.identity_id →
group_membership → source_group → entity_acl → entity. Shortcuts: a direct
principal grant bypasses groups; scope='public' lands in the filter directly
(no synthetic "everyone" group). Rights are applied inside the search SQL —
Query Engine passes the caller, KS runs the join.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.roles import ExpressionElementRole

from achilles.knowledge_store.constants import AclScope
from achilles.knowledge_store.models import (
    EntityAcl,
    GroupMembership,
    Identity,
    SourcePrincipal,
)


def source_scope(
    source_id_col: InstrumentedAttribute[int], allowed_source_ids: Sequence[int] | None
) -> tuple[ColumnElement[bool], ...]:
    """API-key scope: zero or one predicate for the same slot as the ACL.

    None means the key is unscoped. The restriction composes inside each
    primitive's SQL — a post-filter would distort the fused top_k — and in
    the graph walk an out-of-scope node breaks the path like a denied one.
    """
    if allowed_source_ids is None:
        return ()
    return (source_id_col.in_(allowed_source_ids),)


def acl_prefilter(entity_id_col: ExpressionElementRole[int], user_id: int) -> ColumnElement[bool]:
    """EXISTS clause: the user can see the entity behind `entity_id_col`."""
    my_principals = (
        sa.select(SourcePrincipal.id)
        .join(Identity, SourcePrincipal.identity_id == Identity.id)
        .where(Identity.user_id == user_id)
    )
    my_groups = sa.select(GroupMembership.source_group_id).where(
        GroupMembership.source_principal_id.in_(my_principals)
    )
    return sa.exists().where(
        EntityAcl.entity_id == entity_id_col,
        sa.or_(
            EntityAcl.scope == str(AclScope.PUBLIC),
            EntityAcl.source_principal_id.in_(my_principals),
            EntityAcl.source_group_id.in_(my_groups),
        ),
    )
