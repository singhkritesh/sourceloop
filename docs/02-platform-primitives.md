# Platform Primitives — what SAP Business Network must provide (v1)

Brief: *"Map the agent interaction model, identify platform primitives required, and build a prototype."* This document is the second deliverable. A **primitive** is a capability the platform must offer so that buyer and supplier agents can collaborate end-to-end without human bottlenecks *and* without losing control. For each: what it is, why agents need it, what the prototype does, what exists in SBN today (to be confirmed with SAP), and the production gap.

> SAP asset names below come from the Oct 1 call and the research report. Whether each exists in the form needed is **an open question for SAP**, not a finding.

## 1. Summary table

| # | Primitive | Layer | Agent need | Prototype module | SAP asset to build on | Priority |
|---|---|---|---|---|---|---|
| P1 | Agent identity & registry | Trust | Know who an agent is and whom it acts for | `bus/` + `data` (orgs, signing keys) | SBN verified supplier/buyer identities | Must |
| P2 | Mandates (delegated authority) | Trust | Bounded, signed, revocable limits | `policy/`, `mandate` table | Ariba approval limits (concept) | Must |
| P3 | Policy & guardrail engine | Control | Enforce limits and rules in code | `policy/` pure functions | Compliance workflows | Must |
| P4 | Typed agent-to-agent messaging | Interaction | Structured, validated, signed exchanges | `bus/` | Business Network document exchange | Must |
| P5 | Verified supplier data & transaction history | Data | Rank and qualify on trusted facts | Registry tables, `transaction_history` | SBN Discovery, transaction data | Must |
| P6 | Compliance & risk screening services | Data | Sanctions, forced-labor, certificate validity | `compliance.rules` | Screening/compliance partners | Must |
| P7 | External signal services (news, weather, logistics, price index) | Data | Delivery and region risk, price bands | `net/safe_http`, subagents | — (third party) | Should |
| P8 | Orchestration & workflow engine | Control | Stages, checkpoints, parallelism, resume | `orchestrator/` | Ariba Sourcing workflow, AI Agent Hub | Must |
| P9 | Human approval & escalation service | Control | Route exceptions, track overrides | `approval`, override-rate | Joule / task inbox | Must |
| P10 | Audit & decision ledger | Assurance | Prove who/what decided on which evidence | `audit/` hash chain | Transaction ledgers | Must |
| P11 | Isolation & information boundary | Assurance | Keep one side's secrets from the other | Side-scoped queries, predicates | Network tenancy | Must |
| P12 | Model & agent gateway | AI | Swap models/providers, budget, secure hosting | `llm/` + Studio models | Joule, BTP AI Core (to confirm) | Must |
| P13 | Prompt & agent configuration management | AI | Safe no-code change of behavior | Studio prompts/agents | — | Should |
| P14 | Contract & clause service | Domain | Assemble from approved clauses, track redlines | `clause`, `contract` | Icertis | Must |
| P15 | Payment hold & finance hooks | Domain | Block money until a human releases | `ledger_hold` | Ledger/payment controls | Must |
| P16 | Config lifecycle (draft/publish/pin/rollback) | Platform | Change behavior without breaking running work | `studio/lifecycle` | — | Must |
| P17 | Evaluation & adversarial testing harness | Assurance | Prove controls hold; compare to baseline | `tests/adversarial`, metrics | — | Must |
| P18 | Observability & cost control | Platform | Token budgets, traces, stuck-run detection | watchdog, token counters | BTP observability | Should |

## 2. Primitive detail

### P1 Agent identity & registry
- **Need:** every message must be attributable to an agent *and* the organization it represents; agents of the same org must not impersonate another org.
- **Prototype:** per-org HMAC signing keys; `from_agent` must belong to a participant org; signature verified on every message.
- **Production gap:** real key management (HSM/KMS), agent credential rotation, agent-to-human-owner mapping, revocation propagation.

### P2 Mandates (delegated authority)
- **Need:** an agent acts only inside limits a human approved: price band, lead time, excluded countries, locked clauses, round cap, autonomy per stage; supplier side: margin floor, capacity, buyer-rating minimum.
- **Prototype:** signed immutable JSON, versioned; changes only before `start`; limits evaluated by the policy engine, never by the agent.
- **Production gap:** who may sign, expiry, revocation mid-flight, dual-control for high-value mandates, link to ERP authority matrices.
- **Liability link:** the mandate is the basis for the working hypothesis that the buyer owns the decision (SAP legal to confirm).

### P3 Policy & guardrail engine
- Pure functions `check(message, sender_mandate) → (passed, rule_id)` plus platform rules (locked clauses, price band, step size, round cap, sanctions).
- Returns **predicates only** across the boundary (see P11).
- **Gap:** policy authoring UX for customers, rule conflict detection, regional rule packs (UFLPA, EU Forced Labour Regulation) maintained from primary sources.

### P4 Typed agent-to-agent messaging
- 16 message types, strict schemas (`extra=forbid`), type-for-stage validation, `in_reply_to` threading, replay protection via `(project_id, seq)`.
- **Why typed:** free-text negotiation is unauditable and injectable; typed offers can be checked against a mandate.
- **Gap:** standard schemas across ERPs, versioning, interop with non-SAP agents (cross-network), rate limits per agent.

### P5 Verified supplier data & transaction history
- **SAP's edge:** verified identities, certificates, on-time delivery, quality, disputes — data rivals lack.
- Prototype uses a synthetic registry with planted problems (duplicates, look-alikes, expired certificates, missing fields).
- **Gap:** field-level provenance (verified vs self-declared), refresh cadence, entity resolution at network scale, coverage of niche categories (cold start).

