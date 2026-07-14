.DEFAULT_GOAL := help

BACKEND    := backend
FRONTEND   := frontend
VENV       := $(BACKEND)/.venv/bin
PY         := .venv/bin
COMPOSE    := docker compose
COMPOSE_PROD := docker compose -f docker-compose.prod.yml

# xdist workers: each spins its own postgres+redis; needs Docker Desktop's VM at 10 CPU / 12 GB.
TEST_WORKERS ?= 6

# macOS: libpq (pg_dump/pg_restore, used by the backup/restore tests) is keg-only,
# so Homebrew keeps it out of PATH. Fold it in when present, else those tests skip.
PG_TOOLS_BIN := $(firstword $(wildcard /opt/homebrew/opt/libpq/bin /usr/local/opt/libpq/bin))
PG_TOOLS_PATH := $(if $(PG_TOOLS_BIN),$(PG_TOOLS_BIN):,)

# -- Dependencies --

.PHONY: install
install: ## Install all dependencies
	cd $(BACKEND) && $(PY)/pip install -e ".[dev]"
	cd $(FRONTEND) && npm install

# -- Code quality --

.PHONY: lint
lint: ## Lint without auto-fix
	cd $(BACKEND) && $(PY)/ruff check src/ tests/
	cd $(FRONTEND) && npm run lint

.PHONY: format
format: ## Auto-format code
	cd $(BACKEND) && $(PY)/ruff check --fix src/ tests/ && $(PY)/ruff format src/ tests/
	cd $(FRONTEND) && npm run format

.PHONY: check
check: ## Full check (lint + format + types) — parallel
	@cd $(BACKEND) && $(PY)/ruff check src/ tests/ && $(PY)/ruff format --check src/ tests/
	@$(VENV)/pyright & PID1=$$!; \
	cd $(FRONTEND) && npm run check & PID2=$$!; \
	wait $$PID1 || exit 1; \
	wait $$PID2 || exit 1

.PHONY: test
test: ## Run tests
	@cd $(BACKEND) && PATH="$(PG_TOOLS_PATH)$$PATH" $(PY)/pytest -n $(TEST_WORKERS); rc=$$?; if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then exit $$rc; fi
	@cd $(FRONTEND) && npm run --silent test
	@$(MAKE) --no-print-directory test-embeddings

.PHONY: test-embeddings
test-embeddings: ## Run the embeddings runtime test suite (own venv, py3.12)
	@cd embeddings && uv run --quiet pytest -q

.PHONY: dead-code
dead-code: ## Find unused code (frontend)
	cd $(FRONTEND) && npm run knip

.PHONY: postcheck
postcheck: check test dead-code ## Full post-implementation check

.PHONY: fuzz-api
fuzz-api: ## Schemathesis fuzz against a running backend (make up first)
	cd $(BACKEND) && $(PY)/schemathesis run http://127.0.0.1:8000/openapi.json

# -- Git --

.PHONY: hooks
hooks: ## Install pre-commit hooks
	$(VENV)/pre-commit install

# -- Database --

.PHONY: db-migrate
db-migrate: ## Create new migration (usage: make db-migrate m="description")
	cd $(BACKEND) && $(PY)/alembic revision --autogenerate -m "$(m)"

.PHONY: db-upgrade
db-upgrade: ## Apply all pending migrations
	cd $(BACKEND) && $(PY)/alembic upgrade head

.PHONY: db-downgrade
db-downgrade: ## Rollback one migration
	cd $(BACKEND) && $(PY)/alembic downgrade -1

.PHONY: db-history
db-history: ## Show migration history
	cd $(BACKEND) && $(PY)/alembic history --verbose

# -- Docker (dev) --

.PHONY: up
up: ## Start dev environment (rebuilds images if Dockerfile/deps changed)
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop dev environment
	$(COMPOSE) down

.PHONY: build
build: ## Build dev images
	$(COMPOSE) build

.PHONY: logs
logs: ## Dev logs (follow)
	$(COMPOSE) logs -f

# -- Docker (prod) --

.PHONY: prod-up
prod-up: ## Start prod environment
	$(COMPOSE_PROD) up -d

.PHONY: prod-down
prod-down: ## Stop prod environment
	$(COMPOSE_PROD) down

.PHONY: prod-build
prod-build: ## Build prod images
	$(COMPOSE_PROD) build

.PHONY: prod-logs
prod-logs: ## Prod logs (follow)
	$(COMPOSE_PROD) logs -f

# -- Shell access --

.PHONY: shell
shell: ## Open shell in backend container
	$(COMPOSE) exec backend bash

.PHONY: shell-db
shell-db: ## Open psql in postgres container
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-achilles} -d $${POSTGRES_DB:-achilles}

# -- Prod SSL --

.PHONY: cert-init
cert-init: ## Get initial SSL certificate (usage: make cert-init DOMAIN=example.com EMAIL=you@example.com)
	$(COMPOSE_PROD) run --rm certbot certonly --webroot -w /var/www/certbot -d $(DOMAIN) --email $(EMAIL) --agree-tos --non-interactive
	$(COMPOSE_PROD) exec nginx nginx -s reload

# -- Docs --

.PHONY: docs-map
docs-map: ## Validate architecture doc cross-links + print link map (EN + RU mirror)
	@python3 docs/architecture/_tools/docs_map.py --human
	@python3 docs/ru/architecture/_tools/docs_map.py --human

# -- Utilities --

.PHONY: clean
clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(BACKEND)/dist $(BACKEND)/*.egg-info

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'
