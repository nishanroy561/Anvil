# Deploying Anvil

Anvil is five containers on one host: Postgres, Redis, the API, the worker, and the
dashboard. There is no cluster mode, no managed offering, and no Helm chart. One
VM with Docker is the supported shape, and it will comfortably carry tens of
millions of traces before anything here needs rethinking.

**Minimum host:** 2 vCPU / 4 GB for a small team. The worker is the memory-hungry
part when running judged evals; Postgres is the disk-hungry part
(≈80 MB per 50k traces with spans, so budget from your ingest volume and
`RETENTION_DAYS`).

---

## 1. Get it running

```bash
git clone https://github.com/nishanroy561/Anvil && cd Anvil
cp .env.example .env
```

Edit `.env`. These four are the ones that matter:

```bash
# 1. Signs session cookies. The prod overlay refuses to start without it.
SECRET_KEY=<openssl rand -hex 32>

# 2. Your dashboard's real URL. Also decides whether the session cookie gets
#    `Secure` — an https origin gets one, http does not.
WEB_ORIGIN=https://anvil.example.com

# 3. Your reverse proxy's address. See §3 — skipping this breaks logins.
FORWARDED_ALLOW_IPS=172.18.0.0/16

# 4. Unset means keep every trace forever. Set it and the worker expires them.
RETENTION_DAYS=90
```

Then:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --wait
docker compose exec api python -m anvil_api.seed   # first user; prints an API key once
```

`--wait` returns only when every container reports *healthy*. If it hangs, one
didn't: `docker compose ps` then `docker compose logs <service>`.

What the overlay changes from the dev defaults: two worker replicas, Postgres and
Redis unpublished, `SECRET_KEY` mandatory, `restart: always`, and memory limits.

## 2. Change the database password

The compose file ships `POSTGRES_PASSWORD: anvil`, and **nothing enforces changing
it.** The overlay unpublishes the port so it is not reachable from outside the host,
which is mitigation, not a fix — anything else on that host can still reach it.

There is no override variable because the password appears in three places that must
agree. Edit `docker-compose.yml` directly:

```yaml
postgres:
  environment:
    POSTGRES_PASSWORD: <your password>
api:
  environment:
    DATABASE_URL: postgresql+asyncpg://anvil:<your password>@postgres:5432/anvil
worker:
  environment:
    DATABASE_URL: postgresql+asyncpg://anvil:<your password>@postgres:5432/anvil
migrate:
  environment:
    DATABASE_URL: postgresql+asyncpg://anvil:<your password>@postgres:5432/anvil
