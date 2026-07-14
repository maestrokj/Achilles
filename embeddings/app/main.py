"""Built-in embeddings runtime (knowledge-store/embedding-runtime.html).

One logical service behind the Platform provider's base_url:
- POST /v1/embeddings — OpenAI-compatible inference over loaded models;
- GET /v1/models — OpenAI-compatible catalog of ready models; the registry's
  provider check/discovery speaks this dialect, so the endpoint must exist
  even when the answer is an empty list (reachable ≠ loaded);
- POST /admin/load — lazy weight pull + warm-up, answers immediately and
  loads in the background (assignment must not hang on a download); 409 when
  the model cannot fit the memory budget even alone;
- POST /admin/preflight — pure fit check, no state change;
- GET /admin/status — memory budget + per-model state/error for the backend;
- GET /healthz — pure liveness for the Docker healthcheck (a busy or loading
  runtime is alive; only a dead process should trip autoheal).

Weights land in the HF cache volume (HF_HOME); nothing downloads until the
first load request — the deploy stays weightless until an Admin assigns a
model. The desired model is persisted next to the weights, so a restart
resumes it instead of coming back empty. Model lifecycle and the memory
budget live in manager.py / memory.py.
"""

from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.manager import ModelManager, ModelTooLargeError

manager = ModelManager()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    await manager.resume_desired()
    yield
    await manager.shutdown()


app = FastAPI(
    title="Achilles Embeddings", docs_url=None, redoc_url=None, lifespan=_lifespan
)


class LoadRequest(BaseModel):
    model_id: str = Field(min_length=1)  # HF repo id, e.g. BAAI/bge-m3


class EmbeddingsRequest(BaseModel):
    model: str
    input: str | list[str]


@app.post("/admin/load")
async def load_model(body: LoadRequest) -> dict[str, str]:
    try:
        status = await manager.load(body.model_id)
    except ModelTooLargeError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_TOO_LARGE",
                "required_bytes": exc.required_bytes,
                "budget_bytes": exc.budget_bytes,
            },
        ) from exc
    return {"model_id": body.model_id, "status": status}


@app.post("/admin/preflight")
async def preflight(body: LoadRequest) -> dict[str, object]:
    report = manager.check_fit(body.model_id)
    return {
        "fits": report.fits,
        "required_bytes": report.required_bytes,
        "budget_bytes": report.budget_bytes,
        "resident": manager.snapshot(),
    }


@app.get("/admin/status")
async def status() -> dict[str, object]:
    return {
        "budget_bytes": manager.budget_bytes,
        "desired": manager.desired,
        "models": manager.snapshot(),
    }


@app.post("/v1/embeddings")
async def embeddings(body: EmbeddingsRequest) -> dict[str, object]:
    engine = manager.engine_for(body.model)
    if engine is None:
        # 503, not 500: the caller's search path degrades gracefully by design.
        raise HTTPException(
            status_code=503,
            detail=f"model {body.model!r} is {manager.state_of(body.model)}",
        )
    sentences = [body.input] if isinstance(body.input, str) else body.input
    vectors, usage = await engine.embed(sentences=sentences)
    return {
        "object": "list",
        "model": body.model,
        "data": [
            # C-side conversion; a Python float() loop over 1024 dims is a tax
            # on every response.
            {
                "object": "embedding",
                "index": i,
                "embedding": np.asarray(vector, dtype=np.float32).tolist(),
            }
            for i, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": usage, "total_tokens": usage},
    }


@app.get("/v1/models")
async def models() -> dict[str, object]:
    return {
        "object": "list",
        "data": [
            {"object": "model", "id": model_id}
            for model_id, info in manager.snapshot().items()
            if info["state"] == "ready"
        ],
    }


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "models": {m: info["state"] for m, info in manager.snapshot().items()},
    }
