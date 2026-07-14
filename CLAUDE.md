# Achilles

Enterprise AI platform — a single system that replaces a patchwork of disconnected AI tools: connect any provider and model, run and govern agents, track token spend, and ground every answer in company knowledge.

## 🚦 Stage & Workflow

**Architecture-first**: each module is visually designed in `docs/architecture/`, locked there, then implemented. **v1 is complete and verified end-to-end** — backend, frontend, and both Docker Compose topologies (dev and prod) — and the platform is entering production rollout and enterprise evaluation. New work still starts in the docs layer — design, lock, then code.

## ⚡ Commands

| Command                  | Action                                               |
| ------------------------ | ---------------------------------------------------- |
| `make postcheck`         | Post-implementation gate (check + test + dead-code)  |
| `make check`             | Lint + format-check + types                          |
| `make format`            | Auto-format (backend + frontend)                     |
| `make test`              | Run all tests                                        |
| `make docs-map`          | Validate architecture doc cross-links + link map     |
| `make up` / `down`       | Dev environment (Docker)                             |
| `make prod-up` / `prod-down` | Production stack (`docker-compose.prod.yml`)     |
| `make db-migrate m="…"`  | New Alembic migration                                |
| `make db-upgrade`        | Apply pending migrations                             |
| `make install` / `hooks` | Install dependencies / pre-commit hooks              |

## 📚 Architecture Docs

Layered visual HTML docs — the design source of truth:

- `docs/architecture/README.md` — **all authoring conventions** (layers, lifecycle, labels, cross-links, CSS, language). **Read it first when working on docs; do not rely on summaries.**
- `docs/architecture/architecture-scheme.html` — L1 hub, clickable modules.
- `docs/architecture/modules/` — L2 module pages. A designed module is a directory with `index.html` + three parallel L3 branches: `_workzone/` (design topics), `_wireframes/` (one file per screen), `_features/<f>/` (multi-screen flows). Undesigned modules are single-file stubs.
- `docs/architecture/open-questions.md` — deliberately deferred decisions.
- `docs/presentation/` — product concept (`presentation.html`) and long-term vision (`vision.html`).

After any edit touching links or anchors: `make docs-map`.

## 🏛️ Modules

**Core:** Harvester (data ingestion / ETL) · Knowledge Store (knowledge graph, metadata, vectors) · Cache & Workers (query cache, task queues) · Query Engine (user queries: RAG + hybrid search + agentic) · Agent Engine (autonomous analytics agents)

**Entry points:** Web App (end-user shell: knowledge queries, agent chat) · Admin Panel (config, monitoring, user management) · MCP (AI assistant integration) · Public API (external clients) · Slack · Telegram · Mattermost · Browser Extension (planned)

**Cross-cutting:** Auth & Security (JWT, API keys, access control) · AI Foundation (AI provider/model/tool/prompt/cost registry) · Email (SMTP, transactional) · Notifications (events, channels, in-app + email)

## 📁 Structure

    ├── backend/            Python ≥ 3.14 (FastAPI, uv)
    │   ├── src/achilles/   Application code
    │   ├── alembic/        DB migrations
    │   └── tests/
    ├── frontend/           TypeScript (React, Vite, react-router)
    ├── embeddings/         Built-in embedder microservice (own venv, lazy-loaded weights)
    ├── nginx/              Reverse proxy
    ├── docs/architecture/  Architecture docs (HTML)
    ├── docker-compose.yml       Dev: postgres (pgvector) · redis-durable · redis-cache · backend · embeddings · worker · scheduler · mailpit · frontend
    └── docker-compose.prod.yml  Prod: migrate gate · worker split into 3 lanes (interactive / background / agents) · nginx + certbot (TLS) · autoheal

## 🛠️ Tech Stack

**Backend** — Python ≥ 3.14 · uv · FastAPI · uvicorn · Pydantic v2 · SQLAlchemy 2.0 (asyncpg) · Alembic · Redis · SAQ (queues + cron) · PyJWT · argon2-cffi · anthropic + openai SDKs (LLM) · mcp SDK (MCP surface) · aiosmtplib + Jinja2 (email) · aioboto3 (S3 backups)

**Frontend** — TypeScript · React · Vite · react-router · Tailwind CSS · shadcn/ui on Base UI (`@base-ui/react`) · TanStack Query · react-i18next · assistant-ui (chat) · SSE

