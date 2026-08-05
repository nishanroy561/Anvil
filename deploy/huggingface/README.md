---
title: Anvil
emoji: ⚒️
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Observability and evaluation for LLM features — hosted demo
---

# Anvil — hosted demo backend

The API and worker for [Anvil](https://github.com/nishanroy561/Anvil), running as
a single Docker Space. The dashboard is deployed separately (Vercel) and talks to
this Space through its `/api` proxy.

Data is shared and may be reset. To run the whole thing yourself, clone the repo
and `docker compose up` — that is the only install path and it gives you the
entire product.

## Required secrets

Set these under **Settings → Variables and secrets**. The two connection strings
are the ones most likely to be wrong; see the notes.

| Secret | Value |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://USER:PASSWORD@HOST/DB?ssl=require` |
| `REDIS_URL` | `rediss://default:TOKEN@HOST:6379` |
| `SECRET_KEY` | any long random string |
| `WEB_ORIGIN` | `https://your-dashboard.vercel.app` |
| `RETENTION_DAYS` | `7` |

**`DATABASE_URL`** — Neon's console gives you a psycopg-style URL. Three edits:
`postgresql://` → `postgresql+asyncpg://`, `?sslmode=require` → `?ssl=require`,
and delete `&channel_binding=require`. asyncpg rejects both `sslmode` and
`channel_binding` with a `TypeError` *before it opens a socket*, so a URL copied
verbatim fails with an error that looks nothing like a connection problem.

**`REDIS_URL`** — Upstash requires TLS, so the scheme is `rediss://` with two
s's. With `redis://` the client builds a plaintext connection that can never
succeed, and the API logs `backlog gauge refresh failed; Redis unavailable` every
ten seconds forever.

**`RETENTION_DAYS`** — unset means *keep every trace forever*, which on a free
Postgres tier with a public demo means the database fills and the demo dies
quietly. 7 is a sensible demo window; the worker sweeps daily in batches.

**No provider API keys.** Do not set `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` or
`GROQ_API_KEY` here. Any visitor can sign up and start an eval run, and with a
key present they would be spending your credits — up to `MAX_EVAL_ITEMS` per run.
Without one, the LLM judges report `no judge key configured` per item and the run
still completes; the deterministic graders need no key and demo the loop fine.

## Verifying

```
curl https://YOUR-SPACE.hf.space/healthz
```

`{"db":true,"redis":true}` means both connection strings are right. Anything else
and the rest cannot work, so fix that before looking further.
