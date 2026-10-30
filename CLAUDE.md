# CLAUDE.md — SourceLoop (SAP Business Network, Track 1 agentic sourcing prototype)

## 1. Project Overview

SourceLoop is a prototype where a **buyer agent** and a **supplier agent** (each with private subagents) take a direct-materials sourcing need through discovery → qualification → sourcing (RFQ + bounded negotiation) → contracting, on **synthetic data only**. A **deterministic orchestrator** is the neutral SBN trust layer: state machine, message bus, mandate/policy enforcement, hash-chained audit log, escalation to humans. The architecturally distinctive rule: **limits live in code, not prompts, and neither side can see the other's mandate.**

When in doubt: `docs/PRD.md` for product intent, `docs/TRD.md` for implementation detail, `docs/01-interaction-model.md` for stage/message design.

## 2. Where Things Live

```
SAP/
├── CLAUDE.md  README.md          ← this file / quickstart + architecture
├── docs/
│   ├── 03-connectors.md  04-data-dictionary.md  ← data sources and the field-by-field map (tags, values, where used, blanks)
│   ├── 05-roles-and-perspectives.md  ← who sees what, org admin vs super admin, org overrides
│   ├── PRD.md  TRD.md            ← read before adding features (each ends with an "Implementation status" section)
│   ├── 01-interaction-model.md  02-platform-primitives.md   ← stages, messages, checkpoints, primitives
│   ├── api-studio.md             ← Studio API, pinning, wired vs informational config fields
│   ├── ui.md  evaluation.md  adversarial-report.md
├── src/sourceloop/
│   ├── orchestrator/             ← state machine, RunExecutor, approvals, scenario builder     (trusted)
│   ├── bus/  policy/             ← message schemas/signing/inbox (per-run agent capabilities, quarantine); pure mandate/platform rule functions; policy/judge.py = negotiation conduct judge (trusted)
│   ├── audit/                    ← hash-chained log; canonical.dumps only
│   ├── agents/{buyer,supplier,common}/  ← lead agents (UNTRUSTED side; see rule 1); common/contracts.py = typed tool IO, common/tools.py = ToolRunner (per-agent `set_allowed` allowlist, `NOT_ALLOWED`), common/provenance.py = per-call model provenance collector
│   ├── connectors/               ← data connectors (trusted): typed contracts, catalog, OFAC + Open-Meteo adapters, SAP/commercial stubs (fail closed); `python -m sourceloop.connectors list|sync-ofac|probe-weather`; see docs/03-connectors.md
│   ├── subagents/                ← TRUSTED-side tool implementations (buyer_tools.py, supplier_tools.py)
│   ├── compliance/               ← rules.py (qualification), resolution.py (entity resolution), scoring.py — pure functions
│   ├── llm/                      ← provider abstraction (providers.py), client/replay, prompt_builder.py, prompts_default.py
│   ├── data/                     ← models.py, enums.py, db.py, synthetic generators, audit_store.py
│   ├── api/                      ← app.py, service.py (ProjectService), deps.py, studio_support.py, sse.py, routers/{auth,projects,approvals,settings,studio,suppliers,health}.py
│   ├── studio/                   ← schema.py (StudioConfig), validator.py, invariants.py, lifecycle.py (versions/publish/resolver), sql_store.py, vault.py, discovery.py, rules.py, defaults.py
│   ├── eval/                     ← evaluation harness: `python -m sourceloop.eval` (suite, ablations, baseline, metrics, report)
│   ├── demo.py  seed.py  main.py ← `python -m sourceloop.demo <scenario>`; `python -m sourceloop.seed`; uvicorn app `sourceloop.main:app`
│   ├── net/safe_http.py          ← only allowed outbound HTTP client
│   └── security/                 ← text sanitizer, injection detector
├── web/                          ← React 18 + Vite + TS (dev port 3010); src/{pages,studio,api,lib,state}; scripts/smoke*.mjs
├── scripts/start.sh              ← runs `alembic upgrade head` then uvicorn on 127.0.0.1:8005
├── tests/                        ← pytest; adversarial/ holds the 18-case suite (strict xfail for the open LIMIT-1)
├── alembic/                      ← migrations (DO NOT hand-edit applied versions)
├── data/replay/                  ← recorded LLM/news/weather responses (keys are hashes; don't rename)
├── install-mac.sh doctor.sh      ← MacBook one-command setup (4b model, writes SL_FAST_MODEL/SL_CHAT_MODEL for the seed) / health checklist with fixes; docs/GETTING-STARTED.md is the non-technical guide
├── install.sh build.sh start.sh stop.sh ← new-machine setup (installs only what is missing) / `build.sh` builds images + creates containers / `./start.sh [--local]` / stop + unload Ollama
├── Dockerfile  docker-compose.yml  web/{Dockerfile,nginx.conf} ← api (8005→8000) + web nginx (3010→8080, proxies /api, SSE unbuffered); verified 2026-10-23
├── requirements.lock / requirements-dev.lock  ← pinned Python deps (source of truth; `scripts/lock.py` generates/--check; no uv in this repo)
├── .github/                      ← workflows/ci.yml (lock check, ruff, lint-imports, pytest, bandit, pip-audit, npm audit, CycloneDX SBOMs) + dependabot.yml
├── llm.config.yaml               ← gitignored; copy from llm.config.example.yaml
└── .env                          ← gitignored; copy from .env.example
```

