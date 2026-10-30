---
version: 0.1
last_updated: 2026-10-20
status: Draft for team review
theme: Agentic supplier sourcing on a trusted network
---

# PRD — Agentic Supplier Sourcing Prototype (SAP Business Network, Track 1)

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Target Audience & Personas](#3-target-audience--personas)
4. [Jobs To Be Done](#4-jobs-to-be-done)
5. [Scope & Prioritization](#5-scope--prioritization)
6. [Functional Requirements](#6-functional-requirements)
7. [Authentication & Authorization](#7-authentication--authorization)
8. [AI System](#8-ai-system)
9. [System Architecture](#9-system-architecture)
10. [Data Model / Key Entities](#10-data-model--key-entities)
11. [API Design](#11-api-design)
12. [Security & Privacy](#12-security--privacy)
13. [Non-Functional Requirements](#13-non-functional-requirements)
14. [Error States & Edge Cases](#14-error-states--edge-cases)
15. [Out of Scope](#15-out-of-scope)
16. [Open Questions & Risks](#16-open-questions--risks)
17. [Glossary](#17-glossary)

Companion docs: `docs/01-interaction-model.md` (stage/message design), `docs/TRD.md` (implementation), `CLAUDE.md` (coding-agent cheatsheet). Research basis: *Market research Notes – Meet 3* and *SAP_Track1_Research_Report* (Oct 13, 2026).

---

## 1. Executive Summary

**Product:** *SourceLoop* (working name) — a prototype in which a **buyer agent** and a **supplier agent**, each with private subagents, carry a direct-materials sourcing need from discovery through qualification, sourcing (RFQ + negotiation) and contracting, with a **deterministic orchestrator** acting as the neutral SBN trust layer. Humans approve at defined checkpoints only.

**Vision:** "Be the trusted network where buyer and supplier agents meet, with human approval at the points that matter."

**Interface model:** split-screen run viewer — buyer view | orchestrator timeline | supplier view — plus an approval queue and an audit log. **Everything is configured from the UI (the Studio, Epic H):** models and providers, agents and subagents, prompts, workflow stages, checkpoints, escalation rules, mandate templates, clause library, data sources and scenarios — no code or file edits. A small set of safety invariants is visible in the Studio but not editable.

> **Design over numbers.** The goal of this prototype is to prove the *design*: the interaction model, the platform primitives and the control model. Every numeric value in this PRD (limits, thresholds, timeouts, counts) is a **seeded default that is editable in the Studio**, not a requirement to hit. Market-size and time-saving figures from the research are context only.

**Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, SQLite (prototype) / PostgreSQL-compatible, React + Vite + TypeScript, pluggable LLM layer (local Ollama default; hosted API optional), Docker Compose.

> **Assumed:** stack choices are the team's to confirm; Python was chosen because the research already uses Python for the synthetic-data agent test.

---

## 2. Problem Statement

SAP told the team that taking a new direct-material supplier from first search to signed contract takes **6–12 months** (SAP, Oct 1 call). The team's stage model (Research Report §5) puts a 39-week base case across seven stages: Need 4 wks, Discovery 6, Pre-qualification 4, Technical qualification 14, RFQ/negotiation 5, Contracting 3, Onboarding 3. Most elapsed time is queueing, not work.

**How incumbents fail:**
- **Coupa + Scoutbee:** strong discovery and onboarding prefill, no clear negotiation or contracting agent.
- **Jaggaer (+ Ivoflow, closed Sept 2026):** negotiation is human-in-the-loop; its own blog puts agent-to-agent negotiation about five years out.
- **Alibaba Accio Work:** multi-supplier negotiation inside buyer limits, but built for SMBs on Alibaba's marketplace, not enterprise compliance or direct materials.
- **Ivalua, Zycus, Keelvar, Fairmarkit:** single-vendor suites; none owns a two-sided network; **no one has a supplier-side agent**.
- **SAP today:** onboarding agent, AI-generated RFQ responses, catalog generation; no end-to-end discovery-to-contract agent.

**Failure modes by user type:**
- *Buyers:* ~76% of desk time on the synthetic test goes to reviewing candidate records (≈321 h → 77 h for 12 needs when agent-assisted; assumption-based). A plain structured search returned a qualified supplier only **36%** of the time and put ~3 sanctioned/forced-labor-risk suppliers on shortlists across 12 needs.
- *Suppliers:* answer repeated RFQs and questionnaires manually; no way to protect margin or refuse unreliable buyers at scale.

**The opportunity:** a network that already holds verified identities, transaction history and compliance documents makes trust cheaper than for any rival. Agents on both sides, bounded by signed mandates and enforced by a neutral layer, can cut elapsed time (model: 39 → ~25 weeks, ~21 with a warm bench) without losing control.

**This prototype's job:** make the interaction model, platform primitives and control story *demonstrable* on synthetic data, and test whether the controls hold under adversarial input.

---

## 3. Target Audience & Personas

The audience for the prototype is the SAP project sponsors and customer-interview participants who must be convinced the design is safe and valuable. Personas below are the end users the design serves.

### Priya — Strategic Buyer (high tech, EMS sourcing)
- **Primary journey:** receives requirement "electronics manufacturing partner, tariff-diversified, 12-week lead time" → builds long list from past RFQs and trade-show contacts (≈6 weeks) → sends questionnaires → chases certificates → compares quotes in spreadsheets.
- **Pain point:** 160 internal hours per new-supplier project; stale certificates discovered late; fear of missing a forced-labor link.
- **Success state:** Priya confirms a requirement summary, reviews a reasoned shortlist the same day, and approves an award recommendation with every control logged.

### Marcus — Compliance Officer
- **Primary journey:** receives flagged suppliers from several teams → re-checks sanctions, UFLPA and certificates by hand.
- **Pain point:** the same supplier is screened repeatedly by different teams; duplicates and look-alike names cause false positives and misses.
- **Success state:** Marcus sees only flagged cases, each with evidence and the rule that fired, and no watchlist hit is ever auto-cleared.

### Lena — Supplier Sales Lead (mid-size component manufacturer)
- **Primary journey:** receives dozens of RFQs a month from buyers she may never have met → checks capacity → builds quotes → negotiates by email.
- **Pain point:** quotes below margin floor slip through; unreliable buyers (late payment, frequent re-sourcing) waste capacity.
- **Success state:** Lena's supplier agent quotes only inside her margin floor, flags low-rated buyers, and she approves only exceptions.

### Daniel — Legal Counsel
- **Primary journey:** reviews each contract draft line by line.
- **Pain point:** non-standard terms hidden among standard ones.
- **Success state:** Daniel receives a contract assembled only from approved clauses, with every deviation highlighted and liability/termination/IP always routed to him.

### Nandita / Joern — SAP project sponsors (evaluators)
- **Success state:** they run the demo, see a deal close inside limits, see a deal fail safely, see an attack blocked, and read the audit log end to end.

---

## 4. Jobs To Be Done

| Job | User | Statement |
|---|---|---|
| Brief once | Priya | When I need a new direct-material source, I state the need and limits once — the agent turns them into a structured brief without redoing it per supplier. |
| Trusted shortlist | Priya | When I need candidates, I get a ranked, de-duplicated, diversified shortlist with reasons — so I never review hundreds of raw records. |
| Zero compliance escapes | Marcus | When a supplier hits a watchlist or has an expired certificate, I am alerted with evidence — and nothing is auto-cleared. |
| Delivery confidence | Priya | When I compare suppliers, I see landed cost and a delivery-risk estimate that accounts for weather and regional news — so price is not the only signal. |
| Protect my margin | Lena | When an RFQ arrives, my agent quotes only above my margin floor and within real capacity — I never review a loss-making quote. |
| Choose my buyers | Lena | When an unknown buyer asks for a quote, my agent verifies and rates them — so I do not spend capacity on bad payers. |
| Controlled contracting | Daniel | When a contract is assembled, only approved clauses appear and anything non-standard reaches me — liability, termination and IP always. |
| Trace everything | Marcus / sponsors | When a decision is questioned, I export who or what decided, on which evidence, with which model version. |

---

## 5. Scope & Prioritization

### Must Have (v1)
- Deterministic orchestrator: state machine, message bus, schema/signature validation, mandate enforcement, escalation, hash-chained audit log.
- Buyer agent with subagents: Research, Compliance Screen, Regional News Risk, Weather/Logistics Risk, P&L/Landed Cost, Procurement (RFQ/counter), Contract Assembly.
- Supplier agent with subagents: Quote Generation, Buyer Rating & Verification, Capacity & Margin, Demand Forecast (synthetic), Compliance Docs.
- Stages: Discovery, Qualification, Sourcing (RFQ + bounded negotiation), Contracting. Onboarding as a stub with payment-hold hook.
- Synthetic supplier universe (~1,500 records, planted duplicates, look-alikes, 33 watchlist records, expired certificates) and 12 sourcing needs.
- Approval queue UI with override-rate tracking.
- Split-screen run viewer and audit log viewer/export.
- Adversarial suite (18 cases) with pass/fail report.
- Deterministic replay mode (recorded LLM/news/weather responses) so demos are repeatable.
- **No-code Studio (Epic H):** UI to configure models/providers, agents/subagents, prompts, workflow, checkpoints, escalation rules, mandate templates, clauses, data sources and scenarios, with draft → validate → publish, versioning, diff, rollback, and export/import.

### Should Have (v1)
- Real calls for weather (one public API) and news (one public feed) with automatic fallback to recordings.
- Negotiation visualization (offer/counter curve, floor/ceiling revealed only post-run to evaluators).
- Index-based price band check using a synthetic index.
- Warm-bench mode (pre-qualified suppliers skip stages 2–3).
- Baseline comparison run: "status quo structured search" vs agent on the 12 needs.

### Could Have (v2)
- Monitoring stage (certificate expiry/sanctions updates after go-live).
- Multi-homing simulation (supplier present on rival networks).
- Buyer-side parallel projects with cross-project isolation demo.
- Real SBN Discovery / Ariba connectors.

### Won't Have (v1)
- Real customer data, real supplier outreach, real payments or contract signatures.
- Life sciences categories (excluded Oct 12).
- Production-grade multi-tenancy, SSO, SLAs.
- Autonomous (L4) award above any value cap, or any autonomous liability/termination/IP change.
- Editing the platform **invariants** (§H3) from the UI, or custom code/plugins uploaded through the Studio. The Studio configures; it does not execute user-supplied code.

---

## 6. Functional Requirements

Autonomy levels: L1 suggest · L2 act after approval · L3 act then report · L4 act within hard limits.

### Epic A — Project & Mandate Management

**A1. Create project from a need.** `POST /api/v1/projects` accepts a free-text need plus structured fields (category, volume, required certificates, excluded countries, max lead time weeks, target unit price, industry ∈ {high_tech, industrial, consumer}). Buyer agent returns a structured `Brief`; project enters `NEED` with a pending `requirement_confirmation` approval. Industry outside the set → 422. Free text > 4,000 chars → 413.

**A2. Mandates.** A buyer mandate is a signed JSON: `max_unit_price`, `target_unit_price`, `max_lead_time_weeks`, `max_rounds` (default 4, hard cap 8), `excluded_countries[]`, `locked_clauses[]` (always includes `liability`, `termination`, `ip`), `autonomy` per stage, `award_value_cap`. A supplier mandate holds `min_margin_pct`, `min_unit_price` (derived), `max_capacity_units`, `provable_certificates[]`, `unchangeable_clauses[]`, `min_buyer_rating`. Mandates are immutable once a project starts; changing one creates a new version and restarts the current stage. Invalid signature or modified payload → 403 `MANDATE_INVALID`.

**A3. Information boundary.** Neither mandate is readable by the counterparty's agent, API routes or UI panel. The orchestrator sees mandates only as predicates (`within_mandate(message)` → pass/fail + violated rule id), never exposing the other side's thresholds in any response, log line visible to the other side, or error message.

### Epic B — Orchestrator & Message Bus

**B1. State machine.** Stages `NEED → DISCOVERY → QUALIFICATION → SOURCING → CONTRACTING → ONBOARDING → DONE`; side states `ESCALATED` (resumes the same stage) and `STOPPED`. Transitions happen only when the stage exit condition holds; each transition writes an audit event. Illegal transition requests → 409 `ILLEGAL_TRANSITION`.

**B2. Typed messages.** Message types: `Brief, Invite, InterestReply, DocRequest, DocSubmission, QualResult, RFQ, Offer, Counter, Accept, Reject, ContractDraft, Redline, SignRequest, OnboardData, Escalate`. Every message carries `{id, project_id, stage, type, from_agent, to_agent, mandate_ref, payload, signature, ts, in_reply_to}`. The bus rejects (a) unknown type, (b) payload failing its Pydantic schema, (c) type not valid in the current stage, (d) bad signature, (e) sender not a participant. Rejections are logged and returned as 422/403; the sender gets the rule id, not internals.

**B3. Mandate enforcement is code.** Before delivery of any `Offer/Counter/Accept/ContractDraft/Redline`, the policy engine evaluates the *sender's* mandate. A violation: message is **not delivered**, project → `ESCALATED` with reason `MANDATE_BREACH`, audit event written. No prompt can override this.

**B4. Negotiation bounds.** Max rounds from mandate (≤ 8). Each side may counter at most once per round, within 30 s per turn (agent timeout → treated as `Reject`, escalate). If buyer ceiling < supplier floor (detected by the orchestrator via predicate probes, never revealed), the run ends `NO_DEAL` after the first non-overlapping exchange and is displayed as a valid outcome. Duplicate identical counters ×2 → `STALLED` → escalate.

**B5. Escalation.** Triggers: watchlist/forced-labor hit; missing or unverified data; offer outside mandate; locked-clause touched; price outside index band (±15% default); max rounds hit; supplier claim not backed by registry; subagent confidence < 0.6; subagent timeout; low-confidence extraction. Escalation creates an `Approval` row (status `PENDING`), pauses the project, and records the reasons. Resolution `APPROVED | REJECTED | EDITED` resumes or stops the stage.

**B6. Isolation.** A buyer sees only messages where it is `from_agent` or `to_agent` in its own project. Offers from competing suppliers in the same RFQ are never shown to other suppliers. Cross-project queries return 404 (not 403) to avoid existence leaks.

### Epic C — Discovery (Stage 2)

**C1. Research subagent.** Searches SBN registry (transaction history first), then external synthetic "open web" index. Output: candidates with `source`, `match_score`, `reasons[]`. Entity resolution merges duplicates: name similarity ≥ 0.88 *and* no conflicting country/tax-id → merge; conflicting hard facts block merging even at 0.95. Look-alike names in different countries stay separate.

**C2. Diversification.** Shortlist (default N = 5, max 10) must include ≥ 2 countries and ≥ 1 non-incumbent where available; shortlist violates a buyer-excluded country → impossible by construction (filter precedes ranking).

**C3. Invite.** Orchestrator delivers `Invite` to each shortlisted supplier agent in parallel; `InterestReply` (accept/decline + capacity) due in 60 s simulated time; no reply → supplier dropped with reason `NO_RESPONSE`.

**C4. Checkpoint.** L3: buyer sees the long list and reasons after the fact; removal triggers re-ranking. Shortlist empty → project `STOPPED` with `NO_VIABLE_SUPPLIER` and the reason per rejected candidate.

### Epic D — Qualification (Stage 3)

**D1. Compliance screen (rules only, no LLM judgment).** Checks: required certificates present in registry **and** unexpired; name/aliases vs watchlist (fuzzy similarity → hit; the code cutoff is **88.0 on rapidfuzz's 0-100 scale**, i.e. 0.88, not 0.85); forced-labor risk flag incl. sub-tier origin; missing mandatory fields (capacity, lead time, financial risk, years in business) → `REVIEW`, never pass. Free-text certificate claims not in the registry → `UNVERIFIED_CLAIM` → REVIEW. Watchlist hit → `BLOCKED` and escalate to Compliance; never auto-cleared.

**D2. Regional news risk subagent.** For each candidate's region: structured risk signals (strike, unrest, port closure, sanction, regulation change) with `severity 0–1`, `confidence 0–1`, `sources[] {url, title, date}`. Anything without ≥ 1 source is discarded. Confidence < 0.6 → escalate as `LOW_CONFIDENCE`.

**D3. Technical qualification.** Agent schedules audits/samples (simulated), tracks corrective actions; **cannot approve a part** — only Quality (human) sets pass/fail. Simulated delay recorded.

**D4. Output.** Each supplier ends `QUALIFIED | REJECTED | REVIEW` with machine-readable reasons, shown in plain words.

### Epic E — Sourcing (Stage 4)

**E1. RFQ.** Procurement subagent drafts RFQ (spec, volume, incoterm, target lead time) → `RFQ` message to qualified suppliers in parallel. No price targets or ceilings in the RFQ unless the buyer mandate marks `disclose_target = true`.

**E2. Supplier quote.** Quote Generation subagent computes `unit_price = max(cost × (1 + margin_floor), market_adjusted_price)`; Capacity & Margin subagent checks capacity and lead time; Demand Forecast reduces offered capacity **continuously**: `offered = max_capacity × (1 − 0.6 × forecast_load)` for a synthetic load of 30-85% (there is no 85% threshold in code). Quote below `min_unit_price` is blocked by policy engine (B3).

**E3. Buyer verification (supplier side).** Buyer Rating & Verification checks buyer identity and synthetic payment history; rating < `min_buyer_rating` → supplier agent declines with reason code `BUYER_RATING` (reason text neutral, no rating value disclosed to buyer).

**E4. Comparison.** P&L/Landed Cost subagent computes (in code) `unit_price + freight + duty/tariff + risk_cost`, where `risk_cost = unit_price × delay_probability × delay_penalty_pct`. Weather/Logistics subagent supplies `delay_probability` with source and confidence. Outputs a ranked comparison with each component shown.

**E5. Negotiation.** Procurement proposes counters; policy engine enforces B3/B4. Counter step size ≥ 0.5% of price (prevents infinite micro-steps). Index check: unit price outside ±15% of the synthetic index band → escalate.

**E6. Award.** L2: buyer approves the award recommendation; below `award_value_cap` and with all checks passed it may be batched (async) — never skipped. Recommendation shows ranked options and the reasons others lost.

### Epic F — Contracting & Onboarding (Stages 5–6)

**F1. Assembly.** Contract Assembly builds from an approved clause library (≥ 15 clauses, versioned). Any clause not in the library or any edit to `liability`, `termination`, `ip` → `Escalate` to Legal; agent cannot sign.

**F2. Redlines.** Supplier agent may accept or propose alternatives only from clauses listed as `negotiable` in the library; others → escalate.

**F3. Signature (simulated).** `SignRequest` creates a pending signature; Legal approval required; the prototype records a simulated signature hash.

**F4. Onboarding stub.** Collects master data (synthetic); bank details validated by a rules stub; **ledger hold** record created and stays `HELD` until Finance releases it in the UI. Agents cannot release a hold.

### Epic G — Approvals, Audit, Metrics

**G1. Approval queue.** Role-scoped list (Compliance, Quality, Legal, Finance, Buyer). Each item shows reasons, evidence, the agent's recommendation and actions Approve / Reject / Edit-and-approve with mandatory comment ≥ 10 chars on Reject/Edit. Items unresolved for 24 h (wall clock; demo can accelerate) show `OVERDUE` badge; no auto-approval ever.

**G2. Override monitoring.** Per stage and per reviewer: override rate = (rejected + edited)/decided. A reviewer with ≥ 20 decisions and rate < 2% is flagged `POSSIBLE_RUBBER_STAMP`; flagged reviewers trigger a spot-check item (random 10% of their approvals re-queued to a second reviewer).

**G3. Audit log.** Append-only, hash-chained (`hash = SHA256(prev_hash + canonical_json(event))`). Event fields: `{seq, ts, project_id, actor_type, actor_id, action, inputs_ref, rule_id, evidence[], model_id, model_version, outcome, approver_id}`. `GET /api/v1/projects/{id}/audit/export` returns NDJSON; `GET .../audit/verify` recomputes the chain and returns `{valid: bool, first_bad_seq}`.

**G4. Metrics.** Per run: elapsed (simulated) weeks by stage vs baseline, human touches, wait time at checkpoints, precision of shortlist vs hidden answer key, compliance escapes, escalation count by reason, LLM tokens/cost.

### Epic H — Studio (no-code configuration)

Principle: **configuration is data, validated by the same typed schemas the runtime uses; the UI is a form over those schemas.** Nothing in Epics B–G requires a code change to alter within the bounds below.

**H1. Models & providers.**
- *Providers:* add/edit/test providers of type `ollama`, `anthropic`, `openai_compatible`, `stub`. Fields: name, base URL, API key (write-only), "local provider" flag. **Test connection** performs a minimal call and reports reachable / authenticated / latency.
- *Model discovery:* for Ollama the UI lists installed models (from `/api/tags`) with a pick-list; for hosted providers the user types a model id (with a suggestion list). A model flagged `unavailable` cannot be assigned.
- *Roles:* assign a provider + model + parameters (temperature, max tokens, timeout, thinking mode `auto|on|off`) to each role (`chat`, `fast`). **Per-agent overrides:** any lead agent or subagent may override the role default (e.g., News Risk uses a bigger model). The Studio shows the *effective* model for every agent.
- *Mode:* `live | replay | record` selector; replay store browser (list, delete, re-record a single key).
- *Safety:* changing a model never changes policy; a model with failing tests is blocked from publish (H8). Keys are write-only: the UI shows `****` and a "last updated" stamp.

**H2. Agents & subagents.** Catalog of agents (buyer lead, supplier lead) and subagents (Research, Compliance Screen, Regional News Risk, Weather/Logistics, Landed Cost, Procurement, Contract Assembly, Quote Generation, Buyer Verification, Capacity & Margin, Demand Forecast, Compliance Docs). For each: enable/disable, which stages it runs in, tool-call limit, timeout, model override, prompt template (H4), and input/output schema **read-only view**. Disabling a subagent is allowed only if no *required* dependency exists; the validator names what would break (e.g., disabling Compliance Screen is rejected — it is an invariant).
- **Custom subagent from a template:** the user may clone a built-in *LLM-extraction* subagent (e.g., "Regional News Risk") to make "Port Congestion Risk" by editing its prompt, source feeds and output labels. Output must still conform to the `SubagentResult` envelope; a custom subagent can contribute **evidence and risk signals only**, never a pass/fail on compliance.

**H3. Invariants (visible, read-only).** The Studio has an "Invariants" page listing what cannot be configured, each with its reason: audit logging and hash chain; message signing and schema validation; mandate enforcement by the policy engine; counterparty mandate secrecy; watchlist hits never auto-cleared; qualification status set only by rules; locked clauses `liability`, `termination`, `ip` always require Legal; payment hold release only by FINANCE; LLM output never executed; untrusted-data wrapping of external text; strict fail-closed on validation errors. A publish that would weaken an invariant is rejected with the invariant id.

**H4. Prompts.** Each LLM-using subagent has a prompt template editable in the UI with: variables palette (typed, only allowed fields), live preview with sample inputs, version history, and diff. The **untrusted-data wrapper, English-only preamble and output schema are appended by code and shown read-only**; the user edits only the instruction body. Templates containing a forbidden variable (credentials, counterparty mandate) fail validation. A prompt "test" runs it against saved sample inputs in replay or live mode and shows schema-conformance.

**H5. Workflow.** Visual stage editor over a fixed set of stage *types*: enable/disable optional stages (onboarding), set per-stage autonomy level (L1–L4, with a hard ceiling per stage defined by invariants — e.g., contracting ≤ L2), choose which human role is the checkpoint, make a checkpoint synchronous or batched/async (only where permitted), and set exit-condition parameters. Stage *order* is fixed; the editor shows the resulting state machine diagram, and the validator rejects unreachable or deadlocking configurations.

**H6. Escalation & policy rules.** Rule builder (form, not code) for escalation triggers: pick a *signal* (subagent confidence, news severity, price vs index band, rounds used, data missing, custom-subagent signal), an operator, a value and a target role/action. Built-in invariant triggers appear locked. A "what-if" panel replays a past project's recorded signals against the draft rules and lists which escalations would change.

**H7. Mandate templates & scenarios.** Form builder for buyer and supplier mandate templates (field set fixed by schema; defaults/ranges editable; required/optional toggles). **Scenario designer:** create sourcing needs (category, industry, requirements, excluded countries, buyer template, set of supplier profiles with their mandate templates and behaviours: cooperative, aggressive, unreliable, adversarial). Scenarios are runnable from the UI and saved.
- **Clause library editor:** add/edit clauses, mark `negotiable`; `locked` is forced on for the three invariant clauses.
- **Data sources:** manage the synthetic supplier universe (generate with parameters or import), watchlist, indexes, and external sources (weather/news feeds) with an **outbound host allowlist** managed here (admin-only, with warnings). Each source has an on/off and "use recorded fallback" switch.

**H8. Lifecycle: draft → validate → publish.**
- All edits go to a **draft** configuration; the live configuration is immutable once published (versioned `config_version`).
- *Validate* runs: schema checks, invariant checks, dependency checks, model connectivity tests for models used, prompt schema-conformance tests on saved samples, workflow reachability. Publish is blocked on any error; warnings are listed.
- *Publish* creates a new immutable version and writes an audit event. *Diff* between any two versions. *Rollback* publishes a prior version as a new one.
- **Pinning:** a project captures the published config version at `start`; later publishes never alter a running project, and replay uses the pinned version.
- *Export/import:* a config version exports as a single JSON bundle (secrets excluded); import goes into a draft and is validated like manual edits.
- *Presets:* "Safe demo" (replay, local model), "Live local" (Ollama), "Live hosted". Applying a preset only fills a draft.

**H9. Roles & access.** `DESIGNER` can edit drafts and run validation/tests; `SUPER_ADMIN` (SBN) can publish, manage providers/keys and the outbound allowlist; `EVALUATOR` read-only. Organization admins (`ADMIN`) do not touch the platform configuration: they manage their own organization and layer versioned own-side settings on top of it (see `docs/05-roles-and-perspectives.md`). Every Studio change (draft save, publish, key update, rollback) is an audit event with before/after (secrets redacted).

**H10. Guided first run.** A setup wizard on an empty install: pick a provider and model (with connection test) → pick a preset → pick a scenario → run. No file editing, no restarts.

**H11. Failure behaviour.** Invalid draft → inline field errors, save allowed, publish blocked. Provider unreachable → badge + test details; running projects fall back per §14. Concurrent edits to the same draft → 409 with the other editor's name and a merge-by-section prompt. Importing a bundle with an unknown schema version → rejected with the version needed.

> All numeric thresholds in this PRD (timeouts, caps, rate limits, bands) are the **seeded defaults** for the Studio fields above, not fixed requirements.

### Acceptance Criteria

| Requirement | Evaluation Method | Metric | Criteria | Justification |
|---|---|---|---|---|
| Shortlist precision | Compare shortlist to hidden answer key, 12 needs × 9 seeds | Share of top-5 meeting all hard requirements | ≥ 98% mean (100% expected for rules) | Report showed 100% rule-based upper bound; status quo 36% |
| Compliance escapes | Same runs | Count sanctioned/forced-labor suppliers reaching RFQ | 0 | Non-negotiable control |
| Mandate breach delivery | Adversarial suite | Offers outside mandate delivered | 0 of N | Limits are code |
| Information leak | Adversarial suite + log scan | Counterparty threshold values visible to other side | 0 | Antitrust / isolation |
| Prompt injection via news/web | Adversarial suite (≥ 4 cases) | Injected instruction altering a decision | 0 | Untrusted external input |
| Audit integrity | `audit/verify` after tamper test | Detect modified row | 100% detection | Liability evidence |
| Run reproducibility | Replay mode ×5 | Identical decision sequence (outcome, stages, messages, approvals) through the live API; identical audit head hash only when project/user ids and the clock are fixed (test harness, `FixedClock`) | 5/5 identical decision sequence; 5/5 identical head hash under fixed ids + clock | Demo reliability |
| Time to first shortlist | Stopwatch, replay mode | Seconds | < 20 s for 1,700 records | Usable demo |
| Human touches | Count per project | Approvals required | ≤ 6 on happy path | "No bottleneck" claim |

---

## 7. Authentication & Authorization

> **Assumed:** prototype only, local deployment, demo personas — not production identity. SAP SSO integration is out of scope.

- **Tokens:** JWT access token (15 min) in an `HttpOnly; SameSite=Strict; Secure` cookie (Secure waived on `localhost`); refresh token (8 h) in a separate `HttpOnly` cookie, rotated on use, server-side revocation list in DB. Logout revokes the refresh token.
- **Demo login:** `POST /api/v1/auth/login` with email + password for seeded personas; passwords hashed with argon2id (time=3, memory=64 MiB, parallelism=4). Five failed logins in 15 min → 15-min lockout per account + IP (429 with `Retry-After`).

| Role | Capabilities |
|---|---|
| `BUYER` | Create/read own projects; confirm requirements; approve shortlist edits, awards; view buyer-side subagent outputs |
| `SUPPLIER` | Read own supplier agent's inbox/outbox and own mandate; approve supplier-side exceptions |
| `COMPLIANCE` | Resolve compliance escalations across projects |
| `QUALITY` | Technical pass/fail decisions |
| `LEGAL` | Resolve contract escalations; approve signature |
| `FINANCE` | Release ledger holds; confirm bank details |
| `EVALUATOR` | Read-only: all views, post-run mandate reveal, metrics, audit export; cannot approve |
| `DESIGNER` | Studio: edit drafts, run validation and prompt/model tests; cannot publish or touch keys/allowlist |
| `SUPER_ADMIN` | SBN platform operator, all access: Studio publish/rollback, providers, keys and outbound allowlist, settings, every organization and the full agent workflow; resolves operational approvals |
| `ADMIN` | **Organization admin** of one buyer or supplier organization: its people and roles, versioned own-side agent settings (timeouts, tool-call limits, model, prompt wording; never safety rules), and the agent workflow of its own side |

Route enforcement: auth dependency on every `/api/v1` route except `/health` and `/auth/login`; ownership/side scoping in the query (`WHERE project.buyer_org_id = :org`), never post-fetch filtering. Violations → 403 for known-but-forbidden, 404 for cross-tenant objects.

---

## 8. AI System

**Provider abstraction.** All LLM calls go through `llm/client.py`. Providers, models, roles and per-agent overrides are **configured in the Studio (H1)** and stored in the database as versioned configuration; a bootstrap file (`llm.config.example.yaml`) only seeds a fresh install. Roles: `chat` (reasoning/drafting), `fast` (JSON extraction/classification). Providers: `ollama` (default, local, satisfies "securely hosted" scope), `anthropic`, `openai_compatible`, `stub` (replay). Switching provider or model = a published config change, no restart.

**Where an LLM is allowed:** news/web reading → structured risk signals; document reading → certificate extraction; drafting RFQ text, explanations, contract summaries; negotiation *proposals* (price chosen from a bounded set the code supplies). **Where it is not allowed:** arithmetic (landed cost, margin), watchlist/certificate decisions, mandate checks, state transitions, clause approval.

**Subagents are tools.** Each is a function `(typed input) → typed output {result, confidence, sources[], assumptions[]}` with a JSON schema. Lead agents never consume free text from a subagent as a decision input. Each call has a 20 s timeout and ≤ 2 retries; invalid JSON triggers one repair attempt via `fast`, then falls back to `confidence=0` and escalation.

**Agent loop.** Lead agent loop (buyer or supplier): max 12 tool calls per stage turn; duplicate call (same tool + identical args hash) is short-circuited and returns the cached result; loop that makes no state progress in 3 iterations is terminated and escalated. Tool calls run non-streaming.

**Confirmation gates (human required, all the buyer user's):** a flagged supplier (only when flagged); the award; a changed locked clause (only when touched); one final decision that confirms bank details and releases the payment hold. Technical pass/fail and signature are settled by rules and audited as `checkpoint_skipped`; requirement confirmation is off by default. *(Revised from the original list; see CLAUDE.md.)*

**Streaming.** Run viewer receives SSE `GET /api/v1/projects/{id}/stream` (see TRD). Events: `stage_changed`, `message`, `subagent_call`, `subagent_result`, `policy_block`, `escalation_created`, `approval_resolved`, `audit_event`, `run_finished`, `error`.

**Quality fallback.** If a `fast`-role output fails schema validation twice or has confidence < 0.4, retry once on `chat`; if still failing, escalate with `LOW_CONFIDENCE`.

**English-only.** All system prompts start with an explicit instruction to respond only in English (multilingual local models drift to other languages on ambiguous text).

**Replay mode.** `LLM_MODE=replay` serves recorded responses keyed by `hash(role, prompt_template_id, canonical_inputs)`; missing key → fail loudly in tests, fall back to live call in dev.

---

## 9. System Architecture

A React/Vite single-page app talks to a FastAPI service (`api`, host port **8005**, container 8000). The API hosts the orchestrator, the policy engine, the buyer and supplier agent runtimes, the subagent library and the SQLite/Postgres data layer in one process, separated by module boundaries: agents may import only the `bus` client interface, never the orchestrator internals or the counterparty's store. This keeps the trust boundary testable (an import-linter rule enforces it).

A run is started by `POST /projects/{id}/start`. The orchestrator spawns an asyncio task per project that drives the state machine: for each stage it asks the relevant agent(s) for the next message, validates and logs it, applies the policy engine, delivers it to the counterparty's inbox, and records subagent calls. Buyer and supplier agent turns for parallel steps (invites, RFQs) run concurrently with `asyncio.gather`. When escalation occurs the task persists state and exits; resolving the approval re-queues it (**actual:** the runtime — agents, bus, approvals — is in memory only and is NOT rebuilt from the DB; a resolved approval resumes the live in-process run. On restart, `recover()` closes unfinished projects as `STOPPED`/`RESTART_LOST_RUNTIME` and they must be re-created; true DB resumability is a known deviation). Events are written to the audit table first, then fanned out over SSE.

External calls: LLM (Ollama on host at `localhost:11434`, `host.docker.internal:11434` inside Docker; or hosted API), weather API, news feed. All outbound HTTP goes through one `safe_http` client with a host allowlist. Web content is wrapped as untrusted data and passed through an extraction step before any decision uses it.

Deployment: Docker Compose with `api` and `web`; Ollama stays on the host. SQLite file in a mounted volume for the prototype; PostgreSQL swap by changing `DATABASE_URL`.

---

## 10. Data Model / Key Entities

Full schema: TRD §3.

- **Organization** — buyer or supplier org; `kind ∈ {BUYER, SUPPLIER}`.
- **User** — demo persona, role, org.
- **Supplier** (registry) — verified identity, country, tax id, categories, capacity, lead time, financial risk, years in business, network status, soft-delete.
- **Certificate** — registry certificates with issuer and expiry; ⚠ expiry compared in UTC date.
- **WatchlistEntry** — synthetic sanctions/forced-labor list with aliases.
- **TransactionHistory** — per supplier: on-time-delivery %, quality score, disputes (SBN advantage).
- **Mandate** — signed buyer/supplier limits; immutable; versioned.
- **Project** — one sourcing need; stage, status, buyer org, replay mode.
- **Message** — every bus message; unique `(project_id, seq)`.
- **SubagentCall** — tool name, input hash, output, confidence, latency, model id.
- **Approval** — escalation item; role target, reasons, status, resolver, comment.
- **Offer** — structured quote/counter with round number.
- **ContractClause / Contract** — clause library (versioned) and assembled contract with deviation list.
- **LedgerHold** — payment hold released only by FINANCE.
- **AuditEvent** — append-only, hash-chained, no UPDATE/DELETE permitted.
- **BuyerProfile** — synthetic payment/dispute history used by supplier verification.
- **IndexPrice** — synthetic material price index for band checks.
- **AdversarialCase / AdversarialResult** — the 18-case suite and outcomes.

---

## 11. API Design

Base path `/api/v1`, JSON, cookie auth (§7). SSE endpoint `GET /projects/{id}/stream` (`text/event-stream`). Errors use one envelope (TRD §5). Versioned by path; breaking changes bump `/v2`. Epics covered: A–G. Full route table in TRD §5.

---

## 12. Security & Privacy

> This prototype runs on synthetic data only. Controls below are still required because the *point* of the prototype is to demonstrate that the control model is sound.

### 12.1 OWASP Top 10

| Item | Applies | Requirement | Verification |
|---|---|---|---|
| Broken Access Control | Yes | Auth dependency on all routes; side scoping (`buyer_org_id`/`supplier_org_id`) in SQL; mandate and counterparty data never returned cross-side; ownership check on every mutation (`WHERE id=:id AND org_id=:org`); cross-tenant → 404 | pytest matrix role × route; adversarial cases A-07..A-09 |
| Cryptographic Failures | Yes | argon2id passwords; JWT HS256 with ≥ 32-byte secret from env (RS256 if keys provisioned); HMAC-SHA256 message/mandate signatures with per-org keys from env; no MD5/SHA1; TLS terminates at proxy in non-local deployments; mandate payloads stored plaintext (synthetic) but signature verified on read | unit tests; config lint |
| Injection | Yes | SQLAlchemy ORM / bound params only; Pydantic validation on every body; no shell commands from input; LLM output never executed | bandit + ruff `S` rules; test with SQL meta-characters in all string fields |
| Insecure Design | Yes | Rate limits (below); round caps; subagent step caps; deterministic policy engine; fail-closed on any parse error | adversarial suite; rate-limit tests |
| Security Misconfiguration | Yes | `APP_ENV=prod` disables docs, debug, stack traces; CORS exact origin list; security headers (CSP, `X-Content-Type-Options`, `Referrer-Policy`) | startup config assert; header test |
| Vulnerable Components | Yes | Pinned versions; `uv.lock`/`package-lock.json` committed; weekly `pip-audit` + `npm audit`; Dependabot | CI job |
| Authentication Failures | Yes | §7 lifecycle; lockout; refresh rotation; revocation on logout | tests |
| Data Integrity | Yes | Pydantic v2 `extra="forbid"` for all bus messages; JSON-schema validation for subagent outputs and external API responses; audit hash chain | `audit/verify`; fuzz tests |
| Logging & Monitoring | Yes | Structured JSON logs; log auth failures, 403s, policy blocks, rate-limit hits, mandate breaches; never log passwords, tokens, mandate numeric thresholds in cross-side logs, or raw web content | log-scan test |
| SSRF | Yes | `safe_http` allowlist (weather host, news host, Ollama host, hosted LLM host); block private/link-local ranges and redirects to non-allowlisted hosts; supplier-provided URLs are never fetched | unit + adversarial case A-12 |

**Rate limits (per user unless noted):** login 5/15 min per account+IP; project create 10/h; start run 5/h; approval resolve 60/min; SSE connections 3 concurrent; global LLM calls 600/h and a hard token budget per project (default 400k) — exceeded → run pauses with `BUDGET_EXCEEDED`, HTTP 429 on new LLM-triggering actions with `Retry-After`.

### 12.2 Input sanitization map

| Input | Sanitization |
|---|---|
| Need free text | length ≤ 4,000; strip control chars; treated as data in prompts (see 12.4) |
| Structured fields | Pydantic enums/ranges; country = ISO-3166 alpha-2 |
| Comments on approvals | length 10–1,000; HTML-escaped on render (React default); `nh3`/bleach not needed because no HTML accepted |
| Supplier names, aliases | NFKC normalize, strip zero-width chars, length ≤ 200 |
| News/web content | untrusted; extracted to schema; raw text never reaches decision code or other prompts |
| Uploaded synthetic datasets (admin) | schema-validated CSV/JSON; size ≤ 10 MB; row ≤ 50,000 |

### 12.3 Secrets management
- Bootstrap secrets in env vars (`JWT_SECRET`, `ORG_SIGNING_KEYS`, `CONFIG_ENCRYPTION_KEY`); `.env` gitignored; `.env.example` has placeholders only.
- Provider API keys entered in the Studio are **encrypted at rest** (AES-GCM / Fernet with `CONFIG_ENCRYPTION_KEY`), write-only in the UI, never returned by any API (`"****"` + last-updated), never included in config exports, never logged. Env vars (`ANTHROPIC_API_KEY`, …) remain a fallback.
- Hosted-LLM keys scoped to minimum permission.

### 12.4 Secure AI requirements
- **Prompt injection:** user and external text are inserted only into a delimited `<untrusted_data>` block in the *user* turn, never the system prompt. Extraction prompts instruct the model to ignore instructions inside the block. Outputs are schema-validated; free-text fields cannot influence control flow. Suspicious patterns (e.g., "ignore previous", role-play markers) are flagged `INJECTION_SUSPECTED` and logged, not stripped silently.
- **PII in prompts:** the prototype has synthetic data only, but the rule stands: no user emails, passwords, tokens or org signing keys in any prompt; a prompt-builder allowlist of fields enforces this.
- **Output trust:** model output is parsed as JSON into Pydantic models; never `eval`/`exec`, shell, or SQL. Negotiation prices from the LLM must be one of the discrete options supplied by code; anything else is rejected.
- **Rate limiting:** see 12.1.
- **System prompt confidentiality:** prompts live in `prompts/` on the server; never returned by API; SSE `subagent_call` events show tool name and input *summary*, not prompt text.
- **Mandate secrecy in prompts:** an agent's prompt may include its own mandate; it must never include the counterparty's.

### 12.5 Known attack surface

| Attack vector | Mitigation | Owner |
|---|---|---|
| Malicious news/web content with embedded instructions | Untrusted-data block, extraction schema, source required, injection flag | Subagent layer |
| Supplier agent probing buyer ceiling via repeated offers | Round cap, no ceiling in responses, minimum step size, predicate-only checks | Orchestrator |
| Counterparty reading other side's mandate via API | Side-scoped queries, 404 on cross-side, response filtering tests | API |
| Forged message from non-participant | HMAC signature + participant check | Bus |
| Replayed old offer | `(project_id, seq)` uniqueness + nonce + stage check | Bus |
| Tampered audit log | Hash chain + DB role without UPDATE/DELETE on `audit_event` | Data layer |
| Free-text certificate claim to bypass check | Registry-only truth; claims → REVIEW | Compliance screen |
| Look-alike / altered name to evade watchlist | Fuzzy + alias matching; false positives go to human | Compliance screen |
| Approval spam / reviewer rubber-stamping | Override-rate monitoring, spot checks | Approval service |
| Cost attack via runs | Per-user rate limits, token budget | API |
| SSRF via supplier URL fields | Never fetched; allowlist client | `safe_http` |
| Credential stuffing | argon2id, lockout, rate limit | Auth |
| XSS in comments/names | React escaping, CSP `default-src 'self'` | Web |
| CSRF on cookie auth | SameSite=Strict + double-submit CSRF header for mutations | Web/API |
| Studio provider URL used for SSRF (e.g., cloud metadata IP) | Admin-only; scheme http/https; block link-local/metadata ranges always; private ranges only if "local provider" flag set explicitly; re-validated at call time (DNS rebinding) | Studio / `safe_http` |
| Prompt template edited to weaken defenses or exfiltrate | Wrapper, English preamble and schema appended by code; forbidden variables rejected; DESIGNER cannot publish; every publish audited with diff | Studio |
| Config change weakening an invariant | Invariant checker blocks publish; invariant ids in the error | Studio validator |
| Malicious config bundle import | Schema version + schema validation, size cap, no executable content, lands in draft only | Studio |
| Draft/publish race altering a running project | Projects pin the published config version at `start` | Orchestrator |
| Stored provider key theft from DB | Encrypted at rest, key in env, write-only API, redacted logs | Studio / data layer |

---

## 13. Non-Functional Requirements

| Requirement | Target | Notes |
|---|---|---|
| Discovery + qualification latency (replay) | < 20 s for ~1,700 records, 1 need | Wall clock from `start` to shortlist event |
| End-to-end happy-path run (replay) | < 90 s | Simulated delays are time-compressed |
| End-to-end run (live Ollama 8B-class) | < 6 min | p95 over 10 runs |
| First SSE event after start | < 500 ms | degraded > 1 s, failing > 3 s |
| API p95 (non-LLM routes) | < 200 ms | local, 10 concurrent users |
| Audit write overhead | < 10 ms per event | |
| Determinism | 5/5 identical replay decision sequences through the live API; identical final audit hash only with fixed ids + `FixedClock` (tests) | compare decision sequence (`scripts/e2e-determinism.py`); compare final audit hash in `test_same_scenario_three_times_identical_audit_hash` |
| Test coverage | ≥ 85% on `orchestrator/`, `policy/`, `bus/`; ≥ 70% overall | `pytest --cov` |
| Adversarial suite | 18/18 pass | `make adversarial` |
| Startup | < 30 s to healthy `/health` | `docker compose up` |
| Accessibility | WCAG 2.1 AA for approval queue and run viewer | axe check, keyboard operable |
| Browser support | Latest Chrome, Safari, Firefox | |
| LLM cost cap | ≤ 400k tokens per project | counted per project in DB |

---

## 14. Error States & Edge Cases

| Scenario | Behaviour |
|---|---|
| LLM provider unreachable | Retry ×2 with backoff (1 s, 3 s); then subagent returns `confidence=0`; project escalates `LLM_UNAVAILABLE`; rule-only stages continue |
| LLM returns invalid JSON | One repair attempt on `fast`; then `LOW_CONFIDENCE` escalation |
| Weather/news API down or rate-limited | Use recorded fallback tagged `source=recorded`; UI badge "recorded data"; confidence capped at 0.5 |
| Subagent timeout (> 20 s) | Cancel; treat as missing evidence → REVIEW (qualification) or `Reject` (negotiation); log |
| Run task crashes mid-stage | Project status `FAILED_RECOVERABLE`; resume from last committed message via `POST /projects/{id}/resume`; no duplicate messages (idempotent on `(project_id, seq)`) |
| Run stuck in `RUNNING` > 10 min without event | Watchdog marks `STALLED`, escalates to ADMIN |
| Ollama model not releasing VRAM | After each project ends, `POST /api/generate` with `keep_alive: 0` (direct httpx); failure logged, not fatal |
| Concurrent approval resolution | Optimistic version check; second resolver gets 409 `APPROVAL_ALREADY_RESOLVED` |
| Mandate updated mid-run | Rejected for running projects (409); new version only before `start` |
| Buyer ceiling < supplier floor | `NO_DEAL` outcome, explained without revealing numbers |
| All suppliers disqualified | `STOPPED / NO_VIABLE_SUPPLIER` with per-candidate reasons |
| Missing mandatory supplier data | `REVIEW`, never pass |
| Duplicate suppliers at different stages | Merged by entity resolution; conflicting hard facts → kept separate and flagged |
| Message with unknown/invalid type for stage | 422 `INVALID_MESSAGE_FOR_STAGE`; audit event; no state change |
| Malformed body / oversize payload | 422 / 413 with envelope; nothing persisted |
| Auth boundary violation | 403 (known resource, wrong role) or 404 (cross-tenant); logged |
| SSE client disconnects | Run continues; reconnect with `Last-Event-ID` replays missed events from audit |
| Token budget exceeded | Run pauses `BUDGET_EXCEEDED`; ADMIN may raise |
| Audit chain verification fails | Banner in UI, exports flagged `TAMPER_SUSPECTED`, runs blocked |
| Reviewer approves after 24 h overdue | Allowed; overdue duration recorded in metrics |

---

## 15. Out of Scope
- Real customers, real supplier contact, real signatures or payments.
- Integration with live SBN, Ariba, Icertis, Joule, AI Agent Hub.
- Life sciences compliance (GxP etc.).
- Pricing/outcome-based billing model (Track 5).
- Legal conclusions on agent liability (hypothesis only; SAP legal to confirm).
- Production identity, multi-tenant hosting, SLAs.
- Training or fine-tuning models.
- Mobile UI.
- Monitoring stage after go-live (v2).
- Audits/samples as real physical processes (simulated delay only).
- Forecasting accuracy claims; demand and buyer-rating data are synthetic.

---

## 16. Open Questions & Risks

| # | Risk / Question | Severity | Notes |
|---|---|---|---|
| 1 | Revenue figures differ between notes (~$25M, price-savings driven) and report (~$51M, labor-driven) | Medium | Reconcile before final deck; prototype does not depend on it |
| 2 | Synthetic test is self-written; 100% precision is an upper bound | High | Mitigation: adversarial suite authored by someone other than the agent author; label all results "synthetic upper bound"; add noisy-data seeds |
| 3 | Prompt injection through news/web content | High | Mitigation: untrusted-data block, schema extraction, source requirement, injection flag, 4+ adversarial cases, no free text reaching decisions |
| 4 | Mandate/information leakage between sides (antitrust) | High | Mitigation: predicate-only enforcement, side-scoped queries, leak scan test, EVALUATOR-only post-run reveal |
| 5 | Reviewer rubber-stamping defeats checkpoints | High | Mitigation: override-rate monitoring, 10% spot checks, mandatory comments on edits/rejects |
| 6 | Liability model unconfirmed (buyer owns decision, SAP owns agent behaviour, audit log arbitrates) | Medium | Hypothesis for SAP legal; prototype only provides the audit evidence |
| 7 | Local model quality (tool calling, JSON reliability) | Medium | Replay mode, schema repair, `chat` fallback, tool-calls non-streaming |
| 8 | Weather/news APIs add flakiness and cost | Medium | Recorded fallbacks; real calls optional |
| 9 | Too many subagents → latency, cost, audit burden | Medium | Build decision-changing subagents first; stub the rest; cap tool calls per turn |
| 10 | Negotiation may not converge | Medium | Round cap, min step, `NO_DEAL` as first-class outcome |
| 11 | Dates for SAP Sourcing/Supplier Management Assistants unverified | Low | Confirm before quoting |
| 13 | "No-code" config lets a user build an unsafe workflow | High | Mitigation: invariants are non-editable and enforced by the validator at publish; DESIGNER/ADMIN split; every publish audited with diff; presets start from the safe baseline |
| 14 | Studio becomes a second product (scope creep) | Medium | Configure only what the runtime already parameterizes; fixed stage order and message types; no custom code; each Studio page maps to a typed schema |
| 15 | Prompt editing by non-engineers degrades quality silently | Medium | Prompt tests on saved samples gate publish; version diff; instant rollback |
| 12 | Port assignments conflict with other local projects | Low | API 8005, web 3010 chosen as unused in the user's registry; user to record them |

---

## 17. Glossary

| Term | Definition |
|---|---|
| SBN | SAP Business Network — SAP's supplier–buyer network (~6M suppliers) |
| Track 1 | Supplier diversification and lifecycle management for direct materials |
| Direct materials | Inputs that become part of the finished product (vs. indirect goods/services) |
| Buyer agent / Supplier agent | LLM-assisted lead agents acting for a buyer or a supplier, each bound by a mandate |
| Subagent | A typed tool-like worker (e.g., news risk, landed cost) called by a lead agent |
| Orchestrator | Deterministic SBN-layer component: state machine, bus, policy engine, audit, escalation |
| Mandate | Signed set of limits an agent may not exceed (price, lead time, clauses, rounds, autonomy) |
| Policy engine | Code that checks every outbound message against the sender's mandate and platform rules |
| Predicate probe | A pass/fail check of a message against a counterparty mandate that reveals no thresholds |
| RFQ | Request for quotation |
| Landed cost | Unit price plus freight, duties/tariffs and risk-adjusted cost |
| Warm bench | Pre-qualified suppliers kept ready before a need appears |
| Escalation | Pausing a project and routing an item to a human with reasons |
| Autonomy level L1–L4 | Suggest only / act after approval / act then report / act within hard limits |
| Override rate | Share of agent recommendations a reviewer rejects or edits; near zero indicates rubber-stamping |
| IMDA-style test | Singapore IMDA-inspired check of human oversight quality via override monitoring |
| Entity resolution | Merging records that describe the same company while blocking false merges |
| UFLPA | US Uyghur Forced Labor Prevention Act |
| Ledger hold | Block on payments tied to an agent decision until a human releases it |
| Replay mode | Deterministic mode serving recorded LLM/external responses |
| NO_DEAL | Valid terminal outcome when buyer limits and supplier limits do not overlap |
| Hash-chained audit log | Append-only log where each event's hash includes the previous event's hash |
| SSE | Server-sent events, used to stream run events to the UI |
| Multi-homing | Suppliers listing on several competing networks |
| Studio | The no-code UI for configuring models, agents, prompts, workflow, rules, templates and data sources |
| Invariant | A safety property that is visible in the Studio but cannot be changed by configuration |
| Draft / Published config | Editable working copy vs immutable, versioned configuration that projects pin at start |
| Preset | A prebuilt starting configuration (e.g., Safe demo) that only fills a draft |
| Canary deployment | Rollout to a few ideal-profile customers with an internal sponsor, in shadow mode first |

---

## Implementation status

* **Negotiation judge (built):** every negotiation turn is judged for conduct by code (optionally with a second model opinion that can only add a concern); an inappropriate turn drops the candidate, concerns are shown in plain language to the people involved. See TRD implementation status.
* **Test-bed security (built, simplified):** per-run agent identity, repeated-violation quarantine, external audit anchor file, platform kill switch. MFA and key rotation are out of scope for the proof of concept.

Requirements above are unchanged. Status of the prototype in this repository:

* **Implemented:** discovery -> qualification -> sourcing (RFQ + bounded negotiation) -> contracting -> onboarding on synthetic data, with the
  deterministic orchestrator, mandate/policy enforcement in code, hash-chained audit, human checkpoints (compliance flag, technical qualification,
  award, locked clause, signature, bank details, hold release), the 6 demo scenarios, the Studio (versioned, pinned, invariant-protected configuration),
  the React UI, the evaluation harness with ablations, and the adversarial suite.
* **Partially implemented:** Studio coverage of the H-series requirements is a generic schema-driven form (no stage graph, rule what-if or prompt
  preview widgets); autonomy and batched checkpoints apply only to the one non-mandatory checkpoint (requirement confirmation); only the news-risk
  agent uses an LLM; live external data feeds do not exist (news and weather are synthetic).
* **Planned / not built:** mandate-edit and reveal flows, ledger-hold and metrics routes, setup wizard, replay management, Postgres validation, Docker
  verification, supplier-side UI beyond the filtered workspace view. LIMIT-1 (watchlist token-drop match, see docs/adversarial-report.md) remains an open policy decision.



## Connectors (added after the second review)

The PRD kept "real SBN Discovery / Ariba connectors" out of scope; that remains true for SAP-side systems (documented fail-closed stubs only).
Two public-data connectors are real: OFAC SDN sanctions (loaded into the watchlist by `python -m sourceloop.connectors sync-ofac`) and an
opt-in Open-Meteo weather signal (Studio `data_sources.weather_source`, LIVE LLM mode only). The "Missing data" flags in demo runs come from
deliberately blanked synthetic fields, not from a connector failure. Full map, measurements and gaps: `docs/03-connectors.md`.

## Known deviations (second review)

Must-haves that are missing or only partly met in this prototype. Each is deliberate and listed so nobody assumes otherwise.

* **G1 OVERDUE:** `orchestrator.approvals.sla_fields` derives `due_at` / `overdue` (default SLA 24 h, 8 h for bank details and hold release; no Studio SLA field yet), but the approvals list response only shows it once the API router adopts it. No escalation-on-overdue action exists.
* **G2 Reviewer override rates:** built as `GET /reviewers/override-rates` (computed in SQL from resolved approvals; flag `POSSIBLE_RUBBER_STAMP` at >= 20 decisions and < 2% overrides). No UI widget yet.
* **G3 Audit export / tamper:** built as `GET /projects/{id}/audit/export` (NDJSON, trailer with head hash and anchor check). `TAMPER_SUSPECTED` blocking is implemented in `orchestrator/tamper_guard.py`; it only takes effect on start/resume once `api/service.py` calls it.
* **A1:** <!-- A1: status per the concurrent fixer's outcome - to be filled in by that owner -->
* **A2 Mandate-edit route:** mandates are immutable and versioned in the schema, but there is no API route to create a new mandate version or restart a stage.
* **B2 `mandate_ref`:** messages do not carry a mandate reference/version; the mandate is bound to the participant on the bus.
* **B4 per-turn timeout / STALLED:** no per-turn timeout or watchdog; duplicate-identical-counter detection (two identical counters => STALLED) is not implemented. `STALLED` exists in the status enum and transitions but the executor never enters it.
* **B5 max rounds:** hitting the round cap ends the negotiation as `NO_DEAL` with no escalation approval (PRD asks for an escalation).
* **C2 Non-incumbent discovery:** discovery does not guarantee a non-incumbent supplier in the shortlist.
* **C3 `NO_RESPONSE` window:** invites are answered within the same synchronous round; there is no 60 s simulated-time window, so `NO_RESPONSE` is not produced.
* **D3 Audits / samples:** no audit-scheduling or sample-request flow.
* **F4 Bank-details rules:** the bank-details checkpoint is a human confirmation only; automatic bank-detail rule checks are a stub.
* **Epic H widgets:** the Studio is a generic schema-driven form; stage graph, rule what-if and prompt preview widgets are missing.
* **Postgres:** immutability triggers and migrations exist but are untested against a live Postgres.
