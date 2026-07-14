"""Memory budget + model-size estimation for the embeddings runtime.

The runtime keeps models resident as long as they fit and evicts only under
pressure, so it needs two numbers: how much memory this container may use and
how much a model will take. Both are estimates by design — accounting runs on
them rather than on live RSS, because glibc keeps freed engine memory mapped
after astop() and a live reading would understate the headroom right after an
eviction.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.constants import HF_HUB_CACHE

logger = logging.getLogger("embeddings")

# Ops escape hatch (and the test hook): overrides any cgroup/host detection.
_BUDGET_ENV = "EMBEDDINGS_MEMORY_BUDGET_BYTES"

# Never plan up to the hard cap: the allocator, CUDA-free torch pools and the
# HTTP layer all breathe outside the model accounting.
BUDGET_SAFETY_FRACTION = 0.9

# Process floor before any model: python + torch + tokenizer pools. Measured
# ~1.5G on the fp16 dev image; rounded up.
BASE_PROCESS_BYTES = 1_600_000_000

# Loading transiently holds ~2x the steady weights (torch materializes the
# checkpoint, then casts to the target dtype before the source is dropped).
LOAD_PEAK_FACTOR = 2.0

# Steady state above raw weights: activations, dynamic-batching buffers,
# tokenizer state. fp16 0.6B model: ~1.2G weights → ~2.9G resident beyond base
# would be too pessimistic; 1.3 matches the measured envelope with headroom.
RUNTIME_OVERHEAD_FACTOR = 1.3

_BYTES_PER_PARAM = {"float32": 4, "float16": 2, "bfloat16": 2}
_DEFAULT_PARAM_BYTES = 4

# Weight files worth counting on disk / in Hub metadata (complements the
# download filter in manager.py — formats the torch backend actually reads).
_WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt")


@dataclass(frozen=True, slots=True)
class ModelEstimate:
    """Bytes a model costs: resident after load vs the transient load peak."""

    steady_bytes: int
    peak_bytes: int


def read_memory_budget() -> int:
    """Usable bytes for this runtime: env override → cgroup limit → host total."""
    override = os.environ.get(_BUDGET_ENV)
    if override:
        return int(int(override) * BUDGET_SAFETY_FRACTION)
    limit = _read_cgroup_limit()
    if limit is None:
        limit = _read_host_total()
    return int(limit * BUDGET_SAFETY_FRACTION)


def _read_cgroup_limit() -> int | None:
    # cgroup v2, then v1; "max" / the v1 no-limit sentinel mean "no cap here".
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            raw = Path(path).read_text().strip()
        except OSError:
            continue
        if raw == "max":
            return None
        value = int(raw)
        if value >= 1 << 60:  # v1 reports "unlimited" as a huge page-aligned number
            return None
        return value
    return None


def _read_host_total() -> int:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    # macOS / exotic hosts (dev outside Docker): sysconf is the portable floor.
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def estimate_model_bytes(model_id: str, dtype: str) -> ModelEstimate | None:
    """Estimate a model's memory cost; None = unknown (caller stays conservative).

    Cascade: Hub parameter count (exact) → Hub weight-file sizes (scaled to the
    target dtype) → cached snapshot size on disk. Every path multiplies by the
    runtime overhead and load-peak factors above.
    """
    weights = _weights_from_hub(model_id, dtype)
    if weights is None:
        weights = _weights_from_cache(model_id, dtype)
    if weights is None:
        return None
    steady = int(weights * RUNTIME_OVERHEAD_FACTOR)
    # The load peak is checkpoint + weights coexisting; the inference buffers
    # behind RUNTIME_OVERHEAD_FACTOR don't exist yet at that moment, so
    # applying it here double-counts (measured fp32 0.6B peak ≈ 5.2G vs 7.8G
    # estimated with it). The max() keeps peak ≥ steady should the factors flip.
    peak = max(steady, int(weights * LOAD_PEAK_FACTOR))
    return ModelEstimate(steady_bytes=steady, peak_bytes=peak)


def _weights_from_hub(model_id: str, dtype: str) -> int | None:
    try:
        info = HfApi().model_info(model_id, files_metadata=True)
    except Exception as exc:  # Hub down / air-gapped / bad id — fall through
        logger.info("hub metadata unavailable for %s: %s", model_id, exc)
        return None
    params = getattr(getattr(info, "safetensors", None), "total", None)
    if params:
        return params * _BYTES_PER_PARAM.get(dtype, _DEFAULT_PARAM_BYTES)
    sizes = [
        s.size
        for s in info.siblings or []
        if s.size and s.rfilename.endswith(_WEIGHT_SUFFIXES)
    ]
    if not sizes:
        return None
    # Checkpoints ship fp32 unless stated otherwise; loading at fp16 halves them.
    ratio = _BYTES_PER_PARAM.get(dtype, _DEFAULT_PARAM_BYTES) / _DEFAULT_PARAM_BYTES
    return int(sum(sizes) * ratio)


def _weights_from_cache(model_id: str, dtype: str) -> int | None:
    cache_dir = Path(HF_HUB_CACHE) / f"models--{model_id.replace('/', '--')}"
    if not cache_dir.is_dir():
        return None
    total = sum(
        f.stat().st_size
        for f in cache_dir.rglob("*")
        if f.is_file() and f.name.endswith(_WEIGHT_SUFFIXES)
    )
    if total == 0:
        return None
    ratio = _BYTES_PER_PARAM.get(dtype, _DEFAULT_PARAM_BYTES) / _DEFAULT_PARAM_BYTES
    return int(total * ratio)
