"""Model lifecycle: load, memory-aware eviction, desired-model persistence.

Models stay resident as long as they fit the memory budget — a switch on a
roomy host is seamless (the old model serves until the new one is READY) and
only a tight host falls back to evict-then-load. The one model an /admin/load
asked for last is "desired": it is never evicted, it is persisted to the cache
volume, and the runtime resumes loading it after a restart.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from itertools import count
from pathlib import Path

from huggingface_hub import snapshot_download
from infinity_emb import AsyncEmbeddingEngine, EngineArgs

from app.memory import (
    BASE_PROCESS_BYTES,
    ModelEstimate,
    estimate_model_bytes,
    read_memory_budget,
)

logger = logging.getLogger("embeddings")

# fp32 is the safe default. float16 halves the weights but torch CPU kernels
# emulate half precision — inference slows an order of magnitude (measured: a
# 49-chunk batch blew the backend's 120s budget), so shrink only when RAM, not
# time, is the binding constraint.
_DTYPE = os.environ.get("EMBEDDINGS_DTYPE", "float32")

# Survives restarts on the HF cache volume next to the weights it points at.
_DESIRED_PATH = Path(
    os.environ.get("EMBEDDINGS_DESIRED_PATH", "/cache/desired_model.json")
)

# Formats the torch backend never reads — pulling them would double the download.
_SKIP_WEIGHT_PATTERNS = ["onnx/*", "*.onnx", "openvino/*", "*.h5", "*.msgpack"]


class ModelState(StrEnum):
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


class ModelTooLargeError(Exception):
    """The model cannot fit the budget even with every other model evicted."""

    def __init__(self, required_bytes: int, budget_bytes: int) -> None:
        super().__init__(f"requires ~{required_bytes} of {budget_bytes} budget")
        self.required_bytes = required_bytes
        self.budget_bytes = budget_bytes


@dataclass(frozen=True, slots=True)
class FitReport:
    fits: bool
    required_bytes: int | None  # None when the size is unknown
    budget_bytes: int


class ModelManager:
    def __init__(self, budget_bytes: int | None = None) -> None:
        self._engines: dict[str, AsyncEmbeddingEngine] = {}
        self._states: dict[str, ModelState] = {}
        self._errors: dict[str, str] = {}
        self._estimates: dict[str, ModelEstimate] = {}
        self._last_used: dict[str, int] = {}  # LRU clock, not wall time
        self._clock = count()
        self._desired: str | None = None
        self._lock = asyncio.Lock()
        self._budget = (
            budget_bytes if budget_bytes is not None else read_memory_budget()
        )

    # --- public surface -------------------------------------------------

    @property
    def budget_bytes(self) -> int:
        return self._budget

    @property
    def desired(self) -> str | None:
        return self._desired

    def engine_for(self, model_id: str) -> AsyncEmbeddingEngine | None:
        engine = self._engines.get(model_id)
        if engine is not None:
            self._last_used[model_id] = next(self._clock)
        return engine

    def state_of(self, model_id: str) -> str:
        state = self._states.get(model_id)
        return state.value if state else "not_loaded"

    def snapshot(self) -> dict[str, dict[str, str | None]]:
        """Per-model state + error for /admin/status and /healthz."""
        return {
            model_id: {"state": state.value, "error": self._errors.get(model_id)}
            for model_id, state in self._states.items()
        }

    def check_fit(self, model_id: str) -> FitReport:
        """Would `model_id` fit with everything else evicted? Pure read."""
        estimate = self._estimates.get(model_id) or estimate_model_bytes(
            model_id, _DTYPE
        )
        if estimate is None:
            return FitReport(fits=True, required_bytes=None, budget_bytes=self._budget)
        required = BASE_PROCESS_BYTES + estimate.peak_bytes
        return FitReport(
            fits=required <= self._budget,
            required_bytes=required,
            budget_bytes=self._budget,
        )

    async def load(self, model_id: str) -> str:
        """Start loading (idempotent); returns the model's state after the call.

        Raises ModelTooLargeError — before any state change — when the model
        cannot fit even alone.
        """
        estimate = estimate_model_bytes(
            model_id, _DTYPE
        )  # network I/O — outside the lock
        if (
            estimate is not None
            and BASE_PROCESS_BYTES + estimate.peak_bytes > self._budget
        ):
            raise ModelTooLargeError(
                BASE_PROCESS_BYTES + estimate.peak_bytes, self._budget
            )
        async with self._lock:
            self._set_desired(model_id)
            if estimate is not None:
                self._estimates[model_id] = estimate
            state = self._states.get(model_id)
            if state is ModelState.READY or state is ModelState.LOADING:
                return state.value
            await self._make_room(model_id, estimate)
            self._errors.pop(model_id, None)
            self._states[model_id] = ModelState.LOADING
            asyncio.get_running_loop().create_task(self._load_engine(model_id))
        return ModelState.LOADING.value

    async def resume_desired(self) -> None:
        """Reload the persisted desired model after a restart (best-effort)."""
        model_id = self._read_desired()
        if model_id is None:
            return
        logger.info("resuming desired model %s", model_id)
        try:
            await self.load(model_id)
        except ModelTooLargeError as exc:
            # A budget shrink between runs must not crash the service; the
            # backend re-asserts fit on the next assignment.
            logger.error("desired model %s no longer fits: %s", model_id, exc)

    async def shutdown(self) -> None:
        for engine in self._engines.values():
            await engine.astop()

    # --- internals --------------------------------------------------------

    def _set_desired(self, model_id: str) -> None:
        self._desired = model_id
        try:
            tmp = _DESIRED_PATH.with_suffix(".tmp")
            _DESIRED_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({"model_id": model_id}))
            os.replace(tmp, _DESIRED_PATH)
        except OSError as exc:  # read-only volume degrades to no restart-resume
            logger.warning("cannot persist desired model: %s", exc)

    def _read_desired(self) -> str | None:
        try:
            raw = json.loads(_DESIRED_PATH.read_text())
            model_id = raw.get("model_id")
            return model_id if isinstance(model_id, str) and model_id else None
        except (OSError, ValueError):
            return None

    async def _make_room(self, incoming: str, estimate: ModelEstimate | None) -> None:
        """Evict LRU non-desired residents until the incoming model fits.

        Caller holds `_lock`. Unknown size → evict everything else: staying
        conservative keeps the pre-refactor single-model behavior instead of
        gambling the container on a guess.
        """
        if estimate is None:
            for model_id in [m for m in self._states if m != incoming]:
                await self._evict_one(model_id)
            return
        while self._planned_bytes(incoming, estimate) > self._budget:
            victim = self._lru_victim(exclude=incoming)
            if victim is None:
                # Nothing left to evict; the alone-fit was checked in load().
                return
            await self._evict_one(victim)

    def _planned_bytes(self, incoming: str, estimate: ModelEstimate) -> int:
        resident = sum(
            self._estimates[m].steady_bytes
            for m in self._states
            if m != incoming and m in self._estimates
        )
        unknown = [
            m for m in self._states if m != incoming and m not in self._estimates
        ]
        if unknown:
            # Residents of unknown size can't be accounted — treat them as
            # not fitting so they get evicted first.
            return self._budget + 1
        return BASE_PROCESS_BYTES + resident + estimate.peak_bytes

    def _lru_victim(self, exclude: str) -> str | None:
        candidates = [m for m in self._states if m != exclude and m != self._desired]
        if not candidates:
            return None
        return min(candidates, key=lambda m: self._last_used.get(m, -1))

    async def _evict_one(self, model_id: str) -> None:
        engine = self._engines.pop(model_id, None)
        if engine is not None:
            try:
                await engine.astop()
            except Exception:
                logger.exception("failed to stop evicted engine %s", model_id)
        # A model still mid-load sees its LOADING state vanish and discards
        # itself in _load_engine, so a superseded load cannot resurrect.
        self._states.pop(model_id, None)
        self._estimates.pop(model_id, None)
        self._last_used.pop(model_id, None)
        self._errors.pop(model_id, None)
        logger.info("evicted model %s", model_id)

    async def _load_engine(self, model_id: str) -> None:
        try:
            # The download is minutes of blocking I/O — keep it off the event
            # loop so /healthz and /v1/embeddings stay alive while weights
            # arrive. astart() loads in infinity's own background threads.
            await asyncio.to_thread(
                snapshot_download, model_id, ignore_patterns=_SKIP_WEIGHT_PATTERNS
            )
            engine = AsyncEmbeddingEngine.from_args(
                EngineArgs(
                    model_name_or_path=model_id,
                    engine="torch",
                    device="cpu",
                    dtype=_DTYPE,
                    # BetterTransformer is gone from modern transformers; the
                    # flag defaults to True and crashes the torch backend.
                    bettertransformer=False,
                    # Infinity's self-benchmark (batch 32 × 513 tokens) costs
                    # ~40s of CPU and a memory spike per load and warms shapes
                    # the first real call recompiles anyway — our own warm-up
                    # below is enough.
                    model_warmup=False,
                )
            )
            await engine.astart()
            await engine.embed(sentences=["warm-up"])  # weights hot before real traffic
        except Exception as exc:
            logger.exception("failed to load %s", model_id)
            async with self._lock:
                # Only mark ERROR if this load is still the wanted one — an
                # eviction mid-load cleared the state on purpose.
                if self._states.get(model_id) is ModelState.LOADING:
                    self._states[model_id] = ModelState.ERROR
                    self._errors[model_id] = str(exc) or exc.__class__.__name__
            return
        async with self._lock:
            # A newer switch may have evicted us mid-load (our LOADING state
            # was cleared). Honour it — registering now would resurrect a
            # model nobody wants and leak its weights past the next eviction.
            if self._states.get(model_id) is not ModelState.LOADING:
                await engine.astop()
                logger.info("model %s loaded but superseded — discarded", model_id)
                return
            self._engines[model_id] = engine
            self._states[model_id] = ModelState.READY
            self._last_used[model_id] = next(self._clock)
        logger.info("model %s loaded and warm", model_id)
