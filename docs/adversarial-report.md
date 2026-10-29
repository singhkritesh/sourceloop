# Adversarial suite report

Location: `tests/adversarial/` (marker `adversarial`; `.venv/bin/pytest tests/adversarial -q`). Offline (socket/DNS patched to fail),
seeded, `FixedClock`; about 7 s. The PRD/TRD name the suite ("18 cases", A-07..A-09 access, A-12 SSRF) but do not list the cases, so the
18 below are derived from the PRD 12.5 attack-surface table, the risk table and the guardrails.

| ID | Case | File | PRD 12.5 / guardrail |
|---|---|---|---|
| ADV-01 | Forged / tampered signature, wrong-key, identity spoof, replayed id | test_adv_bus | Forged message, replayed offer |
| ADV-02 | Wrong stage / project, type not allowed for stage (exhaustive stage x type) | test_adv_bus | Stage check |
| ADV-03 | Sender-kind spoof (supplier sending BRIEF/INVITE/RFQ ...), full matrix | test_adv_bus | Bus sender rules |
| ADV-04 | Extra/unknown payload keys, hostile numbers, error text never echoes input | test_adv_bus | Data integrity (extra=forbid) |
| ADV-05 | Floor / ceiling / round cap / step / band / capacity / lead, ACCEPT semantics, 400-case seeded property loop | test_adv_bus | Probing, round cap, min step |
| ADV-06 | Mandate leakage (audit, approvals, Rejected, inboxes, tool caches, logs, exceptions) | test_adv_leak | Information leak = 0 |
| ADV-07 | BLOCK never waivable; ESCALATE only via human waiver, per rule | test_adv_bus | Fail closed / human checkpoint |
| ADV-08 | Locked clauses liability/termination/ip | test_adv_bus | Locked clauses -> Legal |
| ADV-09 | Prompt injection in news: wrapper breakout, zero-width, fake tags, prompt hygiene | test_adv_llm | Injection >= 4 cases |
| ADV-10 | LLM cannot set QUALIFIED/BLOCKED or a price outside code options | test_adv_llm | Output trust |
| ADV-11 | LLM garbage / timeout / huge / deep-nested => confidence 0 + human | test_adv_llm | Fail closed |
| ADV-12 | Watchlist look-alike / duplicate-entity evasion | test_adv_compliance | Look-alike evasion |
| ADV-13 | Inflated claims, expired certs, missing data never a pass | test_adv_compliance | Free-text claim, missing data |
| ADV-14 | Audit tamper (alter/delete/reorder/truncate), audit-before-delivery | test_adv_audit | Tampered audit log |
| ADV-15 | Parallel projects, no bleed; double start | test_adv_concurrency | Concurrency |
| ADV-16 | SSRF: metadata, private, link-local, mapped IPv6, rebinding, redirects, size cap, supplier URLs | test_adv_ssrf | SSRF (A-12) |
| ADV-17 | Determinism: 5x identical hash, PYTHONHASHSEED-independent, no wall clock/RNG | test_adv_determinism | Replay determinism |
| ADV-18 | Human checkpoints: role binding, comment rules, REJECTED never proceeds, no stage skipping | test_adv_human | Human checkpoints |

Strict xfails are confirmed defects in core code. FIXED rows have had their xfail marker removed; OPEN rows (only LIMIT-1 now) are still strict xfails:

