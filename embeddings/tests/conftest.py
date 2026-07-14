"""Hermetic fixtures: no Hub, no torch — a fake engine and scripted sizes.

The manager's contract under test is scheduling (evict/coexist/supersede),
not inference; every heavy dependency is replaced at the module seam.
"""

import asyncio
from types import SimpleNamespace

import pytest

import app.manager as manager_module
from app.manager import ModelManager
from app.memory import BASE_PROCESS_BYTES, ModelEstimate


class FakeEngine:
    """Stands in for AsyncEmbeddingEngine; optionally blocks in astart."""

    instances: dict[str, "FakeEngine"] = {}
    gates: dict[str, asyncio.Event] = {}

    def __init__(self, args) -> None:
        self.args = args
        self.model_id = args.model_name_or_path
        self.stopped = False
        FakeEngine.instances[self.model_id] = self

    @classmethod
    def from_args(cls, args) -> "FakeEngine":
        return cls(args)

    async def astart(self) -> None:
        gate = FakeEngine.gates.get(self.model_id)
        if gate is not None:
            await gate.wait()

    async def astop(self) -> None:
        self.stopped = True

    async def embed(self, sentences):
        return [[0.0, 1.0, 2.0, 3.0] for _ in sentences], len(sentences)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    FakeEngine.instances = {}
    FakeEngine.gates = {}
    monkeypatch.setattr(manager_module, "AsyncEmbeddingEngine", FakeEngine)
    monkeypatch.setattr(
        manager_module, "EngineArgs", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(manager_module, "snapshot_download", lambda *a, **k: None)
    monkeypatch.setattr(
        manager_module, "_DESIRED_PATH", tmp_path / "desired_model.json"
    )


@pytest.fixture
def sizes(monkeypatch):
    """Scripted per-model estimates; missing id → unknown size (None)."""
    table: dict[str, ModelEstimate | None] = {}
    monkeypatch.setattr(
        manager_module,
        "estimate_model_bytes",
        lambda model_id, dtype: table.get(model_id),
    )
    return table


def make_manager(headroom: int) -> ModelManager:
    """Manager whose budget leaves `headroom` bytes above the process base."""
    return ModelManager(budget_bytes=BASE_PROCESS_BYTES + headroom)


async def wait_state(
    manager: ModelManager, model_id: str, state: str, timeout: float = 2.0
):
    async def _poll() -> None:
        while manager.state_of(model_id) != state:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout)
