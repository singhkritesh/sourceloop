# Hosting SourceLoop

SourceLoop is a prototype on synthetic data. This page covers what it takes to run it somewhere other than your own machine, what has been
checked, and what has not. Nothing here has been deployed to a public host.

## Shape

| Part | Where | Why |
|---|---|---|
| Web (React, static, about 470 KB built) | Vercel, or any static host | Needs no server. |
| API (FastAPI) | A container host (Fly.io, Render, Railway, Cloud Run, a VM) | A negotiation is a background task that lasts minutes and keeps its state in memory, and the live view is a long-lived stream. Serverless functions fit neither. Run exactly **one** API instance (state is in memory; restarts lose live runs, and the buyer can "Run again"). |
| Database | PostgreSQL 14+ | SQLite is a local file and will not survive a serverless or ephemeral host. |
| Model | A hosted provider set up in Studio | The local Ollama of the demo is not reachable from a host. |

## Checked here, and not

Checked on this machine: the schema builds on PostgreSQL 16 and is stamped at the current revision; a full project (sourcing to payment release) ran on it
with a valid audit chain; the append-only protections (audit events, mandates, published configs) fire on PostgreSQL; the API starts in production mode
and refuses the demo role picker (404), hides the API docs, accepts a password sign-in and sets `Secure`, `HttpOnly`, `SameSite=Strict` cookies and HSTS.

**Not checked:** any real hosting platform; the Vercel rewrite carrying the live stream (confirm it, and its time limit, on your plan); a hosted model
provider; backups; more than one API instance (not supported); moving existing SQLite data to PostgreSQL (there is no tool: a hosted database starts empty).

## 1. Database
Create an empty PostgreSQL database and give the API `DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/DBNAME`.
On start the container runs `python -m sourceloop.data.migrate`. A brand-new PostgreSQL database is created from the models and stamped at the latest
revision (the first SQLite-era revisions repeat a constraint name that PostgreSQL refuses, so they are not replayed; applied revisions are never edited).
Every later revision runs normally. On SQLite it simply runs `alembic upgrade head`.
Local try-out with a database container: `POSTGRES_PASSWORD='<choose one>' docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d --build`.

## 2. API settings (production)
With `APP_ENV=prod` the API refuses to start unless these are set.

| Variable | Value |
|---|---|
| `APP_ENV` | `prod` |
| `DATABASE_URL` | as above |
| `JWT_SECRET` | 32 or more random bytes, for example `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `ORG_SIGNING_SECRET` | another, different 32+ byte secret (it signs mandates and messages; it never defaults to `JWT_SECRET`) |
| `SOURCELOOP_VAULT_KEY` | a Fernet key: `python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`. It encrypts provider keys. Keep it: lose it and stored provider keys are unreadable. |
| `CORS_ORIGINS` | your web address, `https://...`, no wildcard |
| `LLM_MODE` | `live` |
| `TRUSTED_PROXY_HOPS` | how many proxies sit in front of the API (so rate limits see the real client address) |
| `APP_BASE_URL` | the web address, used in links |
| `AUDIT_ANCHOR_PATH` | a file on a **persistent** disk (the external audit anchor) |

In production the role picker is off and people sign in with a password. Cookies are `SameSite=Strict` on path `/api/v1`, so the browser must reach
the API **through the same address as the web** (the Vercel rewrite below, or a reverse proxy). Calling the API from a different address directly will not keep a session.

## 3. First start and people
There is no "create the first admin" command yet. `python -m sourceloop.seed` loads the synthetic supplier registry and creates the demo accounts
(`superadmin@sourceloop.test` and others) with random passwords printed once. Run it once, sign in as the super admin with that password, create real
organizations and users from the admin screens, then change or disable the demo accounts. Treat this as a known gap before any real use.

## 4. Model
In Studio, add a provider (Anthropic or an OpenAI-compatible endpoint), save its key (write-only), set each agent's model, validate and publish. What
the agents read (needs, quotes, summaries) is sent to that provider. Today all data is synthetic; do not put real supplier or pricing data through a
provider you have not approved.

## 5. Web on Vercel
1. Import the repository; set the project's root directory to `web`.
2. In `web/vercel.json`, replace `REPLACE-WITH-YOUR-API-HOST` (a placeholder, left on purpose so it fails visibly) with the API's host name.
3. Deploy. Then set the API's `CORS_ORIGINS` and `APP_BASE_URL` to the Vercel address and restart the API.

## What must be true before real use
No MFA and no key rotation (`docs/security-roadmap.md`); one API instance only; no backups configured; demo accounts present; delivery of mail and
texts is the in-app mailbox, not a real provider; the bank check does not verify with a bank (`docs/ui.md`); data is synthetic.
