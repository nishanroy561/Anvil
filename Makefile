# Dev shortcuts. `make` is often unavailable on Windows — every recipe below is a
# one-liner you can paste directly, and STATUS.md records the verified sequence.
#
# The suites need Postgres and Redis (`make up-svc`). They build their own
# `anvil_test` database and use Redis DB 15, so dev data is never touched; with the
# services down they skip with a pointer instead of failing.

.PHONY: up up-svc up-obs up-prod down clean \
        api worker web migrate seed \
        test test-api test-worker test-sdk-py test-sdk-ts test-demo test-plugins \
        lint typecheck build-web \
        accept accept-ingest accept-reads

# ── running it ───────────────────────────────────────────────────────────

up:                    ## the whole product, migrations included
	docker compose up -d --build --wait

up-svc:                ## just Postgres and Redis, for host-side development
	docker compose up -d postgres redis --wait

up-obs:                ## + Prometheus, Grafana, Tempo, OTel collector
	OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
	  docker compose --profile observability up -d --build --wait

up-prod:               ## production overlay: 2 workers, no published DB ports
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --wait

down:
	docker compose down

clean:                 ## down + delete the volumes (destroys all local data)
	docker compose down -v

# ── host-side development ────────────────────────────────────────────────

api:
	cd apps/api && python -m uvicorn anvil_api.main:app --reload --port 8000

worker:
	cd apps/worker && python -m anvil_worker.main

web:
	cd apps/web && npm run dev

migrate:
	cd apps/api && python -m alembic upgrade head

seed:
	cd apps/api && python -m anvil_api.seed

# ── tests ────────────────────────────────────────────────────────────────
# `make test` is Phase 11 §7: every suite in the repo, not just the API's.

test: test-api test-worker test-sdk-py test-sdk-ts test-demo
	@echo ""
	@echo "all suites passed"

test-api:
	cd apps/api && python -m pytest -q

test-worker:
	cd apps/worker && python -m pytest -q

test-sdk-py:
	cd sdks/python && python -m pytest -q

test-sdk-ts:
	cd sdks/typescript && npm test

test-demo:
	cd examples/demo-rag-app && python -m pytest -q

test-plugins:          ## needs the example plugin installed to be meaningful
	pip install -e examples/plugin-example
	cd apps/worker && python -m pytest tests/test_plugins.py -q

# ── static checks ────────────────────────────────────────────────────────

lint:
	cd apps/api && python -m ruff check .
	cd apps/worker && python -m ruff check .

typecheck:
	cd apps/web && npx tsc --noEmit

build-web:
	cd apps/web && npm run build

# ── acceptance scripts ───────────────────────────────────────────────────
# Each needs the API running (`make api`) and asserts a phase's criterion.
# `retention_acceptance.py` is deliberately excluded from `accept`: it runs a
# global retention sweep and deletes real data, so it stays an explicit
# `--force` opt-in.

accept: accept-ingest accept-reads
	@echo ""
	@echo "run the phase acceptances individually — see scripts/"

accept-ingest:
	python scripts/loadtest_ingest.py --traces 1000 --kill-worker

accept-reads:
	python scripts/loadtest_reads.py --traces 50000
