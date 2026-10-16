.PHONY: setup env up down load refresh eval test ask api web

setup: env  # fresh machine: tools check, .env, dependencies, FalkorDB, graph (~6 min first time: embeddings)
	@for t in docker uv npm openssl; do command -v $$t >/dev/null || { echo "missing: $$t"; exit 1; }; done
	cd backend && uv sync
	cd web && npm ci --no-audit --no-fund
	$(MAKE) load

env:  # .env from .env.example with random secrets (never overwrites an existing .env)
	@test -f .env || { sed -e "s/^AGENT_RO_PASSWORD=.*/AGENT_RO_PASSWORD=$$(openssl rand -hex 16)/" \
	  -e "s/^WS_WRITER_PASSWORD=.*/WS_WRITER_PASSWORD=$$(openssl rand -hex 16)/" \
	  -e "s/^MCP_RO_PASSWORD=.*/MCP_RO_PASSWORD=$$(openssl rand -hex 16)/" \
	  -e "s/^MCP_API_KEY=.*/MCP_API_KEY=$$(openssl rand -hex 24)/" .env.example > .env && echo "wrote .env"; }

up:
	docker compose up -d
	@until docker compose exec -T falkordb redis-cli ping >/dev/null 2>&1; do sleep 1; done
	@until [ "$$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/mcp)" != 000 ]; do sleep 1; done

down:
	docker compose down

load: up
	cd backend && uv run python -m unheeded.loader && uv run python -m unheeded.enrich

refresh:  # make refresh DATA=<dir with a newer CAROL export>: diff, reload, alert watching workspaces, re-enrich
	@test -n "$(DATA)" || { echo "usage: make refresh DATA=<dir>"; exit 1; }
	cd backend && uv run python -m unheeded.refresh "$(abspath $(DATA))" && uv run python -m unheeded.enrich

eval:
	cd backend && uv run python -m eval.eval1 && uv run python -m eval.eval2 && uv run python -m eval.eval3 && uv run python -m eval.eval4 && uv run python -m eval.concurrency

test:
	cd backend && uv run pytest -q

ask:  # make ask Q="Was the Nome crash warned about?" [WS=demo] [DECIDE=approve|reject]
	cd backend && uv run python -m unheeded.agents "$(Q)" --workspace $(or $(WS),demo) $(if $(DECIDE),--decision $(DECIDE))

api:  # HTTP API for the web app, localhost only
	cd backend && uv run uvicorn unheeded.api:app --host 127.0.0.1 --port 8000

web:  # Next.js app on http://127.0.0.1:3001 (needs `make api` running); first time: cd web && npm install
	cd web && npm run dev
