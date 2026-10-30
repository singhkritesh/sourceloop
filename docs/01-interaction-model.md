# Track 1 – Agent Interaction Model (v1)

Scope: direct materials; high tech, industrial, consumer products. Discovery → qualification → sourcing → contracting (+ onboarding stub).
Principles: **(1)** agents prepare and propose within signed limits; humans decide at defined points. **(2)** Limits live in code, not prompts. **(3)** A neutral, deterministic orchestrator sits between the two sides; neither side sees the other's mandate. **(4)** Everything below the invariants is configurable from the Studio (no code) and pinned per project.

Companion docs: `PRD.md` (what/why), `TRD.md` (how), `02-platform-primitives.md` (what SBN must provide).

## 1. Actors

| Actor | Role | Holds |
|---|---|---|
| Orchestrator (SBN layer) | Deterministic: state machine, message bus, policy engine, audit, escalation, approval queue. Never reasons about price. | Pinned config version; predicates over both mandates (pass/fail only) |
| Buyer agent | Lead agent for one sourcing project; calls buyer subagents | Buyer **mandate** (price band, lead time, excluded countries, locked clauses, round cap, autonomy per stage) |
| Supplier agent | Lead agent per supplier; calls supplier subagents | Supplier **mandate** (margin floor, capacity, provable certificates, unchangeable clauses, min buyer rating) |
| Subagents | Typed tools returning `{result, confidence, sources[], assumptions[]}`; never peers | Nothing beyond their input |
| Humans | Requester/buyer, compliance, quality, legal, finance, supplier rep, designer/admin (Studio) | Approvals; configuration |

```
                 ┌───────────────────────────────────────┐
                 │ Orchestrator (deterministic SBN layer)│
                 │ state machine · bus · policy · audit  │
                 │ escalation · approvals · pinned config│
                 └──────┬─────────────────────────┬──────┘
         typed, signed  │                         │  typed, signed
        ┌───────────────▼───┐                 ┌───▼────────────────┐
        │   BUYER AGENT     │                 │   SUPPLIER AGENT   │
        │ research          │                 │ quote generation   │
        │ compliance screen │                 │ buyer verification │
        │ regional news     │                 │ capacity & margin  │
        │ weather/logistics │                 │ demand forecast    │
        │ landed cost (P&L) │                 │ compliance docs    │
        │ procurement       │                 └────────────────────┘
        │ contract assembly │
        └───────────────────┘          Studio (UI) ──► draft ─► validate ─► publish ─► pinned at project start
```

## 2. Project state machine

```
NEED → DISCOVERY → QUALIFICATION → SOURCING → CONTRACTING → ONBOARDING → DONE
          │              │             │            │
          └──── ESCALATED (waiting on human; resumes same stage) ───┘
any stage → STOPPED (policy block / no viable supplier / user stop)
any stage → FAILED_RECOVERABLE | STALLED | BUDGET_EXCEEDED (resumable)
```

Stage **order and message types are fixed**. Which stages are enabled (onboarding optional), their autonomy level, checkpoint role/mode and exit parameters come from the pinned Studio config. Every transition is an audit event; a stage advances only when its exit condition holds.

## 3. Stages: what moves, who decides

| Stage | Buyer side does | Supplier side does | Messages | Exit condition | Human checkpoint (default) | Hard guardrail |
|---|---|---|---|---|---|---|
| 1 Need | Structures request into a brief | – | `Brief` | Requester confirms | Requester (L2) | Cannot change spec |
| 2 Discovery | Research subagent searches SBN history then external; dedupes; ranks; diversifies | Replies to invitations (interest, capacity) | `Invite`, `InterestReply` | Shortlist with reasons | Buyer may edit list (L3, async) | Excluded countries/sanctioned names filtered before ranking |
| 3 Qualification | Compliance screen (rules), news risk, requests docs | Compliance Docs subagent supplies verified docs | `DocRequest`, `DocSubmission`, `QualResult` | Each supplier QUALIFIED / REJECTED / REVIEW / BLOCKED | Compliance reviews flags; Quality owns technical pass/fail | Watchlist hit = block + escalate; agent cannot approve a part |
| 4 Sourcing | Procurement issues RFQ; weather + landed cost compare; counters in mandate | Buyer verification → capacity/margin → quote generation; counters in mandate | `RFQ`, `Offer`, `Counter`, `Accept`, `Reject` | Award recommendation or `NO_DEAL` | Buyer approves award (L2) | Out-of-mandate message not delivered; round cap; min step; price-band check |
| 5 Contracting | Contract assembly from approved clauses | Accept / redline negotiable clauses | `ContractDraft`, `Redline`, `SignRequest` (the bus also admits `Accept`, `Reject`, `Escalate` in this stage per `STAGE_TYPES`; the table lists the messages the default flow actually uses) | Signature (simulated) | Legal on every non-standard term; liability/termination/IP always | Only library clauses; locked clauses cannot be edited |
| 6 Onboarding | Collects master data | Supplies data | `OnboardData` | Vendor record + ledger hold | The buyer user confirms bank details and releases the hold in one decision | Agents cannot release hold |

