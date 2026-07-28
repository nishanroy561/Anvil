# ADR-0021 — Eval and dataset APIs accept either a session or an API key

Status: **Accepted** — 2026-07-28 · Spec: [../evaluation.md](../evaluation.md) §5

## Context

Phase 7 puts an eval run in CI: a GitHub Action creates a run, waits for it,
and fails the check on a regression. But every dataset and eval-run route was
`Depends(current_user)` — session cookie only. The only way to authenticate a
CI job would be to store a **user's email and password** as repository secrets
and have the CLI log in.

That is the wrong credential in every respect. It is org-wide rather than
project-scoped, it is the same secret that logs into the dashboard, revoking it
locks a human out, and it cannot be rotated per-repository.

Meanwhile API keys already exist and already have exactly the right shape:
project-scoped, hashed at rest, revocable, listed by prefix, and already the
credential the ingest endpoint and both SDKs use.

## Decision

**Dataset and eval-run routes accept either credential.** A `Caller` carries
whichever one authenticated:

```python
class Caller(NamedTuple):
    user: User | None = None       # session cookie — org-wide reach
    project: Project | None = None # API key — exactly one project
```

`owned_project()` in `deps.py` resolves both and remains the single place the
"404, never 403" rule is enforced. It still accepts a bare `User`, so the
session-only routes — projects, API keys, traces, auth — did not change at all.

Two details that are load-bearing:

- **The API key is checked first.** If a request carries both a
  `Bearer ak_…` header and a session cookie, the key wins. The alternative
  silently downgrades a CI job to whatever browser session happened to be in
  the cookie jar, which would run as the wrong principal *and* succeed, so
  nobody would notice.
- **A malformed or revoked key is a 401, not a fallback.** A caller that
  presents a key means it. Falling through to the cookie on a bad key is the
  same failure with extra steps.

An API-key caller is confined to one project by construction — there is no
org-wide reach to get wrong, because the credential does not carry an org.

## Consequences

- CI holds `ANVIL_API_KEY`, a project-scoped secret it can rotate and revoke
  without touching a human account.
- The tenancy surface grew, so the tests grew with it: a key reaching another
  tenant's run, results, dataset, or run list all 404, and — the dangerous one
  — creating a run against another tenant's dataset 404s rather than spending
  the intruder's grader budget on the victim's data.
- Rate limiting is now uneven: ingest has a per-key limit, the eval surface
  does not. Phase 11's rate-limit sweep owns closing that. It is not urgent —
  eval runs already carry `MAX_EVAL_ITEMS` and `MAX_CONCURRENT_RUNS` spend
  guards, which bound the expensive part.
- Traces and projects stay session-only. Nothing needs them from CI yet, and
  widening auth on a route with no caller is surface for free.

## Alternatives rejected

**Store user credentials in CI secrets.** The status quo made this the only
option, and it is the reason this ADR exists.

**A separate CI token type.** A third credential to mint, store, revoke and
document, to do what API keys already do. Worth revisiting only if CI ever
needs permissions an API key should not have.

**A service-account user.** Gets project scoping wrong — a user belongs to an
org and can reach every project in it, which is more than a repository's CI
should hold.
