# Security roadmap

## Status (test-bed decision, 2026-10-26)

SourceLoop is a proof of concept on synthetic data, so the controls below were built in a **simplified, test-bed form** and the heavy ones were
deliberately left out. Authentication stays simple (password login, short-lived cookie sessions); nothing here adds a second factor.

| item | status | what was built |
|---|---|---|
| A. per-run agent identity | **built (simple)** | `MessageBus.enable_identity/mint`: every `AgentChannel` holds a capability `agent.scopes.hmac` minted for THIS run (random in-memory key, never persisted). `_submit` refuses a message without a valid capability for its sender (`IDENTITY_INVALID`) or outside its scopes (`SCOPE_DENIED`). Scopes come from the existing sender matrix, so the token can only narrow it. No expiry (the key dies with the run); after a restart the run is rebuilt and re-minted. |
| B. external audit anchor | **built (file sink)** | `audit/anchor_sink.FileAnchorSink`: at the end of every run segment `(project, seq, head hash)` is appended to `AUDIT_ANCHOR_PATH` (a JSONL file, each line commits to the previous one). `GET /projects/{id}/audit/verify` returns `external_anchor` and is `valid:false` when the chain no longer holds a published `seq` with the same hash, or the file was edited. Production would swap in object storage with a retention lock or RFC 3161; the interface is the same. |
| C. monitoring / quarantine / kill switch | **built (minimal)** | A sender with 10 rejected messages in one run is quarantined (`participant_quarantined` audited, then every message from it is refused with `QUARANTINED`; per project, never global). `PUT /settings {"kill_switch": true}` (SUPER_ADMIN, migration 0007) makes every start / resume answer `503 KILL_SWITCH`. No webhook alerting. |
| D. approver MFA / step-up | **not built, by decision** | Out of scope for a test bed. |
| E. key management / rotation | **not built, by decision** | Keys stay derived from one secret; fine for synthetic data. |

The original designs are kept below for when this leaves the test bed.

## Original designs (items that need owner decisions)

Companion to `docs/security-mapping.md` section 9 (items 3, 4, 5, 9, 10). None of these is implemented. Each section gives a concrete design, the
tradeoffs, and the questions only the owner can answer. Rules referenced are CLAUDE.md section 7 (changes to the stage/message model, mandate shape or
trust boundary need clarification first, and `docs/` must be updated in the same change).

## A. Per-run agent identity (backlog 3)

Goal: an agent message proves "agent X of project P, allowed to send types T, until time E", not just "someone holding the org key".

Design:
- At run start the orchestrator mints one capability per agent: `{agent_id, project_id, run_id, scopes: ["bus.send:OFFER", ...], iat, exp, kid}`,
  canonical JSON (`audit.canonical.dumps`), HMAC-signed with a key derived from the org key and `kid` (so the root secret never signs messages directly).
- `AgentChannel` (agents side) holds the capability opaque string and attaches it to each send; it cannot mint or widen it (agents may not import
  `policy`/`orchestrator`, so verification stays in `bus/bus.py::_submit`, next to the signature check).
- `_submit` verifies: signature, `project_id` equals the bus project, `exp` against the injected `Clock`, scope contains the message type, `agent_id`
  equals `from_agent`. Failure maps to a `policy_block` with `rule_id` `IDENTITY_INVALID|IDENTITY_EXPIRED|SCOPE_DENIED` and audit before any delivery.
- Scopes come from the stage matrix already in the bus (type-for-stage, sender kind), so there is no second source of truth; the token only narrows.
- Determinism: `iat/exp` come from the `Clock`; the token is not part of the signed envelope body or audit hash, only its `kid`+`agent_id` go into the
  audit evidence, so replay hashes stay stable. Tests: expired, wrong scope, cross-run reuse, cross-agent reuse, tampered scope.

Tradeoffs: today the verifier and the agents run in one process, so this is defence in depth against a bug or a confused-deputy path, not against a
compromised process. It only pays off fully once agents run out of process (then pair with item E, asymmetric keys). Cost is moderate (bus + channel +
tests); risk is mostly in resume after restart (capabilities must be re-minted, because runtime state is in memory).

Open questions: lifetime (per run vs per stage)? Should a supplier agent get a fresh token per RFQ round? Do we need revocation (kill switch, item C)
or is expiry enough? Is `agent_id` the `supplier:<id>` string or a new stable identifier?

## B. External audit anchor (backlog 4)

Goal: detect a full-chain rewrite by someone with database write access (the in-DB `audit_anchor` and triggers do not stop that).

Design:
- A periodic job (and on `run_finished`) computes `(project_id, seq, head_hash)` and writes it to a sink outside the application's write path.
  Options: S3 with Object Lock (compliance mode); an RFC 3161 timestamp authority response stored with the anchor; or a transparency-log style append-only
  file in a separate account. Store the returned receipt id in an `audit_external_anchor` table (insert-only, new Alembic revision).
- `GET /projects/{id}/audit/verify` additionally fetches/reads the latest external anchor and checks that the chain contains `seq` with the same hash.
- The main chain stays unkeyed so replay determinism is untouched; only anchors are signed/stamped.
- Egress goes through `net/safe_http` with the sink host on the allowlist (never a supplier-supplied URL).

Tradeoffs: Object Lock is simple but ties the project to a cloud account; RFC 3161 is vendor-neutral but adds a third-party dependency and needs
hash-only payloads. Anchoring only at run end leaves a window; anchoring per N events costs more writes. A keyed chain (HMAC) was rejected: it breaks
replay-hash comparisons and moves trust to whoever holds the key.

Open questions: which sink is acceptable (cloud account availability for the prototype)? Anchor cadence? Is a self-hosted append-only service
acceptable instead? Retention period for anchors and chains (EU AI Act Art. 12 style retention policy is currently undocumented)?

