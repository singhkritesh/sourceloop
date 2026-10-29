# SourceLoop security and AI-agent safety framework mapping

Date: 2026-10-21. Scope: prototype, synthetic data only. Code was read-only for this review.

## 1. Method and confidence

- Framework facts come from web searches run on 2026-10-21. Most results were secondary summaries (vendor and research blogs), not the primary
  standard text. I did not fetch the primary pages, so item names/versions below are "seen in search results", not "verified against the standard".
  Re-check the ones marked (secondary) before quoting them externally.
- Repo claims: "Covered" means I located both the code and a test or enforcing mechanism. I did not run the test suite. Where I saw code but no
  test, or a test but only on a partial surface, the status is Partial.
- Line numbers are as of this review; other agents are editing code concurrently, so treat them as anchors, not contracts.

## 2. Frameworks consulted (current editions as seen)

| Framework | Edition / date seen | Source (secondary unless noted) | Confidence |
|---|---|---|---|
| OWASP Top 10 (web) | 2025 edition, final Jan 2026. A01 Broken Access Control (absorbs SSRF), A02 Misconfig, A03 Software Supply Chain Failures, A04 Crypto, A05 Injection, A06 Insecure Design, A07 Auth Failures, A08 Software/Data Integrity, A09 Logging and Alerting Failures, A10 Mishandling of Exceptional Conditions | https://owasp.org/Top10/2025/ (appeared in results; page content not fetched), https://www.fastly.com/blog/new-2025-owasp-top-10-list-what-changed-what-you-need-to-know | Medium-high |
| OWASP Top 10 for LLM Applications | 2025 (v4.2.0a PDF dated 2024-11-14). LLM01 Prompt Injection first for the second edition. A 2026 update is mentioned in one result title but I could not confirm its content | https://owasp.github.io/www-project-top-10-for-large-language-model-applications/assets/PDF/OWASP-Top-10-for-LLMs-v2025.pdf | Medium (2026 revision unconfirmed) |
| OWASP Top 10 for Agentic Applications | 2026 edition, announced 2025-12-09. ASI01 Goal Hijack, ASI02 Tool Misuse, ASI03 Identity and Privilege Abuse, ASI04 Agentic Supply Chain, ASI05 Unexpected Code Execution, ASI06 Memory and Context Poisoning, ASI07 Insecure Inter-Agent Communication, ASI08 Cascading Failures, ASI09 Human-Agent Trust Exploitation, ASI10 Rogue Agents | https://goteleport.com/blog/owasp-top-10-agentic-applications/ , https://docs.modulos.ai/frameworks/owasp-top-10-agentic , https://cycode.com/blog/owasp-top-10-agentic-applications/ | Medium-high (names consistent across sources) |
| OWASP ASVS | 5.0.0, released May 2025; 17 chapters (V1 Encoding/Sanitization ... V17); about 345 requirements (70 L1, 183 L2, 92 L3). Target for this app: L2 (L3 for audit/crypto parts) | https://security-resilience.ai/asvs/index.html , https://codific.com/owasp-asvs-a-comprehensive-overview/ | Medium (counts vary by source) |
| NIST AI RMF | AI RMF 1.0 (NIST AI 100-1, Jan 2023) is still current; a revision was directed by the July 2025 AI Action Plan but no public draft was found as of mid-2026. GenAI Profile NIST AI 600-1 (July 2024), 12 risks | https://docs.modulos.ai/frameworks/nist-ai-rmf/generative-ai-profile , https://nistairmfkit.com/ai-rmf-2-0/ | Medium |
| NIST agent guidance | CAISI "AI Agent Standards Initiative" launched 2026-02-17 (industry standards, open protocols, security/identity research). NCCoE concept paper "Accelerating the Adoption of Software and AI Agent Identity and Authorization", 2026-02-05. No final standard found; it is a concept paper | https://labs.cloudsecurityalliance.org/research/csa-research-note-nist-caisi-ai-agent-standards-compliance-2/ , https://workos.com/blog/nist-ai-agent-standards-initiative-explained | Medium (not primary) |
| MITRE ATLAS | Release v2026.09 (2026-09-15): 16 tactics, 120 techniques, 88 sub-techniques, 40 mitigations, 73 case studies; 139 entries tagged Agentic. Relevant: AML.T0053 AI Agent Tool Invocation, AML.T0086 Exfiltration via Agent Tool Invocation, AML.T0101 Data Destruction via Agent Tool Invocation, context poisoning | https://www.vectra.ai/topics/mitre-atlas , https://www.speakeasy.com/blog/mitre-atlas-explained | Low-medium (counts from one vendor; only three technique IDs seen) |
| ISO/IEC 42001 | 42001:2023 is the only edition; no amendment registered as of 2026-10-05 (secondary). EN adoption 2026-03. Companions: 42005:2025 (impact assessment), 42006:2025 (certifier requirements) | https://gaicc.org/blog/iso-42000-standards-family/ , https://www.iso.org/standard/42006 | Medium |
| EU AI Act | Digital Omnibus on AI, cited as Regulation (EU) 2026/1744, in force 2026-07-27: Annex III stand-alone high-risk obligations moved from 2026-08-02 to 2027-12-02; product-embedded to 2028-08-02. Art. 50 transparency and GPAI duties stay on the 2026-08-02 schedule | https://usercentrics.com/knowledge-hub/eu-ai-act-high-risk-delay-article-50-transparency-consent/ , https://www.orrick.com/en/Insights/2026/07/EU-AI-Act-Update-Digital-Omnibus-Finalizes-8-Compliance-Changes | Medium (regulation number and dates from secondary sources; verify on EUR-Lex) |
| Google SAIF / CoSAI | SAIF 2.0 extends SAIF to agents (secondary). CoSAI "Agentic Identity and Access Management" whitepaper approved by CoSAI TSC 2026-03-20: per-agent short-lived identity, bound to verifiable claims (code/model signature), validated on every critical operation | https://www.coalitionforsecureai.org/wp-content/uploads/2026/04/agentic-identity-and-access-control.pdf , https://github.com/cosai-oasis/ws4-secure-design-agentic-systems | Medium. I could not confirm SAIF 2.0's exact risk list |
| MCP authorization | Spec 2026-07-28: OAuth 2.1, PKCE, Protected Resource Metadata, RFC 8707 resource indicators (token audience must match server), RFC 9207 issuer validation, move from DCR to client metadata documents | https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations , https://blog.modelcontextprotocol.io/posts/2026-07-28/ | Medium-high |

