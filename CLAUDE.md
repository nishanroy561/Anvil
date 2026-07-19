# Anvil — agent instructions

**Read in this order before doing anything: 1) `STATUS.md` (what exists and what's next), 2) `plan.md` (phase order + acceptance criteria), 3) the `plan/` doc for the phase you're implementing.**

## Rules

- Execute phases in order; a phase is done only when its acceptance criteria in `plan.md` actually pass (run them, don't assume).
- Decisions are settled in `plan/adr/` — do not relitigate an Accepted ADR. New significant decisions get a new ADR at decision time.
- Schema changes only via Alembic migrations (`apps/api/alembic/`). The full v1 schema already exists in migration `0001`.
- `project_id` always comes from the resolved credential (API key/session), never from a request body.
- Tenant behavior: other tenants' resources return 404, never 403.
- Mock all LLM calls in automated tests (LangChain fake LLMs / respx); the suite must run keyless.
- Errors use `{"error": {"code", "message"}}` (routers currently raise HTTPException with a dict detail — keep the shape consistent).
- UTC everywhere; timezone rendering is frontend-only.
- Before finishing a phase, check `plan.md`'s edge-case checklist rows tagged with that phase.
- Update `STATUS.md` whenever you complete meaningful work — it is the handoff document between sessions/models.

## Commands (Windows dev box; `make` may be unavailable — run these directly)

```
docker compose up -d postgres redis          # backing services
cd apps/api
pip install -e .[dev]                        # once
alembic upgrade head                         # migrate
python -m anvil_api.seed                     # demo org/user/project/key
uvicorn anvil_api.main:app --reload          # API on :8000
python -m pytest -q                          # tests
cd apps/web && npm install && npm run dev    # web on :3000
```
