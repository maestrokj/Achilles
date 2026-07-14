"""ModelManager scheduling: coexistence, LRU eviction, rejection, supersede."""

import asyncio

import pytest

from app.manager import ModelTooLargeError
from app.memory import ModelEstimate

from .conftest import FakeEngine, make_manager, wait_state

EST = ModelEstimate(steady_bytes=30, peak_bytes=60)


async def test_models_coexist_when_they_fit(sizes):
    manager = make_manager(headroom=100)  # steady 30 + peak 60 ≤ 100
    sizes["a"] = sizes["b"] = EST
    await manager.load("a")
    await wait_state(manager, "a", "ready")
    await manager.load("b")
    await wait_state(manager, "b", "ready")
    assert manager.state_of("a") == "ready"
    assert not FakeEngine.instances["a"].stopped  # old model kept serving


async def test_lru_eviction_under_pressure(sizes):
    manager = make_manager(headroom=110)  # two fit (30 + 60 = 90); a third (120) does not
    sizes["a"] = sizes["b"] = sizes["c"] = EST
    await manager.load("a")
    await wait_state(manager, "a", "ready")
    await manager.load("b")
    await wait_state(manager, "b", "ready")
    manager.engine_for("a")  # touch: "b" becomes the LRU victim
    await manager.load("c")
    await wait_state(manager, "c", "ready")
    assert manager.state_of("b") == "not_loaded"
    assert FakeEngine.instances["b"].stopped
    assert manager.state_of("a") == "ready"


async def test_too_large_rejected_without_state_change(sizes):
    manager = make_manager(headroom=100)
    sizes["huge"] = ModelEstimate(steady_bytes=90, peak_bytes=180)
    with pytest.raises(ModelTooLargeError) as exc:
        await manager.load("huge")
    assert exc.value.budget_bytes == manager.budget_bytes
    assert manager.state_of("huge") == "not_loaded"
    assert manager.desired is None  # rejection precedes any mutation


async def test_unknown_size_evicts_everything_first(sizes):
    manager = make_manager(headroom=100)
    sizes["a"] = EST  # "mystery" stays absent from the table → unknown
    await manager.load("a")
    await wait_state(manager, "a", "ready")
    await manager.load("mystery")
    await wait_state(manager, "mystery", "ready")
    assert manager.state_of("a") == "not_loaded"
    assert FakeEngine.instances["a"].stopped


async def test_superseded_load_discards_itself(sizes):
    manager = make_manager(headroom=60)  # room for exactly one peak
    sizes["slow"] = sizes["fast"] = EST
    FakeEngine.gates["slow"] = asyncio.Event()
    await manager.load("slow")
    await asyncio.sleep(0.01)  # let the load task reach the gate
    await manager.load("fast")  # evicts "slow" mid-load
    await wait_state(manager, "fast", "ready")
    FakeEngine.gates["slow"].set()
    await asyncio.sleep(0.05)
    assert manager.state_of("slow") == "not_loaded"
    assert FakeEngine.instances["slow"].stopped
    assert manager.state_of("fast") == "ready"


async def test_load_failure_surfaces_error_state(sizes, monkeypatch):
    import app.manager as manager_module

    manager = make_manager(headroom=100)
    sizes["broken"] = EST

    def _boom(**kwargs):
        raise RuntimeError("weights corrupted")

    monkeypatch.setattr(manager_module, "EngineArgs", _boom)
    await manager.load("broken")
    await wait_state(manager, "broken", "error")
    assert manager.snapshot()["broken"]["error"] == "weights corrupted"


async def test_load_is_idempotent_while_loading(sizes):
    manager = make_manager(headroom=100)
    sizes["a"] = EST
    FakeEngine.gates["a"] = asyncio.Event()
    assert await manager.load("a") == "loading"
    assert await manager.load("a") == "loading"  # no duplicate task, no reset
    FakeEngine.gates["a"].set()
    await wait_state(manager, "a", "ready")
    assert await manager.load("a") == "ready"