Not applicable as a unit: MCP itself. SourceLoop has no MCP servers/clients (tools are in-process `ToolRunner`), so MCP guidance is used only as a model for tool authorization.

### EU AI Act risk-class reasoning (analysis, not legal advice)

- Annex III lists employment, education, essential services (credit/insurance), law enforcement, migration, justice and critical infrastructure uses.
  Business-to-business direct-materials sourcing is not listed, so SourceLoop is most likely not high-risk. Caveat: if it were extended to rank or decide on natural-person
  sole traders, or to credit-score suppliers, re-assess.
- Article 50 (transparency) applies from 2026-08-02: a counterparty interacting with an AI agent should be told so. SourceLoop has no explicit "this message
  was produced by an AI agent" marker in the supplier UI or message envelope (grep of `src`, `web/src`, PRD found none): Gap, size S.
- Even if not high-risk, the controls the Act would require (human oversight Art. 14, logging Art. 12, accuracy/robustness Art. 15, risk management Art. 9) are the right design
  target and are mostly present (section 4 and 5). Voluntary alignment is cheap.

## 3. Threat-surface facts used below (verified in code)

- Trust boundary: `import-linter` contract in `pyproject.toml` [tool.importlinter] forbids `sourceloop.agents` importing data/policy/orchestrator/compliance/subagents/studio/audit.chain.
  `allow_indirect_imports = true` means an indirect path is not caught (Partial robustness).
- Bus pipeline `src/sourceloop/bus/bus.py:191-237`: project, replay, stage, type-for-stage, participants, sender kind, HMAC signature, strict payload parse, mandate policy, audit append, then deliver.
- Signing `src/sourceloop/bus/signing.py:14-36`: HMAC-SHA256 with per-org keys; `scenario.py:90` shows keys derived from one demo secret (`DerivedKeys`); prod uses `ORG_SIGNING_SECRET` (`api/config.py:56`).
- Audit `src/sourceloop/audit/chain.py` (hash chain, `verify_chain` at l.166 with anchor), `data/models.py:427-480` (audit_anchor table, SQLite triggers).
- Human gates `src/sourceloop/orchestrator/approvals.py` (role-bound, version-locked, rubber-stamp flag at l.100-107).
- LLM: `llm/client.py` (budget l.111-113, repair, replay), `security/injection.py` (sanitize, flags, wrapper).
- Egress `net/safe_http.py`. AuthN/Z `api/deps.py`, `api/routers/auth.py`, `api/security.py`.