Do not modify manually: applied Alembic revisions, `data/replay/*` (regenerate with `LLM_MODE=record`), `requirements*.lock` (regenerate with `scripts/lock.py`), `package-lock.json` (use npm).

## 3. Stack & Key Packages

Python 3.13 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) + Alembic · SQLite (dev) / Postgres-compatible · React 18 + Vite + TS · TanStack Query · Zustand · Ollama on host.

- `argon2-cffi` for passwords. ⚠️ Not bcrypt, not passlib defaults.
- `rapidfuzz` for entity resolution/watchlist similarity. No embeddings in v1.
- `slowapi` rate limiting. `structlog` JSON logs. `httpx` for outbound (via `net/safe_http` only).
- `import-linter` enforces the trust boundary. `ruff` (incl. `S` rules) + `bandit` + `pip-audit` in CI.
- ⚠️ Ollama: use direct `httpx` for `keep_alive: 0` — not the Ollama SDK (serialization bugs #401/#492).
- ⚠️ Money is `Decimal` / `Numeric(14,4)`. Never `float`.
- Package manager: `uv` (install if missing) for Python; `npm` for web.

## 4. Data Model Quick Reference

Full schema: TRD §3. Enums live in `data/enums.py` — import them, never type strings.

- `project` (UUID, `stage`, `status`, `version` optimistic lock) · `project_supplier` (composite PK `(project_id, supplier_id)`, no surrogate id)
- `mandate` — immutable, versioned, signed. ⚠️ never UPDATE.
- `message` — unique `(project_id, seq)`; `offer`; `subagent_call`; `approval` (version-locked); `ledger_hold`; `contract`; `clause` (composite PK `(key, version)`)
- `audit_event` — INSERT only, `prev_hash`/`hash` chain. ⚠️ hash input must come from `audit.canonical.dumps` (sorted keys, no whitespace) or `audit/verify` silently breaks.
- Registry: `supplier`, `certificate` (`expires_on` DATE, UTC), `watchlist_entry`, `transaction_history`, `buyer_profile`, `index_price`.
- ⚠️ UUID PKs are **client-side** (`default=uuid.uuid4`); no DB default exists.
- ⚠️ Enum strings are exact (`ProjectStatusEnum`, `StageEnum`, …). A wrong literal in `WHERE` returns zero rows with no error.
- ⚠️ SQLite needs `PRAGMA foreign_keys=ON` per connection (already in `data/db.py`; don't bypass it).

## 5. API Patterns

- Base `/api/v1`; cookie auth (`HttpOnly`); mutations need `X-CSRF-Token` equal to the `csrf` cookie.
- Error envelope (always): `{"error":{"code":"MANDATE_INVALID","message":"…","request_id":"…"}}`
- Cross-tenant/side object → **404**; wrong role on a visible object → **403**.
- Validation → 422; oversize → 413; state conflict / version mismatch → 409; rate limit → 429 + `Retry-After`.
- SSE: `GET /projects/{id}/stream`; events `stage_changed, message, subagent_call, subagent_result, policy_block, escalation_created, approval_resolved, audit_event, run_finished, error`; SSE `id` = audit `seq`; honor `Last-Event-ID`; 15 s heartbeat.

## 6. Authentication & Authorization

Roles: `BUYER, SUPPLIER, ADMIN, SUPER_ADMIN` are the ones the app creates and offers (the login page is a role picker, `DEMO_ROLE_LOGIN`, never in prod). `COMPLIANCE, QUALITY, LEGAL, FINANCE, EVALUATOR, DESIGNER` remain in the enum for old data and tests but own no checkpoint. **Every human checkpoint belongs to the BUYER user (`orchestrator/approvals.py KIND_ROLE`); admins set rules and mediate, they never approve.** Middleware = `api/deps.py`.
**`ADMIN` is an ORGANIZATION admin (one buyer or supplier org); `SUPER_ADMIN` is SBN's platform operator (all access).** Every login sees a project from its own side only: business users (buyer-side roles, suppliers) get `/projects/{id}/overview` and are refused the raw agent workflow (`/messages`, `/audit`, `/stream`, `/activity` = 403); inspectors (`SUPER_ADMIN`, `EVALUATOR`, and an `ADMIN` for its own organization's side) get the workflow. Use `deps.supplier_scoped(p)` (a supplier user OR a supplier org's admin) instead of `p.role is R.SUPPLIER`, and `deps.INSPECTORS` for workflow routes. Full matrix: `docs/05-roles-and-perspectives.md`; `tests/test_authz_matrix.py` is the spec: a new route must be added there.

```python
@router.get("/projects/{pid}")
async def get_project(pid: UUID, user: User = Depends(require_role("BUYER", "EVALUATOR")),
                      db: AsyncSession = Depends(get_session)):
    stmt = select(Project).where(Project.id == pid)
    if user.role == "BUYER":
        stmt = stmt.where(Project.buyer_org_id == user.org_id)      # scope in SQL, not after fetch
    project = (await db.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise ApiError(404, "NOT_FOUND")                            # never 403 for cross-tenant
    # EVALUATOR: read-only everywhere; SUPPLIER: only projects where its org is in project_supplier
    return ProjectOut.model_validate(project)
```

Auth failure: 401 `{"error":{"code":"UNAUTHENTICATED"}}`. Refresh is deduplicated client-side (one shared promise for concurrent 401s).

## 7. Critical Rules

1. **Agents import only `bus.bus.AgentChannel`/messages, `data.enums` and `agents.common`.** Tool *implementations* live in `subagents/` (trusted) and are handed to agents as a `ToolRunner`. Importing `data`, `policy` or `orchestrator` from `agents/` lets an agent read the counterparty mandate. `import-linter` fails CI; don't add exceptions.
2. **Mandate checks are pure functions in `policy/`.** `check(message, sender_mandate) -> PolicyResult(passed, rule_id)`. Prompts can be talked around; code can't.
3. **Predicates, not thresholds, cross the side boundary.** Return `passed` + `rule_id`; never "price must be ≥ X". It leaks the floor/ceiling.
4. **All arithmetic in code with `Decimal`** (landed cost, margin, bands, steps). LLM math is silently wrong; float rounding flips limit comparisons.
5. **Fail closed.** Validation/parse/timeout error → `confidence=0` and escalate/reject. Blank or unknown never means pass (the ablation showed "blank = pass" lets bad suppliers through).
6. **Only `compliance.rules` sets `QualStatus.QUALIFIED/BLOCKED`.** Never from LLM output. Watchlist hits are never auto-cleared.
7. **External text is untrusted.** Wrap in `<untrusted_data>` in the *user* turn, extract to a schema with a required source; never into a system prompt or as free text into later prompts. This is the prompt-injection channel.
8. **LLM prices must be one of code-supplied options.** Anything else is rejected, otherwise output trust = mandate bypass.
9. **Strict Pydantic (`extra="forbid"`)** on bus messages, subagent outputs and external data. Extra keys are how injected fields get through.
10. **Audit first, then side effects.** Append `audit_event`, then deliver the message / emit SSE. A delivered-but-unlogged action cannot be proven.
11. **Scope by SQL.** `where(Project.id == pid, Project.buyer_org_id == user.org_id)`. Post-fetch filtering gets forgotten; 403 leaks existence.
12. **Claim-then-process runs.** Commit `status=RUNNING` in a short transaction, do LLM work outside it. One asyncio task per project. Holding a transaction across LLM calls starves everything and locks SQLite.
13. **Re-throw after recording a failure.** Swallowed errors make the run look healthy in logs while the DB says failed.
14. **Determinism.** Seeded `random.Random(seed)`, injected `Clock` (no `datetime.now()` in decision code), canonical JSON. Acceptance test: identical audit hash in replay mode **only with fixed project/user ids and a FixedClock** (`test_same_scenario_three_times_identical_audit_hash`). Through the live API, ids and timestamps differ per run, so head hashes differ; the decision sequence is identical (`scripts/e2e-determinism.py`). **Restart recovery depends on it:** a replay-mode project is rebuilt after a restart by replaying it against the stored chain (`ProjectService._restore`, `audit/replay.py`), so replay-mode runs must stay deterministic. Fan-outs go through `agents/common/par.gather` (sequential in replay mode, because concurrent event order depends on database I/O timing). Never use bare `asyncio.gather` in agent/orchestrator code that appends audit events, and never read `set` iteration order, wall-clock time or unseeded randomness in decision code. Live-mode runs are not restorable.
15. **Single DB engine** from `data/db.py`. DB-stored settings (`app_setting`, fixed id `1`, upsert) before env fallback. Extra engines leak connections; env-only reads make the admin UI a no-op.
15a. **The app is configured from the UI (Studio), not files.** Models, providers, agents, prompts, workflow, rules, templates, clauses, data sources and scenarios are fields on `studio.schema.StudioConfig`. The runtime reads them **only from the project's pinned `config_version`** — never "active config" mid-run, never env/YAML for model choice after bootstrap. Mid-run reads change behavior of a running project and break replay.
15b. **Invariants are code.** Safety properties live in `studio/invariants.py`, enforced by the publish validator *and* at runtime. Never add a Studio field that can disable one (audit chain, signing, mandate enforcement, mandate secrecy, watchlist no-auto-clear, rules-only qualification, locked clauses `liability/termination/ip`, buyer-only hold release (admins never approve), untrusted wrapper, fail-closed).
15c. **Published configs are immutable;** edit drafts, publish creates a new version, rollback = re-validated copy. Every Studio mutation appends a `studio.*` audit event.
15d. **Provider keys are write-only.** Store via `studio.vault` (encrypted), return `has_key: bool`, never in exports/diffs/logs/SSE/errors. Provider URLs are SSRF input: go through `safe_http`; private ranges only for `is_local` providers set by ADMIN.
16. **Ollama:** thinking control via `_ollama_no_think_kwargs(model)` → `think:false` in the native `/api/chat` body for thinking-capable families (gemma4, qwen3.x). ⚠️ `reasoning_effort` is ignored by the native endpoint (verified on this install): Gemma keeps thinking, burns `num_predict` and returns empty content. JSON/tool calls non-streaming; release VRAM with direct httpx `keep_alive:0`; every system prompt starts `Respond only in English.`
16a. **Local-model testing limits (owner rule):** only `qwen3.5:4b` and `gemma4:e2b-mlx` / `gemma4:e4b-mlx` / `gemma4:12b-mlx` may be used for tests and experiments (qwen up to 4b, gemma up to 12b; every other installed model is off limits; `scripts/jev_concept.py` refuses them). **A model is loaded only while its task is actually running:** unload (`keep_alive:0`) the moment the task completes, fails, pauses for a human or is cancelled (`ProjectService._release_models` runs first in `_drive`'s `finally` and again in `shutdown`; scripts use `try/finally`); every Ollama request also carries `keep_alive:30s` as a backstop. After any manual test check `curl -s localhost:11434/api/ps` is empty.
17. **Prompts are built only by `llm/prompt_builder.py`.** The user-editable part is the instruction *body*; the English preamble, `<untrusted_data>` wrapper and output-schema suffix are code-owned and appended after it. Inline prompts escape injection/PII review; a UI body that could replace the wrapper makes the Studio an injection tool. Replay key includes the body hash.
18. **Never return or log secrets.** Config endpoints return `"****"`. No tokens, passwords, keys, cross-side thresholds, or raw web text in logs.
19. **Outbound HTTP only via `net/safe_http`** (host allowlist, block private IPs, no auto-redirects, 2 MB cap). Supplier-supplied URLs are never fetched.
20. **Scope guardrail:** touch only `/Users/kritesh/Code/SAP`. If a task needs another repo/dir, stop and ask. **Port rule:** report any new port (API 8005, web 3010 today) so the user can update their global registry.
21d. **Agents choose their own moves (live runs).** `agents/common/deliberate.py`: the CODE builds the menu of legal moves (prices included, inside the agent's own mandate, rule-based move FIRST) and a `Deliberator` picks one. In a live run `subagents/deliberation.py` (`LLMDeliberator`) lets the fast model pick by typed decision (option probabilities); below `MIN_MASS`/`MIN_CONFIDENCE` or on any error the rule move stands and the reason is recorded. Every choice is audited as `agent_decision` BEFORE the agent acts (codes and probabilities, never prices). Studio `workflow.negotiation.llm_moves` (default on) switches it; replay/tests use `RuleDeliberator`. The award recommendation uses the same mechanism; the human still decides. `GET /projects/{id}/agent-performance` (inspectors) measures model-vs-rule. Never let a model name a price or an action that is not on the menu.
21c. **The typed need drives a live run.** `subagents/need_intake.py` has the fast model read the buyer's text (wrapped as untrusted, schema-extracted, every value checked against the registry's own lists); an unclear need fails with 422 `NEED_UNCLEAR`, it never falls back to a sample. Replay mode (tests) still builds from the sample needs. Default runtime mode for installs is `live` with a 4b local model.
21a. **Every negotiation turn goes through the judge** (`policy/judge.py`, called from `RunExecutor._judged`): audit `negotiation_judged` first, then deliver. Verdicts cross the side boundary as codes and plain sentences, never prices. A reviewer LLM may only add a concern (`combine`), never end a negotiation or soften a code verdict.
21b. **Bus identity:** channels are minted a per-run capability (`MessageBus.enable_identity` must be called in `build_project` before any `AgentChannel` exists). Test-bed scope: no MFA, no key rotation (docs/security-roadmap.md).
21. **Clarify before changing** the stage/message model, mandate shape or trust boundary; update `docs/` in the same change.

## 8. Common Patterns with Examples

**New authenticated route** — checklist: `require_role`, side-scoped query, Pydantic body, entry in `tests/test_authz_matrix.py`, audit event if it mutates.

```python
class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["APPROVED", "REJECTED", "EDITED"]
    comment: str = Field(default="", max_length=1000)
    version: int

@router.post("/approvals/{aid}/resolve")
async def resolve(aid: UUID, body: ResolveBody, user=Depends(require_approver), db=Depends(get_session)):
    if body.status != "APPROVED" and len(body.comment.strip()) < 10:
        raise ApiError(422, "COMMENT_REQUIRED")
    return await approvals.resolve(db, aid, user, body)   # raises 409 on version mismatch
```

**Calling a subagent from a lead agent** (timeout, retry, dedupe, logging handled for you):

```python
res = await tools.call("regional_news_risk", NewsInput(region="VN", horizon_days=90))
if res.confidence < 0.6 or not res.sources:
    return Escalate(kind="LOW_CONFIDENCE", reasons=["news_risk_low_confidence"])
```

**Subagent result shape**

```python
class SubagentResult(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    result: T
    confidence: float = Field(ge=0, le=1)
    sources: list[Source] = []
    assumptions: list[str] = []
```

**LLM call (schema-bound)**

```python
out = await llm.complete_json(role="fast", template_id="news_extract_v1",
                              system=prompts.NEWS_EXTRACT_SYSTEM,
                              user=prompts.wrap_untrusted(page_text), schema=NewsSignals)
```

**Emit an audited event + SSE**

```python
seq = await audit.append(db, project_id, actor_type="orchestrator", action="stage_changed",
                         rule_id=None, evidence=[], outcome="ok")
await broker.publish(project_id, Event("stage_changed", seq, {"from": a, "to": b}))
```

**Policy check before delivery**

```python
verdict = policy.check(msg, sender_mandate)
if not verdict.passed:
    await escalate(db, project, kind="MANDATE_BREACH", reasons=[verdict.rule_id])  # message NOT delivered
    return
await bus.deliver(msg)
```

**Adding a configurable option (the only way to add UI config)**

```python
# 1. studio/schema.py — add the field; widget hint drives the UI, no frontend code needed
class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    timeout_s: int = Field(20, ge=1, le=120, json_schema_extra={"widget": "number"})
    model_override: str | None = Field(None, json_schema_extra={"widget": "model_picker"})

# 2. studio/validator.py — add a check if it interacts with others (dependencies, invariants)
# 3. consume it at runtime ONLY via the pinned version:
cfg = await configs.get(project.config_version_id)      # immutable, cached
timeout = cfg.agents[agent_key].timeout_s
```

## 9. Anti-Patterns — What Not To Do

- Don't read model/provider/limit settings from env, YAML or "active config" at runtime → pinned `config_version`.
- Don't hand-build a one-off settings form → extend `StudioConfig` + widget hint.
- Don't let a config field weaken an invariant → add/extend an invariant check instead.
- Don't return a provider key (even masked with part of its value) → `has_key` / `"****"`.
- Don't edit a published config row → new version.

- Don't let an agent module import `data`/`policy`/`orchestrator` → use `bus.client` (trust boundary).
- Don't put limits, floors or ceilings in a prompt → `policy/` functions.
- Don't use `EventSource` → `fetch` streaming (custom headers/CSRF, reconnect control).
- Don't use `float` for prices → `Decimal`/`Numeric`.
- Don't use `datetime.now()` / unseeded `random` in decision code → `Clock` and seeded RNG (replay determinism).
- Don't write raw SQL strings → ORM or `text()` with bound params.
- Don't `session.merge`/UPDATE `mandate` or `audit_event` → insert a new version / new event.
- Don't return 403 for another org's object → 404.
- Don't treat missing data as pass → `REVIEW` + escalate.
- Don't let LLM output set qualification status, clause approval, or state transitions.
- Don't concatenate web/news text into prompts → `wrap_untrusted()` + schema extraction.
- Don't fetch supplier-provided URLs or use bare `httpx` → `net/safe_http`.
- Don't stream tool/JSON calls from Ollama → non-streaming.
- Don't use the Ollama SDK for `keep_alive` → direct httpx.
- Don't create engines/sessions outside `data/db.py`.
- Don't read user-changeable config from `os.environ` only → DB setting first.
- Human-in-the-loop only where it is truly needed (owner rule: minimal intervention, agent-to-agent). The remaining checkpoints are code-owned and the buyer user's: a flagged supplier (`COMPLIANCE_FLAG`, only when something is flagged), the award, a changed locked clause (`liability`, `termination`, `ip`, only when one is touched) and ONE final decision that confirms bank details and releases the payment hold. Technical pass/fail and signature are settled by rules and recorded as `checkpoint_skipped` (never silent); requirement confirmation is off by default (`workflow.checkpoints.requirement_confirmation`). Don't add a human gate for something a rule can settle, and don't let a config field switch off the ones that remain.
- Don't hardcode ports or add new ones silently → tell the user.

## 10. Verifying Your Work

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check src tests && .venv/bin/lint-imports   # trust boundary must stay KEPT
.venv/bin/python scripts/lock.py --check                  # locks match the installed env/pyproject
.venv/bin/pytest -q                                        # 911 passed, 1 xfailed (strict xfail = open LIMIT-1)
.venv/bin/python -m sourceloop.demo happy                 # also: no_deal below_floor locked_clause poisoned_news inflated_claims
.venv/bin/python -m sourceloop.eval --no-ablations --seeds 42   # evaluation harness (add --live for local Ollama news extraction)
.venv/bin/alembic upgrade head && .venv/bin/python -m sourceloop.seed   # seed prints RANDOM dev credentials once; save them
scripts/start.sh                                           # alembic upgrade + uvicorn on 127.0.0.1:8005 (foreground)
curl -s localhost:8005/api/v1/health                       # {"status":"ok","db":true,"llm_mode":"replay"}
(cd web && npm ci && npm test && npm run build && npm run dev)   # vitest 30 tests; dev server on 3010, proxies /api to 8005
# Docker: ./install.sh && ./start.sh  (app on http://localhost:3010; demo logins in data/demo-creds.txt); ./stop.sh to stop.
```

Ports: API **8005**, web dev/preview **3010** (both already in the user's registry process; report any new one).

End-of-session minimum (user rule): services start cleanly -> health ok -> login works -> one authenticated request succeeds. Login manually
with the credentials the seed script printed (they are random per run and not stored anywhere else):

```bash
curl -s -c jar -X POST localhost:8005/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"<seeded buyer email from seed output>","password":"<seeded password from seed output>"}'
curl -s -b jar localhost:8005/api/v1/auth/me
```
(Placeholders above must be replaced.)

Replay determinism check: the same scenario run repeatedly in replay mode must give the same `GET /projects/{id}/audit/verify` head hash
(covered by `tests/test_api_flow.py` and `tests/test_studio_api.py`). Live mode (Ollama) is checked by hand, never in pytest.

Healthy log lines: `stage_changed`, `policy.block` (only in adversarial runs), `run_finished`. Red flags: `FAILED_RECOVERABLE`, `injection.suspected` on benign data, `audit.verify valid=false`.