### P6 Compliance & risk screening services
- Rules-based: certificate presence and expiry, watchlist (fuzzy + aliases), forced-labor/sub-tier flags, missing-data handling. LLM may read documents, but **status is set by rules only**.
- **Gap:** live sanctions feeds, sub-tier visibility, regulatory change management, false-positive workflow for Compliance.

### P7 External signal services
- News (region risk), weather/logistics (delay probability), price index (band checks). All pass through the allowlisted client and an extraction step (untrusted input).
- **Gap:** licensed data, source reliability scoring, caching, legal terms for news use.

### P8 Orchestration & workflow engine
- Resumable state machine derived from DB state, parallel fan-out to suppliers, per-stage autonomy and checkpoint config, watchdog for stuck runs.
- **Gap:** durable workflow runtime at scale, multi-project fairness, SLA timers per checkpoint.

### P9 Human approval & escalation service
- Role-scoped queue, evidence and recommendation shown, version-locked resolution, mandatory comment on reject/edit, override-rate monitoring, spot-check sampling.
- **Why a primitive:** "no bottlenecks" is achieved by making the *human step* fast and well-informed, not by removing it.
- **Gap:** mobile/Joule delivery, delegation and out-of-office, batching, SLAs.

### P10 Audit & decision ledger
- Append-only, hash-chained; fields: actor, action, rule id, evidence refs, model id/version, config version, outcome, approver. Exportable and verifiable.
- **Gap:** external anchoring, retention policy, legal admissibility, privacy handling of logged evidence.

### P11 Isolation & information boundary
- Neither side can read the other's mandate; offers from competing suppliers are invisible to each other; cross-tenant access returns 404; thresholds never appear in responses or cross-side logs.
- **Why it matters:** antitrust exposure on a shared price-visible network (the RealPage settlement is the warning case).
- **Gap:** formal review with counsel, differential-privacy-style aggregation for any cross-buyer analytics, probing resistance beyond round caps.

### P12 Model & agent gateway
- One interface for providers (Ollama, hosted, OpenAI-compatible), role defaults with per-agent overrides, budgets (tokens/calls), replay/record, secure hosting, provider-specific quirks hidden.
- Configurable in the Studio; keys encrypted and write-only.
- **Gap:** data-residency routing, model evaluation gates before swap, fallback chains, cost attribution per customer.

### P13 Prompt & agent configuration management
- Prompt bodies editable with variables palette, preview, tests, history; code-owned wrapper/preamble/schema. Agents enabled per stage with limits.
- **Gap:** prompt approval workflow, A/B evaluation, per-customer overrides on a shared base.

### P14 Contract & clause service
- Approved clause library (versioned; `negotiable` vs `locked`), assembly, deviation highlighting, redline loop with supplier agent.
- Liability, termination and IP are locked and always reach Legal.
- **Gap:** Icertis integration, clause-fallback playbooks, jurisdictional variants.

### P15 Payment hold & finance hooks
- Ledger hold created at onboarding; only FINANCE can release; bank detail confirmation is a human gate.
- **Gap:** real payment system hooks, segregation of duties, fraud checks for changed bank details.

### P16 Config lifecycle
- Draft → validate (schema, invariants, dependencies, model tests, prompt tests, reachability) → publish (immutable) → pin per project → diff/rollback → export/import.
- **Why a primitive:** without pinning, a configuration change mid-run alters decisions and breaks evidence.

### P17 Evaluation & adversarial harness
- Baseline comparison (structured search vs agent), ablation (switch controls off one at a time), 18-case adversarial suite (injection, mandate probing, forged messages, look-alike evasion, rubber-stamping, SSRF, config tampering), replay determinism.
- **Gap:** real-data evaluation, human-baseline time studies, continuous evaluation in canary.

### P18 Observability & cost control
- Per-project token budget, per-call logging (tool, input hash, confidence, latency, model), stuck-run watchdog, SSE live view.
- **Gap:** tracing across customers, anomaly alerts, cost dashboards.

## 3. How primitives support each stage

| Stage | Primitives used |
|---|---|
| Need | P2, P8, P9, P12 |
| Discovery | P1, P5, P7, P4, P8, P11 |
| Qualification | P5, P6, P7, P9, P10 |
| Sourcing | P2, P3, P4, P7, P11, P12, P10 |
| Contracting | P3, P14, P9, P10 |
| Onboarding | P15, P9, P10 |
| Across all | P8, P10, P12, P13, P16, P17, P18 |

## 4. What the prototype proves vs. what stays open

| Proves (design) | Does not prove |
|---|---|
| The interaction model is workable end to end with typed messages | Real-world supplier data quality or coverage |
| Limits can be enforced outside prompts and survive adversarial cases | Production scale, latency, cost |
| Neither side needs the other's secrets to reach or fail a deal | Legal acceptability of the liability model |
| A no-code layer can reconfigure behavior without weakening invariants | Customer willingness to adopt (interviews needed) |
| Every decision can be reconstructed from the audit chain | Real negotiation outcomes with human counterparties |

## 5. Questions for SAP

1. Which of P1–P18 already exist as services in SBN, Ariba, Joule, AI Agent Hub or Icertis, and which are net new?
2. Are supplier-side agents in scope for the SBN roadmap, and who owns supplier consent?
3. What is the approved position on agent liability and on price visibility between buyers?
4. Can SAP's simulated data framework load our synthetic universe?
5. Which fields in SBN are verified vs self-declared, and how often are they refreshed?