## 4. Matrix: OWASP Top 10 for Agentic Applications (2026)

| ID | Applicable | How SourceLoop addresses it (evidence) | Status | Gap and recommended fix (effort) |
|---|---|---|---|---|
| ASI01 Goal Hijack | Yes: supplier text, news, certificates reach LLM subagents | Untrusted wrapper in user turn only (`security/injection.py:31`, `llm/prompt_builder.py`, tests ADV-09 test_adv_llm:109-202); schema extraction with extra=forbid (ADV-10 :219); LLM cannot set qualification or price (ADV-10 :225-270); limits in code `policy/engine.py`; invariant for wrapper `studio/invariants.py:175` | Covered for single-turn injection | Heuristic regex flags only (`_PATTERNS`, 8 patterns) are an alarm, not a control; the real control is architecture. No multi-turn or indirect-via-registry injection tests (M) |
| ASI02 Tool Misuse | Yes: ToolRunner tools | Typed tool IO (`agents/common/contracts.py`), per-tool enable/timeout (`tools.py:108-119`), per-turn call cap `TurnGuard` (`tools.py:76-97`; used `agents/buyer/agent.py:64`), single-flight/dedupe, tools implemented on trusted side. UNKNOWN_TOOL and DISABLED return errors (`tools.py:123-126`) | Partial -> Mitigated (2026-10-22) | Done: `AgentDef.allowed_tools` (`studio/schema.py`), `ToolRunner.set_allowed`/`NOT_ALLOWED` (`agents/common/tools.py`), `tool_denied` audit event (`orchestrator/scenario.py`), `INV_COMPLIANCE_TOOL_ALLOWED` (`studio/invariants.py`), tests `tests/test_tool_allowlist.py`. Original gap: No explicit per-agent tool allowlist test; no parameter-level authorization (e.g. buyer tool cannot be called for supplier not in project) beyond what tool code does. Add allowlist per agent key in pinned config + test (S) |
| ASI03 Identity and Privilege Abuse | Yes | Per-org HMAC keys and sender-kind matrix (`bus.py:200-205`; ADV-01, ADV-03); human roles + CSRF + cookie auth + revocation (`api/deps.py:80-135`); approver role binding (ADV-18 :44) | Partial | Agent identity = org key held by the same process; no per-agent/per-run credential, no expiry, no scope claims, one root secret (`DerivedKeys`/`ORG_SIGNING_SECRET`). CoSAI/NCCoE call for short-lived per-agent identity. Sketch in backlog item 3 (M) |
| ASI04 Agentic Supply Chain | Yes: Python deps, npm deps, LLM models/providers, prompts/config | Provider keys in vault (`studio/vault.py`), SSRF-safe egress, published configs immutable (invariants 15c), pinned `config_version` | Partial (2026-10-22: lockfiles, CI, SBOM, Dependabot, model provenance added; CI not executed locally) | Original gap: No Python lockfile in repo root (`ls` shows no `uv.lock`), no `pip-audit`/SBOM/CI workflow (no `.github`), `pyproject` uses `>=` ranges. Model identity/hash not recorded in audit. Now: `requirements*.lock`, `.github/workflows/ci.yml`, `.github/dependabot.yml`, `agents/common/provenance.py` + `subagent_call.model_id/tokens/output.llm`. Remaining: hash-pinned installs (`--require-hashes`), signed provenance (M) |
| ASI05 Unexpected Code Execution | Low: no code-exec tool, no `eval` by design | Tools are fixed functions; ruff `S` rules + bandit in dev deps (`pyproject.toml:26`); `parse_json_loose` is parsing only | Covered by design (not verified by test) | Add a test/linter rule banning `eval/exec/subprocess` in `src/` (S). Bandit is in dev deps but no CI runs it |
| ASI06 Memory and Context Poisoning | Partial: no long-term agent memory; replay store and tool caches exist | Tool cache keyed by input hash (`tools.py:130`), replay keys include body hash; news flagged articles dropped (ADV-09 :175); data in registry is synthetic and code-owned | Partial | Supplier-supplied profile/cert free text persists in registry and is re-presented to later runs; no provenance/quarantine tag. Also `data/replay/*` is trusted input with no integrity check (hash-pin manifest, S) |
| ASI07 Insecure Inter-Agent Communication | Yes: bus | Signed envelopes, replay-id rejection, stage/type/sender checks, strict schemas (ADV-01..04, ADV-15 :44, :59). Mandate secrecy (ADV-06) | Covered (in-process) | HMAC is symmetric and the verifier holds all keys; signing excludes `seq`/`ts` (`messages.py:155`) which is acceptable because bus assigns them, but there is no nonce/expiry window beyond per-run id set (`_seen_ids` in memory). If agents ever run out of process: asymmetric keys, mTLS (L) |
| ASI08 Cascading Failures | Yes | Fail closed to confidence 0 + human (ADV-11); token budget pauses run (`llm/client.py:111`, `runtime.py:184`); per-turn tool cap; project-level run claim (ADV-15); timeouts | Partial | `BudgetExceeded` swallowed inside a tool never reaches the executor (documented in `adversarial-report.md` "Not tested / notes"). No circuit breaker per provider, no global concurrency cap on LLM calls beyond semaphore 8 per runner (S-M) |
| ASI09 Human-Agent Trust Exploitation | Yes | Mandatory human checkpoints for compliance flags, award, locked clauses, hold release, signature (ADV-18, `approvals.py`); comment required for non-approve; rubber-stamp detector `approvals.py:100-107` (ADV-18 :109); edits constrained (ADV-18 :188-215) | Covered, with a UX caveat | Detector only reports; nothing forces re-review or shows the approver the raw evidence next to the agent's summary. Show source evidence and agent confidence together; add review-time floor (S) |
| ASI10 Rogue Agents | Yes | Agents cannot send outside bus rules; `POST /projects/{id}/stop` (`routers/projects.py:107`); STOPPED status; audit of every decision | Partial | Stop is manual, per project. No behavioural monitoring (e.g. policy-block rate, repeated probing per sender), no global kill switch / quarantine of a supplier agent. Add (backlog 5) (M) |

