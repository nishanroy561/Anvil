#!/usr/bin/env bash
# Run the API and the worker in one container, and make either one dying fatal.
#
# **That last part is the whole point of this script.** Anvil's ingest endpoint
# never writes to Postgres — it returns 202 the moment a trace is in Redis, and
# the worker drains the stream. So if the worker dies and the API keeps serving,
# the demo looks perfectly healthy: signup works, traces are accepted, every
# response is a 2xx, and not one trace ever appears in the dashboard. There is no
# error anywhere to lead you to the cause.
#
# `wait -n` returns as soon as *either* process exits. We then take the other one
# down and exit non-zero, so the Space restarts the pair. A visibly restarting
# Space is a much better failure than a silently half-working one.
set -uo pipefail

cd /home/user/app/anvil/apps/api

# Idempotent, and the only chance to create the schema: a Space has no compose
# `migrate` service and no shell you would reliably remember to use. Failing here
# stops the container, which is correct — an API serving 503s against a database
# with no tables is harder to diagnose than a build that stops and says why.
echo "==> applying migrations"
if ! python -m alembic upgrade head; then
  echo "!! migrations failed — check DATABASE_URL." >&2
  echo "!! Neon hands out a psycopg URL; asyncpg needs postgresql+asyncpg:// and" >&2
  echo "!! ?ssl=require (NOT sslmode=require, and no channel_binding)." >&2
  exit 1
fi

echo "==> starting worker"
python -m anvil_worker.main &
worker=$!

echo "==> starting api on :${PORT:-7860}"
python -m uvicorn anvil_api.main:app --host 0.0.0.0 --port "${PORT:-7860}" &
api=$!

wait -n "$worker" "$api"
code=$?

if ! kill -0 "$worker" 2>/dev/null; then
  echo "!! the worker exited (${code}) — traces would be accepted and never persisted" >&2
else
  echo "!! the api exited (${code})" >&2
fi

kill "$worker" "$api" 2>/dev/null || true
wait 2>/dev/null || true
exit "${code:-1}"
