# Deploying the Anvil backend to a Hugging Face Space

For a hosted demo on free infrastructure with no card required. The Space runs
the API and worker; the dashboard goes on Vercel; Postgres and Redis are managed
services (Neon and Upstash both have card-free free tiers).

The files in this directory are the Space's contents — they are kept here so the
deploy is version-controlled alongside the code it deploys, not because Anvil
itself builds from them.

## 0. Push first

The Dockerfile clones `main` from GitHub at build time. Anything not pushed is
not in the image. If you have local commits, push them before building the Space,
or the deploy will quietly run older code.

## 1. Create the Space

<https://huggingface.co/new-space> → **Docker** → **Blank**. Free CPU hardware is
enough (2 vCPU / 16 GB).

## 2. Push these three files to it

A Space is a git repo. Clone it and copy `Dockerfile`, `start.sh` and `README.md`
from this directory to its **root**:

```bash
git clone https://huggingface.co/spaces/<you>/<space> space && cd space
cp /path/to/Anvil/deploy/huggingface/{Dockerfile,start.sh,README.md} .
git add . && git commit -m "Anvil backend" && git push
```

`README.md` carries the YAML frontmatter that tells Spaces this is a Docker app
on port 7860. Without it the Space will not start.

## 3. Set the secrets

**Settings → Variables and secrets**. See `README.md` in this directory for the
table and — more importantly — the two connection strings that are easy to get
wrong. Briefly:

- `DATABASE_URL` needs `postgresql+asyncpg://` and `?ssl=require`. Neon gives you
  `postgresql://` with `?sslmode=require&channel_binding=require`; asyncpg
  rejects both of those parameters before it opens a socket.
- `REDIS_URL` needs `rediss://`, two s's. Upstash is TLS-only.
- Set `RETENTION_DAYS=7` or the free Postgres tier fills up.
- Set **no** provider API keys. A public demo where visitors can start eval runs
  must not be able to spend your credits.

## 4. Point the dashboard at it

In Vercel, set **`WEB_API_BASE_URL=https://<you>-<space>.hf.space`** and
**redeploy**. This is a *build* variable: Next bakes the `/api` rewrite into the
build, so changing it without redeploying does nothing at all.

Set `WEB_ORIGIN` on the Space to the Vercel URL to match — it drives CORS and the
`Secure` flag on the session cookie.

## 5. Verify, in this order

```bash
curl https://<you>-<space>.hf.space/healthz     # want {"db":true,"redis":true}
```

Then open the dashboard, sign up, create an API key in Settings, and send one
trace. If it appears in the traces list, all four pieces are talking.

Optionally seed the demo tenant so a visitor lands on something rather than an
empty state — from a checkout, with the Space's `DATABASE_URL` exported:

```bash
cd apps/api && python -m anvil_api.seed
```

## What to expect from the free tier

The Space sleeps after **48 hours** of inactivity and any visitor restarts it.
That is survivable here because Postgres and Redis are external: traces buffered
in Redis are drained by the worker when it wakes rather than lost.

While asleep the API is not accepting traces — but the SDK never blocks or
crashes the host app (ADR-0013); it buffers, retries with backoff, and counts
anything it sheds in `anvil.stats()`.

## Why one container runs both processes

Compose runs the API and worker separately; a Space gives you one container, so
`start.sh` runs both and **exits if either dies**. That rule matters more than it
looks. Ingest never writes to Postgres — it returns 202 once a trace is in Redis
and the worker persists it. A dead worker beside a live API therefore produces a
demo where signup works, traces are accepted, every response is a 2xx, and
nothing ever appears in the dashboard, with no error anywhere pointing at the
cause. Taking the container down instead makes the failure visible.