## 5. Matrix: OWASP Top 10 for LLM Applications (2025)

| ID | Applicable | Evidence | Status | Gap / fix |
|---|---|---|---|---|
| LLM01 Prompt Injection | Yes | See ASI01. `wrap_untrusted` breakout handling (ADV-09 :109), zero-width/NFKC strip (:115), poisoned article filter (:175) | Covered (single-turn) | Multi-turn and encoded payloads (base64, homoglyph beyond NFKC, non-English) untested (M) |
| LLM02 Sensitive Information Disclosure | Yes: counterparty mandates | Predicates not thresholds; leak tests across audit, SSE, logs, exceptions (ADV-06 test_adv_leak:69-171); log redaction `api/logging_config.py:13`; supplier cost removed from visible registry (:171); keyed digest for supplier audit | Covered | Redaction is key-name regex, not value scanning; side-channel via repeated bounded negotiation (price probing) mitigated by round cap and min step (ADV-05 :299-314) but no probe-rate alert (S) |
| LLM03 Supply Chain | Yes | See ASI04 | Gap | Same fix |
| LLM04 Data and Model Poisoning | Low (no training/finetune) | Hosted/local model consumed as-is; replay data static | N/A mostly; Partial for registry data | Record model digest per call in audit (S) |
| LLM05 Improper Output Handling | Yes | Strict Pydantic on LLM output (ADV-10 :219); prices only from code options (ADV-10 :270); single repair then fail (ADV-11 :313); web renders via React, no `dangerouslySetInnerHTML` found in `web/src` (grep) | Covered | Add CSP-compatible test for web render of supplier text (S) |
| LLM06 Excessive Agency | Yes | Autonomy capped (`invariants.py:23` CONTRACTING_AUTONOMY_MAX=2), humans for irreversible steps, mandate bounds enforced in code | Covered | Autonomy levels are Studio-configurable inside bounds; consider a signed change approval for raising them (S) |
| LLM07 System Prompt Leakage | Yes | Prompts contain no secrets/limits by rule; invariant forbids secret refs in prompt bodies (`invariants.py:196`, ADV-09 :158) | Covered | none material |
| LLM08 Vector and Embedding Weaknesses | N/A | No embeddings/RAG (CLAUDE.md section 3: rapidfuzz only) | N/A | Revisit if RAG added |
| LLM09 Misinformation | Yes: news/risk summaries, supplier claims | Sources required, confidence thresholds, inflated claim and missing data never pass (ADV-13), human review | Covered | Calibration of confidence not measured (eval harness exists in `src/sourceloop/eval`, outcome unverified) (M) |
| LLM10 Unbounded Consumption | Yes | Token budget per project (`api/config.py:23`), rate limits (`api/deps.py:64-72`, login routes only have explicit decorators `routers/auth.py:70,94`), body limit middleware, 2 MB egress cap, timeouts | Partial | Default limit applies globally but no per-org/day LLM spend cap; SSE connection count cap not found (S) |

