# MarketLens helper commands.  Run `make help`.
#
# Uses the local toolchain (uv + pnpm + Node) when it is installed, and falls back to Docker when it
# is not.  Force one with `make start MODE=local` or `make start MODE=docker`.

API_PORT ?= 8000
WEB_PORT ?= 3000
LOG      := data/runtime/dev.log
PIDFILE  := data/runtime/dev.pid

HAVE_LOCAL := $(shell command -v uv >/dev/null 2>&1 && command -v pnpm >/dev/null 2>&1 && command -v node >/dev/null 2>&1 && echo yes)
MODE ?= $(if $(HAVE_LOCAL),local,docker)
# The LiveKit voice worker starts with the app when .env selects VOICE_MODE=livekit.
VOICE_FLAG := $(shell grep -qE '^VOICE_MODE=livekit' .env 2>/dev/null && echo --voice)

export API_PORT WEB_PORT

.DEFAULT_GOAL := help
.PHONY: help install seed start stop restart run status logs test evaluate lint clean-docker

help: ## Show this help
	@echo "MarketLens  (mode: $(MODE); ports: web $(WEB_PORT), api $(API_PORT); voice: $(if $(VOICE_FLAG),on,off))"
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-13s %s\n", $$1, $$2}'
	@echo "Options: MODE=local|docker  WEB_PORT=3000  API_PORT=8000"

install: ## Install dependencies (local mode; Docker installs on first start)
ifeq ($(MODE),local)
	pnpm install
	uv sync
else
	docker compose build
endif

seed: ## Create demo runs, a saved brief and a watchlist (fixture data only)
ifeq ($(MODE),local)
	uv run python scripts/seed_demo.py
else
	docker compose run --rm marketlens sh -c "pnpm install --frozen-lockfile && uv sync --frozen && uv run python scripts/seed_demo.py"
endif

start: ## Start MarketLens in the background (installs and seeds first when needed)
ifeq ($(MODE),local)
	@mkdir -p data/runtime
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then echo "Already running (pid $$(cat $(PIDFILE))). Use 'make status'."; exit 0; fi; \
	[ -d node_modules ] && [ -d .venv ] || $(MAKE) --no-print-directory install MODE=local; \
	if grep -qE '^(DATA_MODE=live|LLM_MODE=openai)' .env 2>/dev/null; then echo 'Skipping demo seed (live data or a real LLM is configured).'; else $(MAKE) --no-print-directory seed MODE=local; fi; \
	nohup uv run python scripts/dev.py --api-port $(API_PORT) --web-port $(WEB_PORT) $(VOICE_FLAG) > $(LOG) 2>&1 & echo $$! > $(PIDFILE); \
	for i in $$(seq 1 240); do grep -q "MarketLens is running" $(LOG) 2>/dev/null && break; \
	  kill -0 $$(cat $(PIDFILE)) 2>/dev/null || { echo "Failed to start:"; tail -n 20 $(LOG); rm -f $(PIDFILE); exit 1; }; sleep 0.5; done; \
	grep "MarketLens is running" $(LOG) || { echo "Still starting; see 'make logs'."; }
else
	docker compose up -d --build
	@echo "Starting in Docker (first start installs dependencies, which takes a few minutes). Follow with 'make logs'."
	@echo "Web: http://localhost:$(WEB_PORT)   API: http://127.0.0.1:$(API_PORT)/api/health"
endif

run: ## Run in the foreground (Ctrl+C stops everything)
ifeq ($(MODE),local)
	uv run python scripts/dev.py --api-port $(API_PORT) --web-port $(WEB_PORT) $(VOICE_FLAG)
else
	docker compose up --build
endif

stop: ## Stop MarketLens (the backend, web app and MCP servers)
ifeq ($(MODE),local)
	@if [ -f $(PIDFILE) ]; then pid=$$(cat $(PIDFILE)); kill -INT $$pid 2>/dev/null; \
	  for i in $$(seq 1 60); do kill -0 $$pid 2>/dev/null || break; sleep 0.5; done; rm -f $(PIDFILE); fi
	@pkill -INT -f "scripts/dev.py --api-port $(API_PORT) --web-port $(WEB_PORT)" 2>/dev/null; sleep 1; echo "Stopped."
else
	docker compose down
endif

restart: stop start ## Stop, then start

status: ## Show whether MarketLens is up
ifeq ($(MODE),local)
	@curl -fsS -m 3 http://127.0.0.1:$(API_PORT)/api/health >/dev/null 2>&1 \
	  && echo "API   up   http://127.0.0.1:$(API_PORT)/api/health" || echo "API   down"
	@curl -fsS -m 3 -o /dev/null http://localhost:$(WEB_PORT) 2>/dev/null \
	  && echo "Web   up   http://localhost:$(WEB_PORT)" || echo "Web   down"
ifneq ($(VOICE_FLAG),)
	@grep -q "registered worker" $(LOG) 2>/dev/null && pgrep -f "marketlens-voice-agent dev" >/dev/null \
	  && echo "Voice up   (LiveKit worker registered)" || echo "Voice down (see 'make logs')"
endif
else
	docker compose ps
endif

logs: ## Follow the logs
ifeq ($(MODE),local)
	tail -f $(LOG)
else
	docker compose logs -f
endif

test: ## Run the Python test suite
ifeq ($(MODE),local)
	uv run pytest
else
	docker compose run --rm marketlens sh -c "pnpm install --frozen-lockfile && uv sync --frozen && uv run pytest"
endif

evaluate: ## Run the 49-case evaluation and write reports/evaluation/latest.*
ifeq ($(MODE),local)
	uv run python scripts/run_evaluation.py
else
	docker compose run --rm marketlens sh -c "uv sync --frozen && uv run python scripts/run_evaluation.py"
endif

lint: ## Lint and type-check Python and the web app (local mode)
	uv run ruff check services tests scripts && uv run ruff format --check services tests scripts
	pnpm --dir apps/web lint && pnpm --dir apps/web typecheck

clean-docker: ## Remove the Docker containers and dependency volumes
	docker compose down -v
