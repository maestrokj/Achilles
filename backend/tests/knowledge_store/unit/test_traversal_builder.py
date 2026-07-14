"""Recursive-CTE builder: depth bound, cycle guard, ACL slot, width-before-ACL (unit).

Assertions run against the compiled Postgres SQL — no database.
"""

import pytest
from sqlalchemy.dialects import postgresql

from achilles.knowledge_store.retrieval.traversal import build_traversal

pytestmark = [pytest.mark.unit]


def compile_sql(**kwargs: object) -> str:
    defaults: dict[str, object] = {"start_ids": [1, 2], "user_id": 7, "depth": 2}
    stmt = build_traversal(**{**defaults, **kwargs})  # type: ignore[arg-type]
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_traversal_is_a_recursive_cte():
    sql = compile_sql()
    assert "WITH RECURSIVE" in sql
    assert "walk" in sql


@pytest.mark.parametrize("depth", [0, 4, -1])
def test_depth_outside_bounds_is_rejected(depth: int):
    with pytest.raises(ValueError, match="depth"):
        build_traversal(start_ids=[1], user_id=7, depth=depth)


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_depth_within_bounds_builds(depth: int):
    assert compile_sql(depth=depth)


def test_direction_and_rel_type_drive_the_hop():
    sql = compile_sql(rel_types=["links_to", "child_of"])
    assert "src_entity_id" in sql
    assert "rel_type IN" in sql


def test_cycle_guard_uses_the_visited_path():
    sql = compile_sql()
    step = sql.split("UNION ALL")[1]
    assert "walk.path" in step  # visited set: a real cycle cannot loop the CTE


def test_acl_slot_is_present_in_both_members():
    seed, step = compile_sql().split("UNION ALL")
    assert "entity_acl" in seed  # start nodes are ACL-checked
    assert "entity_acl" in step  # a denied node breaks the path inside the recursion


def test_width_bounds_fire_inside_the_lateral_before_acl():
    step = compile_sql(weight_min=0.5).split("UNION ALL")[1]
    assert "LATERAL" in step
    lateral_part, acl_part = step.split("LATERAL", 1)[1].split("entity_acl", 1)
    assert "LIMIT" in lateral_part  # fanout cap
    assert "coalesce(entity_edge.weight" in lateral_part  # weight threshold
    del acl_part  # the ACL JOIN comes only after the bounded hop
