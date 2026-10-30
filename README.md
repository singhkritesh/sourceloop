# SourceLoop

> Agentic direct-materials sourcing on a trusted network: a proof of concept for SAP Business Network (Track 1). Synthetic data only.

A prototype of agentic direct-materials sourcing on a trusted network (SAP Business Network, Track 1). A **buyer agent** and a **supplier agent**
(each with private subagents) take a need through discovery, qualification, sourcing (RFQ plus bounded negotiation), contracting and onboarding on
**synthetic data only**. A deterministic orchestrator is the neutral trust layer: state machine, message bus, mandate and policy enforcement,
hash-chained audit log and escalation to humans. Limits live in code, not prompts, and neither side can see the other's mandate.

**Status:** working prototype and test bed. Connectors to SAP and commercial data are stubs that fail closed; no real supplier or buyer data is used.

**New here and not a developer?** Read [docs/GETTING-STARTED.md](docs/GETTING-STARTED.md). Check your setup any time with `./doctor.sh`.

## Contents

- [What it does](#what-it-does) · [Architecture](#architecture) · [Quickstart](#quickstart) · [Run on a MacBook with a 4b model](#run-on-a-macbook-with-a-4b-model)
- [Demo commands](#demo-commands) · [Evaluation](#evaluation) · [Tests and checks](#tests-and-checks) · [Documentation](#documentation) · [Known limitations](#known-limitations)

## What it does

| Stage | What happens | Who decides |
|---|---|---|
| Discovery | Buyer agent shortlists suppliers from a 1,640-supplier synthetic registry | Code ranks; agent explains |
| Qualification | Certificates, sanctions (OFAC) and risk checks | `compliance.rules` only, never the LLM |
| Sourcing | RFQ and bounded negotiation; every turn is checked by a conduct judge | Mandate limits in code |
| Contracting | Clauses, signature, payment hold | Humans for locked clauses, award, signature, holds |

Key properties: limits live in code and neither side sees the other's mandate; every action is written to a hash-chained audit log before it takes effect;
runs are deterministic in replay mode; local models are loaded only while a task runs.

## Architecture

```
 React UI (web/, :3010) --/api--> FastAPI (:8005) -- ProjectService --+-- SQLite (Alembic)  audit_event (hash chain), projects, approvals,
   Studio forms, workspace,        auth, SSE, approvals, Studio       |                      config_version (immutable, pinned per project)
   approvals inbox                 routers                            |
                                                                      v
        +----------------------- Orchestrator (trusted) -----------------------+
        |  RunExecutor state machine . policy/ (pure mandate checks) . bus/     |
        |  signing . audit/ (audit first, then side effects) . approvals        |
        +-----------+-----------------------------------------+-----------------+
                    | AgentChannel only                       | AgentChannel only
            +-------v--------+                        +-------v---------+
            | Buyer lead     |   messages (signed)    | Supplier lead   |   untrusted side: no imports of data/policy/orchestrator
            | + ToolRunner   | <--------------------> | + ToolRunner    |   (import-linter enforces it)
            +-------+--------+                        +-------+---------+
                    | tools (subagents/, trusted impls)       |
            research, compliance, news(LLM), weather,   capacity, quote, docs, buyer-verify
            landed cost, ...                                   |
                    +------------- Ollama (host, optional, live mode only) via net/safe_http
```

Humans stay in the loop only where it is truly needed, and it is always the buyer user: a flagged supplier, the award, a changed locked clause
(liability, termination, ip) and one final decision that confirms bank details and releases the payment hold. Technical pass/fail and signature are
settled by rules and recorded in the audit log. Admins set rules and mediate; they never approve. These are code-owned (`studio/invariants.py`); no Studio field can disable them.

## Quickstart

Requires Python 3.13 and Node for the UI.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env                       # optional in dev; see the file for settings
.venv/bin/alembic upgrade head
.venv/bin/python -m sourceloop.seed        # prints RANDOM dev credentials once (one user per role); copy them, they are not stored in clear
scripts/start.sh                           # alembic upgrade + API on http://127.0.0.1:8005 (foreground)
curl -s localhost:8005/api/v1/health       # {"status":"ok","db":true,"llm_mode":"replay"}

cd web && npm ci && npm run dev            # UI on http://127.0.0.1:3010, proxies /api to 8005
```

Log in with the credentials the seed command printed. Runs default to `replay` mode (deterministic, no LLM). Admins can switch the project LLM mode
to `live` in Settings (needs a reachable local Ollama with the configured models, default fast model `gemma4:e2b-mlx`).

## Run on a MacBook with a 4b model

```bash
./install-mac.sh          # installs missing tools, pulls qwen3.5:4b, builds, starts, seeds; app on http://localhost:3010
./install-mac.sh --live   # same, and new projects use the local model instead of recorded answers
```

Needs 8 GB RAM (16 GB comfortable) and about 10 GB disk. The login page is a role picker (no password). Passwords for the optional password sign-in are in `data/demo-creds.txt`. Stop with `./stop.sh`.

## Supply chain

`requirements.lock` (runtime) and `requirements-dev.lock` (runtime + dev) are the pinned source of truth (uv is not used). Install exactly:
`pip install -r requirements-dev.lock && pip install --no-deps -e .`. After changing `pyproject.toml` dependencies, install them in a clean venv and run
`python scripts/lock.py` to regenerate; `python scripts/lock.py --check` verifies. CI (`.github/workflows/ci.yml`) runs the lock check, ruff, import-linter,
pytest (incl. adversarial), `alembic check`, bandit, pip-audit, `npm audit --audit-level=high` and uploads CycloneDX SBOMs; Dependabot is weekly
(`.github/dependabot.yml`). CI cannot be exercised locally.

## Demo commands

```bash
.venv/bin/python -m sourceloop.demo happy          # prints the orchestrator timeline
# other scenarios: no_deal  below_floor  locked_clause  poisoned_news  inflated_claims
```

## Evaluation

```bash
.venv/bin/python -m sourceloop.eval --seeds 42 43 44            # baseline vs agentic, ablations; writes data/eval/{results.json,evaluation.md}
.venv/bin/python -m sourceloop.eval --no-ablations --seeds 42   # quicker
.venv/bin/python -m sourceloop.eval --live                       # news extraction through local Ollama; non-deterministic, no ablations
```

Results are discussed in `docs/evaluation.md`; the adversarial suite is described in `docs/adversarial-report.md`.

## Tests and checks

```bash
.venv/bin/ruff check src tests
.venv/bin/lint-imports                 # trust-boundary contract must be KEPT
.venv/bin/pytest -q                    # 761 passed, 1 xfailed (the xfail is the open LIMIT-1 defect)
cd web && npm test && npm run build    # vitest (39 tests) + tsc strict + vite build
```

Release check against the running Docker stack: `scripts/e2e-all.sh` (lint, import boundaries, backend and web tests, then the headless-Chrome journey `web/scripts/e2e-simple.mjs`: role picker, typed need, deal, the buyer's few decisions, the supplier's own confirmation, agent performance, help). It uses the local 4b model.

`tests/test_live_ollama.py` runs only when a local Ollama with the model is reachable (otherwise skipped); the API live-path check is manual.

## Documentation

| Doc | Read it for |
|---|---|
| `docs/GETTING-STARTED.md` | Install, first run and troubleshooting in plain language |
| `docs/PRD.md`, `docs/TRD.md` | Product intent and implementation detail (each ends with an implementation status) |
| `docs/01-interaction-model.md`, `docs/02-platform-primitives.md` | Stages, messages, checkpoints |
| `docs/03-connectors.md`, `docs/04-data-dictionary.md` | Data sources and field-by-field map |
| `docs/05-roles-and-perspectives.md` | Who sees what |
| `docs/api-studio.md`, `docs/ui.md` | No-code configuration API and the UI |
| `docs/security-mapping.md`, `docs/security-roadmap.md`, `docs/adversarial-report.md` | Threat model, status, attack suite |
| `docs/evaluation.md` | Evaluation harness and metrics |
| `CLAUDE.md` | Rules for AI coding assistants working in this repo (also a good summary of the design rules) |

## Known limitations

* Runtime state (agents, bus, approvals) is in memory, but a restart no longer loses a replay-mode project: on boot `ProjectService.recover()` rebuilds it by deterministic replay (pinned config + the stored human decisions) and verifies every regenerated audit record against the stored chain, so the project comes back at its checkpoint and is resumable. Replay-mode runs execute their fan-outs sequentially to make that possible (`agents/common/par.py`). What cannot be reproduced is closed as `STOPPED` (outcome `FAILED`, reason `RESTART_LOST_RUNTIME`; the audit event carries the reason code; the UI shows "lost on restart"): live-AI runs, runs started before sequential mode existed, runs whose decisions, config, mandate or screening watchlist changed, and any run that does not replay to the stored chain. Never-started drafts survive a restart untouched.
* Docker: `./install.sh && ./start.sh` builds and runs api (8005) + web/nginx (3010); verified 2026-10-23 (login, authenticated call, project run and SSE through nginx). Postgres is not containerized; SQLite lives in the `sl_data` volume.
* MacBook (4b model): `./install-mac.sh` installs what is missing (Homebrew, Docker Desktop, Ollama), pulls `qwen3.5:4b`, points the first Studio config at it (`SL_FAST_MODEL` / `SL_CHAT_MODEL` in `.env`, read by the seed only) and starts the app. Needs 8 GB RAM (16 GB comfortable) and about 10 GB disk. Flags: `--live` (new projects use the model), `--model <name>`, `--no-start`.
* LIMIT-1 is an open policy decision: a watchlist name with a whole token dropped is not matched (`docs/adversarial-report.md`).
* Provider keys are encrypted with `SOURCELOOP_VAULT_KEY`; without it a loud, fixed dev key is used, so do not store real keys in a dev database.
* News and weather are synthetic; `data_sources.egress_allowlist` is validated but no fetcher consumes it. Some Studio fields are informational
  (listed in `docs/api-studio.md`).
* SQLite is the tested database; Postgres compatibility is untested.

## Data connectors

`docs/03-connectors.md` maps each data need to a source. Real today: OFAC SDN sanctions (`python -m sourceloop.connectors sync-ofac`) and an opt-in Open-Meteo weather signal. SAP Business Network, credit-risk, certificate and Icertis connectors are documented stubs that fail closed. The demo's "missing data" flags are deliberate synthetic gaps.

## Roles and perspectives

Every login sees a project from its own side only. The buyer user gets a business view: the need, the candidate suppliers and how they screened, offers, the award, the contract and the human decisions. They never see the agent workflow. Suppliers see only their own thread. The workflow, and how each agent behaved, is for inspectors: **SBN's super admin and evaluator** (everywhere) and an **organization admin** (its own side). The organization admin manages its own organization's people and tunes its own agents (versioned, validated, never touching safety rules). Details and the full matrix: `docs/05-roles-and-perspectives.md`.