**Infra** — Docker Compose · Nginx · PostgreSQL + pgvector (the knowledge graph is Postgres-only: `entity_edge` + recursive CTE — no separate graph engine) · two Redis instances (durable: AOF + noeviction · cache: LRU) · prod splits the worker into 3 lanes (interactive / background / agents)

## 📏 Conventions

- ❌ **No hardcoding** — magic strings, numbers, config values → dedicated modules
- 📍 **Proximity principle** — constants/enums live in the consumer module; extract to shared at 2+ consumers
- 🎨 **Theming** — dark + light via shadcn CSS tokens (`bg-background`, `text-primary`); never hardcode colors
- 🌍 **i18n** — all user-facing strings via `t()`; typed keys; locales in `frontend/src/i18n/locales/`
- 📐 **No layout shift** — a component with its own `useQuery` reserves its height while `isPending`: a skeleton shaped like the real content, never `return null` and never the empty state (`items.length === 0` is a fact about *loaded* data). `PageSkeleton` for whole screens, `TableSkeleton` for tables, a bespoke skeleton for anything else. Values that repaint the first frame (theme class, org accent) are applied before it — see `index.html` and `DisplayPrefs`
- 🔗 **Backend is the source of truth** for domain values → frontend receives them via API
- 🕐 **UTC everywhere** — store and transmit UTC, display in user timezone (frontend only):
  - DB: `TIMESTAMPTZ` (`DateTime(timezone=True)`)
  - Python: `datetime.now(UTC)` + `zoneinfo` — never `utcnow()` (deprecated) or `pytz`
  - API: ISO 8601 UTC (`"2026-05-27T14:30:00Z"`)
  - Frontend: convert via `Intl.DateTimeFormat`; priority: user override → org default (`platform_settings.timezone`, IANA) → browser locale

## 🗃️ Migrations (Alembic)

- ⚠️ **Until the first production deployment:** no live data exists — edit existing migrations in place instead of stacking new ones. From the first deploy onward, migrations become append-only
- PK/FK: `BigInteger` · text: `Text` over `String(N)` · JSON: `JSONB` · timestamps: `DateTime(timezone=True)`
- 🏷️ **Column naming** — generic flexible-attributes JSONB column → **`meta`**, never `metadata` (reserved by SQLAlchemy declarative as `Base.metadata` — a `metadata` mapped attribute raises `InvalidRequestError`). Same name across the schema and the architecture docs; purpose-specific JSON gets a meaningful name (`scope`, `checkpoint`, `content_filters`), not `meta`
- `created_at` / `updated_at`: `server_default=sa.func.now()`, trigger `set_updated_at()` for `updated_at`
  - **When to include** — mutable row (in-place `UPDATE`, incl. UPSERT/counter aggregates) → both `created_at` **and** `updated_at`; immutable-after-insert row (append-only journal/log, one-time token, snapshot replaced wholesale) → `created_at` only. `updated_at` without `created_at` is never valid
  - **Run-journal exception** — a run/state-machine journal (`sync_runs`, `curation_runs`, `backup_snapshots`, `agent_runs`) mutates in place via `queued→running→terminal` transitions, yet carries `created_at` only: its progress lives in the domain timestamps `started_at` / `finished_at` / `heartbeat_at` (heartbeat every ~30s is a finer last-touch than a generic `updated_at`), so `updated_at` would just duplicate `heartbeat_at`
- FK: explicit `ondelete`, `index=True` · `downgrade()` mirrors `upgrade()` in reverse · one migration = one logical unit

## ✅ Quality

Three levels: **format-on-save** (hook on Edit/Write) → **pre-commit** → **`make check` / `make postcheck`**.

Tools: Ruff (Python lint+format) · Pyright (Python types) · ESLint · Prettier · tsc · Knip (frontend dead code).

## 🔎 Code navigation

For **semantic** questions about code symbols (where defined, who calls it, type info, call hierarchy) prefer **LSP** (`goToDefinition`, `findReferences`, `hover`, `documentSymbol`) over `grep` — it understands semantics, skips same-name collisions and string/comment matches. Servers: Pyright (`.py`), tsserver (`.ts`/`.tsx`).

Use `grep` for text patterns, strings, TODOs, and files without an LSP server (HTML/CSS/MD/configs — most of the architecture docs).