| Bug | Status | Where | Impact | Minimal fix |
|---|---|---|---|---|
| BUG-1 | FIXED | bus/bus.py `_ctx` (l.230) + policy/engine.py ACCEPT | ACCEPT may reference own COUNTER or another supplier's offer; a buyer self-ACCEPT of a below-floor counter is delivered | In `_ctx`/`_track` store author+recipient; reject ACCEPT unless offer.from == env.to_agent and offer.to == env.from_agent |
| BUG-2 | FIXED | policy/engine.py `_check_clauses` (l.180) | Draft omitting liability/termination/ip is ALLOWED | ESCALATE `LOCKED_CLAUSE` if any locked key is missing from `clause_keys` |
| BUG-3 | FIXED | data/synthetic.py l.123-131 | Buyer-visible registry carries every supplier `unit_cost` | `replace(s, unit_cost=None)` in the visible copy |
| BUG-4 | FIXED | security/injection.py `wrap_untrusted` l.30 | `</UNTRUSTED_DATA>` and `</ untrusted_data >` survive | Case-insensitive regex `<\s*/\s*untrusted_data\s*>` replace |
| BUG-5 | FIXED | security/injection.py `_PATTERNS` l.5; subagents/buyer_tools.py l.98 | Extra whitespace/newlines evade the injection flag and the poisoned-article filter | Use `\s+`; drop articles by `injection_flags(a.text)` |
| BUG-6 | FIXED | agents/common/contracts.py `NewsIn.country` l.73 | Unconstrained string is interpolated into the SYSTEM prompt | `Annotated[str, Field(pattern=r"^[A-Z]{2}$")]` |
| BUG-7 | FIXED | agents/common/contracts.py `NewsExtraction.signals` l.85 + llm/client.py `_repair` | After garbage, repair can return `{}` => valid "no risk" at confidence 0.8 | Make `signals` required; cap confidence when repaired |
| BUG-8 | FIXED | subagents/buyer_tools.py l.63 | Only the most-complete record of a duplicate cluster is watchlist-screened | Screen every member (or union of names/aliases) |
| BUG-9 | FIXED | compliance/rules.py l.81 | NaN `financial_risk` => QUALIFIED | `if not (risk <= max)` (also for other numerics) |
| BUG-10 | FIXED | audit/chain.py `verify` l.142 | Tail truncation leaves a valid prefix | Compare last record with tracked head / external anchor. **Fix:** `verify_chain(records, anchor)`; `AuditLog.verify` checks the store's anchor (memory: separate dict; SQL: `audit_anchor` row written in the same transaction as the events, forward-only by trigger, backfilled by migration 0002) and the head it last wrote. Residual: an attacker who can also drop triggers and rewrite the anchor row is out of scope (needs an external anchor, e.g. periodic head hash export) |
| BUG-11 | FIXED | orchestrator/runtime.py `run` l.134 | Concurrent `run()` on one project duplicates BRIEF/INVITE and ends IllegalTransition | In-process per-project lock / claim token (API-level DB claim must also exist). **Fix:** `ProjectRuntime.running` claim taken atomically in `RunExecutor.run()`; a second concurrent call raises `RunInProgress` (no traffic, no audit); the API's DB claim is unchanged |
| BUG-12 | FIXED | net/safe_http.py l.23-30 | 100.64.0.0/10 reachable | Block `not ip.is_global` |
| BUG-13 | FIXED | net/safe_http.py l.65-68 | 2 MB cap enforced after full download | `client.stream()` and abort at the cap |
| LIMIT-1 | OPEN | compliance/rules.py | Watchlist name with a whole token dropped ("Acme Holding" vs "Acme Holding Group") is not matched | Threshold/subset policy decision, not a code slip |

Not tested / notes: `AgentChannel._bus` makes mandates reachable by Python introspection (agents are trusted code, enforced by
import-linter, so not asserted); API/DB layers (authz matrix, SQL scoping, rate limits, SSE) and Studio invariants are owned elsewhere;
live Ollama is out of scope; token-budget exhaustion inside a tool is no longer swallowed: `ToolRunner` re-raises `BudgetExceeded`, so the executor
pauses the run (`BUDGET_EXCEEDED`); regression test `test_budget_exceeded_in_a_tool_pauses_the_run`.

## Accepted risks

- **Unkeyed audit chain.** The per-project chain is `sha256(prev_hash + canonical(body))` with no secret. Anyone with direct
  database write access AND the ability to drop the append-only triggers and the `audit_anchor` row can rewrite history and
  recompute a valid chain. Triggers, the forward-only anchor and tail-truncation detection raise the bar but are not a defence
  against a privileged DBA. Mitigation (not implemented): periodically export each stream's head hash to an external anchor
  (a separate write-once store, a signed timestamp service or a transparency log) and compare in `audit/verify`. The main
  chain is deliberately NOT rekeyed (replay determinism needs the same hashes).
- **Supplier audit view** no longer returns `prev_hash`/`hash` (they cover events hidden from the supplier). It returns a
  per-supplier-org keyed digest (`digest`, HMAC with a server secret over the supplier-visible record) instead; the same
  digest replaces `hash` in supplier SSE `audit_event` frames. Chain verification endpoints stay EVALUATOR/ADMIN only.
- **Restart recovery is terminal, not resumable.** The orchestrator runtime lives in memory; after a restart unfinished
  projects that cannot be rebuilt by replay (see TRD, restart recovery) are closed as `STOPPED` / outcome `FAILED` / reason `RESTART_LOST_RUNTIME` and their pending approvals are rejected; replay-mode projects are restored.
- **Scenario baseline runs** (behaviour scenarios) still execute while a project is created; they are cached per
  (config version, scenario, need, seed), limited to `max_baseline_runs` concurrent runs, and time-bounded
  (`baseline_timeout_s`, 503 `BASELINE_TIMEOUT`).