## 6. Matrix: OWASP Top 10 web (2025) and ASVS 5.0 (target L2)

| Item | Evidence | Status | Gap / fix |
|---|---|---|---|
| A01 Broken Access Control (incl. SSRF) | SQL-scoped visibility `api/deps.py:150-170`; 404 for cross-tenant; authz matrix `tests/test_authz_matrix.py`; SSRF ADV-16 (test_adv_ssrf:67-263), `safe_http.py` pinned IP, no redirects, 2 MB stream cap | Covered | Matrix coverage of every new route must be enforced by a test that enumerates `app.routes` (S) |
| A02 Security Misconfiguration | Security headers `api/app.py:96-99` (nosniff, DENY, CSP); prod secret length checks `api/config.py:51-59`; non-root Docker `USER app` | Partial | No HSTS seen (grep: none) (S). CORS `allow_credentials=True` with configured origins (`app.py:141`) needs a prod-origin test (S) |
| A03 Software Supply Chain Failures | `.github/workflows/ci.yml` (pip-audit, bandit, npm audit, SBOM), locks, Dependabot (2026-10-22) | Partial (workflow unexecuted) | `npm audit` clean after the react-router 7.18 upgrade (2026-10-26); hash pinning |
| A04 Cryptographic Failures | argon2-cffi, Fernet vault, HMAC compare_digest, JWT HS256 with required claims (`api/security.py:65-71`) | Partial | HS256 shared secret, no key rotation/`kid`; signing keys all derived from one secret. Add key IDs + rotation (M) |
| A05 Injection | ORM only; strict schemas; ruff S rules | Covered | SQL metachar test ADV-04 :274 |
| A06 Insecure Design | Trust boundary, policy-as-code, invariants, threat tests | Covered | No written threat model doc in `docs/` (S): this file partially fills it |
| A07 Authentication Failures | Argon2, lockout, refresh rotation with reuse detection (`auth.py` docstring l.1), login rate limit, HttpOnly SameSite=Strict cookies (`auth.py:39-45`), access revocation (`deps.py:95-112`, migration 0003) | Covered (code read, tests in `test_api_auth.py` not re-run) | No MFA for ADMIN/FINANCE approvers (M) |
| A08 Software/Data Integrity Failures | Hash-chained audit, signed mandates, immutable configs, replay determinism | Partial | Replay files and Alembic not integrity-checked; audit chain unkeyed (accepted risk in `adversarial-report.md`) |
| A09 Logging and Alerting Failures | structlog JSON + redaction; audit events; healthy/red-flag log lines in CLAUDE.md | Partial | Logging yes, alerting no: nothing consumes `policy.block` / `injection.suspected` / `audit.verify valid=false` (backlog 5) |
| A10 Mishandling of Exceptional Conditions | Fail closed everywhere (ADV-11), re-raise after failure record (`runtime.py:190-192`), error envelope without echo (ADV-04 :263) | Covered | Add chaos test for DB failure mid-audit-append (S) |
| ASVS V2 Validation and Business Logic | Strict models, state machine, version locks | Covered | |
| ASVS V4 API | Error envelope, CSRF double-submit (`deps.py:80-92`) | Covered | |
| ASVS V6/V7 Authn/Session | See A07 | Covered/Partial | MFA is L2+; Gap (M) |
| ASVS V8 Authorization | Role + side scoping | Covered | Field-level authorization for supplier audit view uses keyed digest, good |
| ASVS V11 Cryptography | See A04 | Partial | |
| ASVS V13 Configuration | Secrets from env/vault; Studio invariants | Partial | No secrets scanning in CI |
| ASVS V14/V16 Logging (numbering differs by source; I did not confirm chapter numbers) | Redaction, audit | Partial | |

