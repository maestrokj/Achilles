# syntax=docker/dockerfile:1
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends curl postgresql-client
RUN adduser --disabled-password --gecos "" appuser

ENV VIRTUAL_ENV=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN uv venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Dependencies — cached until pyproject.toml changes
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p src/achilles && touch src/achilles/__init__.py && \
    uv pip install .

# Source (bind-mounted in docker-compose, this is fallback)
COPY . .
ENV PYTHONPATH=/app/src

USER appuser
EXPOSE 8000
# --timeout-graceful-shutdown caps how long uvicorn waits for long-lived SSE
# connections (bell, chat stream) to drain on shutdown before force-closing.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn achilles.main:app --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 5"]