## C. Behavioural monitoring, quarantine and kill switch (backlog 5)

Goal: react to repeated policy blocks, injection flags or price probing by a counterparty agent, not only when a human notices.

Design:
- Counters derived from the audit stream (no new source of truth): per `(project, participant)` counts of `policy_block`, `message_rejected`,
  `tool_denied` (new, from the allowlist work), `injection.suspected`, and a probe metric (offers differing by less than `min_step_pct` repeated N times).
- Thresholds live in the pinned Studio config under a new `monitoring` section (a Studio field, never an invariant bypass), with an invariant floor so
  they cannot be set to "never" (cap on tolerated blocks).
- On breach the orchestrator, in this order: appends `participant_quarantined` to the audit log, sets the participant state `QUARANTINED` in the bus
  (every `_submit` from it returns `QUARANTINED`, nothing delivered), sets the project `ESCALATED` with an approval for COMPLIANCE.
- `POST /admin/agents/{id}/quarantine` and `.../release` (ADMIN, version-locked, comment required, audited). A global kill switch is an `app_setting`
  that makes `RunExecutor` refuse to start or continue runs (read at claim time; short transaction, rule 12).
- Alerting: a small in-process notifier interface (log + optional webhook through `safe_http`) so CI/ops can consume `policy.block` and
  `audit.verify valid=false` which today nothing reads.

Tradeoffs: thresholds that are too low let an adversarial supplier deny service to a buyer by triggering the buyer's own escalations (quarantine is a
lever for attackers); too high and it is decorative. Per-project quarantine is safer than per-agent-global. False positives cost human review time.

Open questions: scope of quarantine (project, supplier org, all projects)? Who may release (ADMIN only, or COMPLIANCE)? Is a webhook sink needed or is
the dashboard enough? Should a quarantined supplier be told why (leaks detection thresholds) or just see a generic rejection?

## D. Approver hardening / MFA (backlog 9)

Goal: a stolen session cannot resolve FINANCE/LEGAL/COMPLIANCE approvals.

Design:
- TOTP (RFC 6238) enrolment for roles that resolve mandatory checkpoints; secret stored encrypted with the existing Fernet vault key (never returned).
  New table `user_mfa(user_id, secret_enc, confirmed_at)` via Alembic; recovery codes hashed with argon2.
- Step-up on `POST /approvals/{id}/resolve` for mandatory kinds: body carries a fresh `mfa_code`; server also records `mfa_verified_at` in the
  approval audit evidence. Cookie session stays as is; step-up is per action (or valid for a short window, e.g. 5 min, kept server-side).
- Separation of duties: reject when `approver == project initiator` for award/signature/hold release (configurable via a Studio rule with a
  non-disableable minimum for hold release); optional second approver above a value threshold.
- Minimum dwell time: reject resolve if the approval was opened less than N seconds ago (needs a server-side "opened" timestamp; the existing
  rubber-stamp detector in `orchestrator/approvals.py` only reports).

Tradeoffs: TOTP is phishable; WebAuthn/passkeys are stronger but need a relying-party config and a browser flow. Dwell time annoys power users and
can be gamed by waiting. A second approver needs enough users per role in the demo data (seed currently creates one per role).

Open questions: TOTP now or WebAuthn directly? Which actions require step-up (all mandatory kinds, or only hold release and bank details)? Value
threshold for a second approver, and who is it? Is a break-glass path needed when MFA is lost?

## E. Key management upgrade (backlog 10)

Goal: no single root secret derives every org key; keys can rotate; the verifier cannot forge.

Design:
- Replace `DerivedKeys` with per-org random keys generated at org creation and stored through `studio.vault` (Fernet today; interface allows KMS later).
  Envelopes and JWTs carry `kid`; the keyring keeps old keys in `verify-only` state for a grace period.
- Rotation endpoint (ADMIN): creates key `kid+1`, marks `kid` verify-only, audits `key.rotated`. Resume of old runs verifies with the old `kid`.
- Phase 2: Ed25519 envelope signatures (`cryptography` is already a dependency). Agents sign with a per-org private key held by the signer service;
  the bus holds only public keys, so a bus compromise cannot forge agent messages. JWT moves from HS256 to EdDSA or stays HS256 with `kid` rotation
  (decision below).

Tradeoffs and compatibility: the signature is part of stored `message` rows and the signing body is canonical JSON, so a new `kid` field changes
bytes for new messages only; old messages must keep verifying with their old algorithm (store `alg`/`kid` per message, add columns by Alembic revision,
never edit applied revisions). Replay determinism holds only if keys in tests are fixed (a `FixedKeyRing`). Ed25519 signing at message rate is cheap.

Open questions: KMS target (AWS KMS, Vault, or file-based for the prototype)? Is the migration of historical rows required, or can the prototype cut
over with a new database? Who holds the signer private keys when agents are in-process? Acceptable to drop HS256 for sessions?

## Suggested order

1. B (anchor) or C (monitoring): highest evidence value for the audit story, independent of the others.
2. D (step-up for hold release and bank details only): small, directly addresses ASI09/A07.
3. E phase 1 (kid + rotation), then A (per-run identity), then E phase 2 only if agents leave the process.

## Test-bed role picker

`DEMO_ROLE_LOGIN` (default true outside prod, forced off in prod) lets the login page sign in as a seeded `@sourceloop.test` account without a password (`GET /auth/demo-roles`, `POST /auth/demo-login`; 404 for any other email; each use logs `auth.demo_login`). It exists for the proof of concept only: set `DEMO_ROLE_LOGIN=false` (or `APP_ENV=prod`) to require passwords again.