## 7. Matrix: NIST AI RMF 1.0, GenAI Profile (AI 600-1) and agent guidance

| Function / item | Applicable | Evidence | Status | Fix |
|---|---|---|---|---|
| GOVERN (policies, accountability, roles) | Yes | `docs/PRD.md`, `docs/TRD.md`, role matrix, Studio publish audit events (`studio.*`) | Partial | No AI risk policy or owner/RACI doc; no incident-response doc (S) |
| MAP (context, impact) | Yes | PRD risk table, `docs/adversarial-report.md`; synthetic data only | Partial | No documented impact assessment (ISO 42005 style) (S) |
| MEASURE (testing, metrics, TEVV) | Yes | 18-case adversarial suite, ablations, eval harness (`src/sourceloop/eval`, `docs/evaluation.md`), determinism test | Covered/Partial | Metrics are not tracked across releases in CI (M) |
| MANAGE (treat, monitor, respond) | Yes | Fail-closed, escalation, stop endpoint, accepted risks listed | Partial | No runtime monitoring or incident playbook (backlog 5) |
| AI 600-1 CBRN, violent content, dangerous content | N/A | Business sourcing domain | N/A | |
| AI 600-1 Confabulation | Yes | Sources required, schema extraction, humans on pass/fail | Covered | |
| AI 600-1 Data privacy | Low (synthetic) | Log redaction, mandate secrecy | Partial | Re-assess with real supplier data (PII in contacts) |
| AI 600-1 Information security (prompt injection, data leakage) | Yes | See LLM01/02 | Covered | |
| AI 600-1 Information integrity | Yes | Audit chain | Partial | external anchor |
| AI 600-1 Human-AI configuration | Yes | Human checkpoints, rubber-stamp flag | Covered | |
| AI 600-1 Value chain and component integration | Yes | Provider abstraction, vault | Gap | See ASI04 |
| AI 600-1 Bias/homogenization, environmental, IP, obscene content | Low | Supplier ranking uses deterministic `compliance/scoring.py` | Partial (bias) | No fairness test across supplier country/size (M) |
| NCCoE agent identity and authorization concept paper (2026-02) | Yes | Covers authN, authZ, auditing, non-repudiation, prompt-injection mitigation | Partial | Auditing/non-repudiation yes (signed envelopes + audit, human `approver_id`); identity and scoped authorization of agents: see ASI03 |
| CAISI Agent Standards Initiative | Informational | No published standard found | N/A (watch) | Re-check quarterly |

## 8. Matrix: MITRE ATLAS (v2026.09), ISO/IEC 42001, EU AI Act, SAIF/CoSAI, MCP

| Item | Applicable | Evidence | Status | Fix |
|---|---|---|---|---|
| ATLAS AML.T0051 LLM Prompt Injection (ID from memory, not confirmed this session) | Yes | ADV-09 | Covered | |
| ATLAS AML.T0053 AI Agent Tool Invocation | Yes | Typed tools, caps, no write tools for LLM beyond code-chosen options | Partial | allowlist test (ASI02) |
| ATLAS AML.T0086 Exfiltration via Agent Tool Invocation | Yes: tools that fetch URLs | Supplier URLs never fetched (ADV-16 :223, :244), egress allowlist | Covered | |
| ATLAS AML.T0101 Data Destruction via Agent Tool Invocation | Low | Agents have no delete tools; audit INSERT-only triggers | Covered (SQLite triggers only; Postgres path unconfirmed) | Add Postgres equivalent trigger/rule + test (M) |
| ATLAS context poisoning techniques | Yes | See ASI06 | Partial | |
| ISO/IEC 42001 clauses 4-10 (AIMS) | Org-level, not code | Docs exist; no management system | Gap (org) | Out of prototype scope; use Annex A controls as a checklist later |
| ISO 42001 Annex A: logging/traceability, human oversight, data quality, third-party | Mapped above | | Partial | |
| EU AI Act Art. 12 logging | Voluntary | Hash-chained audit with config_version | Covered | Retention and export policy undocumented (S) |
| EU AI Act Art. 14 human oversight | Voluntary | ADV-18 | Covered | |
| EU AI Act Art. 15 robustness/cybersecurity | Voluntary | Adversarial suite | Partial | |
| EU AI Act Art. 50 transparency | Yes (applies from 2026-08-02 per secondary sources) | `ai_generated` flag in message API/SSE + UI badge (2026-10-22) | Partially closed | Original: Add `ai_generated: true` marker on supplier-facing messages and UI banner (S) |
| SAIF/CoSAI: agent identity, least agency, auditable actions, human control | Yes | See ASI02/03/09 | Partial | |
| MCP authorization (OAuth 2.1, audience-bound tokens, per-tool scopes) | Not used today | Tools in-process | N/A now | If tools move to MCP: audience-bound tokens, per-tool scopes, tool allowlist |

