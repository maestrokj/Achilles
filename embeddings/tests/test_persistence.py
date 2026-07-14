"""Desired-model persistence: a restart resumes what the last load asked for."""

import json

import app.manager as manager_module
from app.memory import ModelEstimate

from .conftest import make_manager, wait_state

EST = ModelEstimate(steady_bytes=30, peak_bytes=60)


async def test_restart_resumes_the_desired_model(sizes):
    sizes["a"] = EST
    first = make_manager(headroom=100)
    await first.load("a")
    await wait_state(first, "a", "ready")

    second = make_manager(headroom=100)  # same desired file, fresh process
    await second.resume_desired()
    await wait_state(second, "a", "ready")
    assert second.desired == "a"


async def test_corrupt_desired_file_is_ignored(sizes):
    manager_module._DESIRED_PATH.write_text("{not json")
    manager = make_manager(headroom=100)
    await manager.resume_desired()
    assert manager.snapshot() == {}


async def test_missing_desired_file_is_a_noop(sizes):
    manager = make_manager(headroom=100)
    await manager.resume_desired()
    assert manager.snapshot() == {}


async def test_too_large_on_resume_logs_instead_of_raising(sizes):
    manager_module._DESIRED_PATH.write_text(json.dumps({"model_id": "huge"}))
    sizes["huge"] = ModelEstimate(steady_bytes=90, peak_bytes=180)
    manager = make_manager(headroom=100)
    await manager.resume_desired()  # must not raise
    assert manager.state_of("huge") == "not_loaded"
