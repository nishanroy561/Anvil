# Dev shortcuts. On Windows without make, run the underlying commands directly
# (they are all one-liners; see STATUS.md for the verified sequence).

up:
	docker compose up -d --build

up-obs:
	docker compose --profile observability up -d --build

down:
	docker compose down

api:
	cd apps/api && uvicorn anvil_api.main:app --reload --port 8000

worker:
	cd apps/worker && python -m anvil_worker.main

web:
	cd apps/web && npm run dev

migrate:
	cd apps/api && alembic upgrade head

seed:
	cd apps/api && python -m anvil_api.seed

test:
	cd apps/api && python -m pytest -q