## 9. Prioritized remediation backlog (top 10)

| # | Item | Why (framework) | Minimal implementation sketch | Effort |
|---|---|---|---|---|
| 1 | CI pipeline with lockfile, `pip-audit`, `bandit`, `ruff`, `lint-imports`, adversarial suite, `npm audit` | ASI04, LLM03, A03 | Add `.github/workflows/ci.yml` running `uv lock --check`, `pip-audit`, `bandit -r src`, `ruff`, `lint-imports`, `pytest -q`; commit `uv.lock`; pin `>=` ranges | S |
| 2 | SBOM and model/provider provenance | ASI04, AI 600-1 value chain | CI step `cyclonedx-py` and `npm sbom`; record `model`, `provider`, and model digest (Ollama `/api/show` digest) in the `llm_call` audit evidence | M |
| 3 | Per-agent run identity with scopes and expiry | ASI03, NCCoE, CoSAI | At run start orchestrator mints a per-run, per-agent token `{agent_id, project_id, scopes:[bus.send:OFFER,...], exp}` signed with a per-org key and a `kid`; bus verifies scope and expiry in `_submit` next to the signature check; tests: expired, wrong scope, cross-run reuse | M |
| 4 | External audit anchor | A08, accepted risk in adversarial report | Periodic job exports `(project_id, seq, head_hash)` to a write-once sink (S3 object lock, or signed timestamp via RFC 3161) and `audit/verify` compares; keep main chain unkeyed for replay | M |
| 5 | Behavioural monitoring and kill switch | ASI10, ASI08, A09 | Counters for `policy_block`, `injection.suspected`, probe-like offers per supplier per project; threshold sets `ProjectStatus.ESCALATED` and quarantines the participant (bus rejects with `QUARANTINED`); admin `POST /admin/agents/{id}/quarantine`; audit each | M |
| 6 | Tool allowlist per agent from pinned config, enforced in `ToolRunner.call` | ASI02, ATLAS T0053 | Add `allowed_tools` to `AgentConfig` in `studio/schema.py`; `ToolRunner.configure` rejects any non-listed name with `NOT_ALLOWED`; invariant forbids empty compliance tool; test in `tests/adversarial` | S |
| 7 | Multi-turn and encoded injection red-team cases | LLM01, ASI01 | Extend `test_adv_llm.py` with: multi-article split payload (payload halves in two articles), base64/rot13/homoglyph/non-English variants, injection inside certificate and company-profile free text; assert outcome unchanged (no QUALIFIED, price from options) not that the regex fires | M |
| 8 | AI-disclosure marker (EU AI Act Art. 50) and approver evidence view | Art. 50, ASI09 | Add `ai_generated: bool = True` to `Envelope` (extra=forbid means update schema and signing body together; documents update per rule 21) and a UI badge; show raw evidence next to agent summary in approval modal | S |
| 9 | Approver hardening | A07, ASI09 | Require MFA/step-up (TOTP) for FINANCE/LEGAL/COMPLIANCE resolution; add minimum dwell time and a second approver for hold release above a Studio threshold; enforce approver != project initiator | M |
| 10 | Key management upgrade | A04, ASI07 | Replace `DerivedKeys` with per-org random keys in the vault, add `kid` to envelope and JWT, rotation endpoint, asymmetric (Ed25519) envelope signatures so verifier does not hold signing keys | M-L |

### 9a. Remediation status (2026-10-22)

