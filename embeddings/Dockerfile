# Built-in embeddings runtime — its own image on purpose: torch/infinity_emb
# stay out of the backend (they pin <3.14 and weigh ~1GB even CPU-only).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Weights cache — a volume in compose; empty until a model is assigned.
    HF_HOME=/cache

WORKDIR /srv

COPY pyproject.toml ./
# Cache mount keeps the wheel cache out of the image layer (~5GB otherwise).
RUN --mount=type=cache,target=/root/.cache/uv uv sync --no-dev --no-install-project

COPY app ./app

EXPOSE 80
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
