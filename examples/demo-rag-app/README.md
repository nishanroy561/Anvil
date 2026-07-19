# Demo RAG App (Phase 3 — not built yet)

Spec: `plan/sdk.md` §2. A tiny FastAPI RAG app (fake retrieval + real-or-mocked
LLM via LangChain) instrumented with the Anvil Python SDK. Doubles as the
seed-data generator: `python -m demo seed --n 200`, later `--drift` (Phase 8)
and a thumbs-down button (Phase 9).