```

On an existing volume the password is already baked into the data directory, so
change it in Postgres too — `ALTER USER anvil WITH PASSWORD '…'` — or start from a
fresh volume.

## 3. Put a reverse proxy in front

**Anvil ships no TLS and no reverse proxy.** That is deliberate: the right answer
depends on whether you use Caddy, nginx, an ALB, or Cloudflare, and a
half-configured one in the repo would be worse than none. But you need one, and one
detail is easy to get wrong in a way that looks like an unrelated bug.

Two ports are published: **3000** (dashboard, for browsers) and **8000** (API, for
your app's SDK and CI). The browser never calls 8000 — the dashboard proxies
`/api/*` to the API server-side so the session cookie stays first-party.

Caddy, which is the shortest correct version:

```
anvil.example.com {
    reverse_proxy localhost:3000
}

anvil-api.example.com {
    reverse_proxy localhost:8000
}
```

Then set `FORWARDED_ALLOW_IPS`. **This is the one people miss.** Uvicorn only
rewrites the client address from `X-Forwarded-For` for proxies it has been told to
trust, and its default is loopback. Leave it unset behind a proxy and every request
appears to come from the same address, so the per-IP login limit (10/min) meters
your whole user base into one bucket — the eleventh person to sign in that minute
gets a 429 they cannot explain.

Anvil detects that case: it notices a forwarded header that uvicorn did not apply,
logs

```
X-Forwarded-For is present but uvicorn did not apply it (peer=…).
Set FORWARDED_ALLOW_IPS to your proxy's address …
```

and falls back to the per-email limit alone rather than locking everyone out. It
does **not** trust the header directly — that would let any caller pick its own
bucket and bypass the limit entirely.

Set it to the proxy's subnet, or `*` if nothing but the proxy can reach port 8000.

## 4. Point the dashboard at the API

`WEB_API_BASE_URL` is a **build** argument, not a runtime one. Next.js evaluates
`rewrites()` during `next build` and freezes the target into the image, so setting
it on a running container does nothing at all.

The compose default (`http://api:8000`) is correct for every single-host
deployment, because the web container reaches the API by service name on the
compose network. You only need to change it if the API lives somewhere else:

```bash
docker compose build --build-arg WEB_API_BASE_URL=https://anvil-api.example.com web
```

## 5. Judged evals need a provider key

LLM judges (`accuracy`, `hallucination`, `tone`, `custom`) call a provider, and
those SDKs are an image extra so installs that never use them don't carry three of
them:

```bash
WORKER_EXTRAS=[judges,otel] docker compose build worker
```

Then set whichever key matches the models you name — `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`, `GROQ_API_KEY`. The key lives on the worker, never in CI.

**Name the model explicitly in every judge config.** With none named, the first key
present wins in a fixed order, which is rarely what you meant and produces a run
that silently used the wrong provider.

## 6. Retention and disk

Unset `RETENTION_DAYS` means keep everything, forever, by design: an operator who
has not asked for deletion never gets deletion. Set it to a number of days and the
worker deletes older traces once a day, in 5k batches with a pause between so the
sweep never holds locks for its whole duration.

**Datasets are unaffected.** A promoted item carries its own copy of the input and
outputs, so your test cases outlive the traffic they came from.

The daily pass also logs the largest tables, so "why is the disk full" is answerable
from history rather than only from the present.

## 7. Backups

Nothing here is backed up for you. The database is the only durable state that
matters — Redis is a buffer, and losing it costs at most the traces still in flight.

```bash
docker compose exec -T postgres pg_dump -U anvil anvil | gzip > anvil-$(date +%F).sql.gz
```

Restore into a fresh volume, then `docker compose up -d` — the `migrate` service is
idempotent and will no-op on an already-current schema.

## 8. Monitoring the deployment

```bash
docker compose --profile observability up -d --wait
```

Prometheus on 9090, Grafana on 3001 (anonymous viewer by default — put it behind
your proxy or turn that off before exposing it). Ships with an ingestion dashboard
and five alert rules, including the distinction between a *stuck* worker (consumer
lag growing) and a *dead* one (target down + stream depth rising) — the lag gauge
freezes when a worker dies, so it cannot catch that case alone.

For distributed traces of Anvil's own request path:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
  docker compose --profile observability up -d
```

One ingest request then appears in Tempo as a single trace spanning API → worker →
Postgres. Off by default, because it costs latency on every request.

Without the profile, the two endpoints worth pointing your own monitoring at are
`GET /healthz` (503s when Postgres or Redis is unreachable) and `GET /metrics`.

## 9. Upgrading

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --wait
```

The `migrate` service runs `alembic upgrade head` before the API starts, once,
regardless of how many replicas come up. A failed migration shows as a failed
container and the API does not start on a schema it does not understand.

There is **no automatic rollback**. Take a dump first (§7) — migrations are
forward-only in practice even where a `downgrade()` exists.

---

## What Anvil deliberately does not do

Being explicit so you can plan around it rather than discover it:

| | |
|---|---|
| **TLS / reverse proxy** | Bring your own — §3 |
| **Backups** | Bring your own — §7 |
| **Secret management** | Env vars and `.env`. No Vault, no KMS |
| **Horizontal scale** | Workers scale (change `replicas`); the API scales behind a load balancer; Postgres and Redis are single instances |
| **SSO / OAuth / roles** | Email and password, one role. Deliberately out of scope for v1 |
| **Multi-host orchestration** | No Helm chart, no Swarm/ECS manifests. The images are ordinary, so writing one is straightforward |
| **Reproducible web builds** | No npm lockfile is committed, so image builds resolve dependencies fresh. Pin one if that matters to you |

## Troubleshooting

| Symptom | Cause |
|---|---|
| `up --wait` hangs | a container is unhealthy — `docker compose ps`, then its logs |
| Logins 429 for unrelated users | `FORWARDED_ALLOW_IPS` is unset behind a proxy (§3) |
| Login appears to succeed, then you're signed out | cookie has `Secure` but the browser reached the dashboard over http — check `WEB_ORIGIN` |
| Dashboard 500s on every request | `WEB_API_BASE_URL` baked wrong at build time (§4) |
| Traces accepted (202) but never appear | the worker is down. Ingest returns 202 the moment a trace is queued, by design — `docker compose logs worker` |
| Eval runs stay `queued` | same: the worker owns the `eval:jobs` stream |
| Judged graders all error | no provider key, or no model named (§5) |
| Disk filling | set `RETENTION_DAYS` (§6); the daily log line shows the largest tables |