Monitoring after go-live is out of scope for v1.

## 4. Autonomy levels

L1 suggest · L2 act after approval · L3 act then report · L4 act within hard limits. Ceilings per stage are **invariants** (e.g., contracting ≤ L2, award requires a human); the Studio can lower autonomy but never exceed a ceiling.

## 5. "No human bottlenecks" – how we read it

Humans stay but leave the critical path where safe:
- **Async / batched** approvals where the Studio permits (discovery list edits, small awards).
- **Pre-approved mandates**: humans approve limits once; agents work inside them.
- **Parallelism**: invites, document requests and RFQs go to all suppliers at once; a warm bench skips discovery and pre-qualification.
- **Never removed** (invariants): compliance hits, technical pass/fail, liability/termination/IP, bank release, signature.
- Measure *human touches per project*, *wait time at checkpoints* and *override rate* (near zero = rubber-stamping).

## 6. Message envelope (all agent-to-agent traffic)

```
{ id, project_id, stage, type, from_agent, to_agent,
  mandate_ref, payload, signature, ts, in_reply_to }
```
The bus validates schema (`extra=forbid`), type-for-stage, signature, participant, then the policy engine checks the **sender's** mandate. Only after the audit event is written is the message delivered. Isolation: a recipient sees only messages addressed to it; competing suppliers never see each other's offers.

## 7. Negotiation protocol

1. Buyer RFQ → each supplier agent decides: decline (reason code, e.g. buyer rating, capacity) or quote.
2. Supplier quote is generated by code from cost, margin floor and capacity; an LLM may only choose among code-supplied options or write the explanation.
3. Buyer compares landed cost (computed in code) with delivery-risk and region-risk signals, then counters from code-supplied price options.
4. Each side checks the other's message against **its own** mandate; the orchestrator additionally runs predicate probes so no thresholds are exchanged.
4a. An `Accept` is valid only for an offer authored by the Accept's recipient and addressed to its sender; anything else (own counter, another supplier's offer, unknown id) is rejected as `UNKNOWN_OFFER`. A `ContractDraft` that omits any locked clause (liability/termination/ip) escalates `LOCKED_CLAUSE`.
5. End states: `Accept` both sides → award recommendation; round cap hit or repeated counters → escalate; non-overlapping limits → `NO_DEAL` (a valid, explained outcome that reveals no numbers).

## 8. Escalation triggers

Defaults (editable in the Studio unless marked 🔒 invariant): 🔒 watchlist/forced-labor hit; 🔒 locked clause touched; 🔒 offer outside mandate; missing or unverified data; price outside index band; max rounds reached; supplier claim not backed by registry; subagent confidence below threshold; subagent timeout; custom-subagent signals; budget exceeded.
Escalation = project pauses at that stage; an approval item with reasons and evidence goes to the target role; resolution `APPROVED | REJECTED | EDITED` resumes or stops; no auto-approval ever.

## 9. Configuration surface (Studio) vs. fixed

| Configurable (pinned per project) | Fixed (invariants / structure) |
|---|---|
| Providers, models, roles, per-agent model overrides, replay/live mode | Orchestrator is deterministic; agents only talk through the bus |
| Agents/subagents on/off, stages, timeouts, tool-call limits, custom extraction subagents | Compliance screen always on; custom subagents give evidence, never pass/fail |
| Prompt instruction bodies | Untrusted-data wrapper, English preamble, output schema |
| Stage enablement, autonomy (below ceiling), checkpoint role/mode | Stage order, message types, autonomy ceilings |
| Escalation rules, mandate templates, scenarios, clause library, data sources, egress allowlist | Mandate secrecy, signing, audit chain, fail-closed, locked clauses, buyer-only hold release (admins never approve) |

## 10. Open questions

- Who signs a mandate and how is it revoked mid-project?
- How are supplier margins protected from leaking through repeated offers (information boundary beyond predicates)?
- Which checkpoints can SAP legally make async per customer?
- Where does a custom subagent's signal enter the decision (rule only, or also ranking)?