| # | Status | What changed (file refs) | Deviations from the sketch |
|---|---|---|---|
| 1 | Done, CI unexecuted | `.github/workflows/ci.yml`, `.github/dependabot.yml`, `requirements.lock`, `requirements-dev.lock`, `scripts/lock.py`, `Dockerfile` installs from the runtime lock, `[tool.bandit]` in `pyproject.toml`, major-version ceilings in `pyproject.toml` | `uv` is not installed here, so locks are plain pins generated from the installed env by `scripts/lock.py` (not hash-pinned, no cross-platform resolution). Lock check = `pip check` + `scripts/lock.py --check`. |
| 2 | Partly done | CI SBOM steps (`cyclonedx-py requirements`, `npm sbom`); `agents/common/provenance.py`, `llm/client.py` (`LLMResult.provider/model/digest`), `llm/providers.py` (`OllamaProvider.model_digest` via `/api/tags`), `api/service.py` sync -> `subagent_call.model_id/tokens/output.llm` | No `llm_call` audit event exists, and adding one to the hash-chained audit would change every replay chain and put a live-only digest into the hash path. Provenance is persisted in `subagent_call` only; digest is `stub` in replay and never enters replay keys. `model_id` column is 80 chars. |
| 6 | Done | see ASI02 row | Default (unset) means all current tools, omitted from the config dump so existing content hashes are unchanged; verified by loading all 3 dev-DB versions (on a copy) through `ConfigResolver`. Also errors if a REQUIRED tool is removed. |
| 8 | Partly done | `bus/bus.py::ai_generated`, `api/routers/projects.py::_msg_out`, `api/sse.py::translate`, `web/src/components/MessageCard.tsx` badge, `web/src/pages/Approvals.tsx` notice, `web/src/lib/aiDisclosure.ts` | Flag is derived at the view layer from the sender, NOT added to `Envelope`: changing the envelope would change signed/canonical bytes and break existing audit and signature verification. Raw evidence next to the summary in the approval drawer already existed; a review-time floor was not added. |

Scan results at this date: `pip-audit` on `requirements-dev.lock` found no known vulnerabilities; `bandit -r src` found 1 High (B613 bidi control characters in `api/security.py`; fixed by using `\u` escapes in the regex) and low findings for `assert` (B101) and seeded `random` (B311), now skipped by config because ruff S101/S311 police them; `npm audit` reported 2 moderate advisories in `react-router`/`react-router-dom` 6.x; fixed 2026-10-26 by upgrading to 7.18 (build, tests and e2e green). CI fails on `--audit-level=high` only, so it would pass today.

Not implemented (needs owner decisions): backlog 3, 4, 5, 9, 10 and quarantine; designs in `docs/security-roadmap.md`.

## 10. What is not yet the norm for this prototype

| Practice now expected for agent systems | State here |
|---|---|
| Per-agent, short-lived, scoped identity (CoSAI, NCCoE) | Absent: org-level HMAC keys held by the trusted process |
| Keyed or externally anchored audit | Unkeyed chain + in-DB anchor and triggers; no external anchor (accepted risk) |
| Behavioural monitoring, alerting, kill switch | Manual per-project stop only; no alert consumers |
| Multi-turn / adaptive red-team coverage | 18 single-shot deterministic cases; no adaptive or multi-turn attacker, no live-model run in suite (`test_live_ollama.py` exists, outside the adversarial set) |
| SBOM, lockfile, CI, dependency/vuln scanning | Added 2026-10-22 (locks, CI workflow, SBOM steps, Dependabot); unexecuted in CI |
| Tool/MCP allowlisting and per-tool authorization | Per-agent allowlist added 2026-10-22; parameter-level authorization still tool-code only |
| Model provenance in audit | Provider/model/digest/tokens recorded per tool call in `subagent_call` (not in the hash-chained audit, see 9a) |
| MFA for human approvers | Absent |
| AI-interaction disclosure (Art. 50) | Done in API/SSE view and buyer UI (2026-10-22), see 9a |
| Written threat model, incident playbook, retention policy | Partial (this file, adversarial report) |
| HSTS and prod header verification | Not found |

## 11. Items I could not verify

- Primary text of OWASP Agentic Top 10, LLM Top 10 (any 2026 revision), ASVS chapter numbering for logging, SAIF 2.0 risk list, EU Regulation 2026/1744 number and exact dates, MITRE ATLAS technique IDs other than the three cited.
- Whether Postgres has the audit append-only guarantees: triggers found are SQLite-specific (`data/models.py:456-483`).
- Test results: the suite was not executed in this review; "Covered" rests on reading code and test names/bodies only for the cited parts.
