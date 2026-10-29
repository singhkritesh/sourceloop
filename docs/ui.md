# SourceLoop web UI (`web/`)

React 18 + Vite + TypeScript (strict), TanStack Query, Zustand, react-router. Hand-written CSS with design tokens
(`src/styles/tokens.css`, light/dark via CSS variables, `prefers-color-scheme` plus a manual toggle).

## Run

```bash
cd web
npm ci
npm run dev            # http://127.0.0.1:3010 (strictPort); /api is proxied to http://127.0.0.1:8005 (override: VITE_API_TARGET)
VITE_MOCK_STUDIO=1 npm run dev   # dev only: serves /api/v1/studio/* from in-memory fixtures (auth etc. still hit the backend)
npm run build          # tsc -b (strict) + vite build -> web/dist
npm run lint           # eslint (bans dangerouslySetInnerHTML and EventSource)
npm test               # vitest (39 tests): SSE parser/reconnect (+no reconnect on 401/403), single-flight refresh, schema->form, ladder/feed/reducer, error mapping, approval edit shapes
SMOKE_USERS='{"buyer":["<email>","<pw>"],"admin":[...]}' node scripts/smoke.mjs   # studio: SMOKE_ADMIN='["<email>","<pw>"]' node scripts/smoke-studio.mjs
#   # headless Chrome (puppeteer-core) vs the live stack
# full real-stack journey (fresh DB: scripts/e2e-fresh.sh; dev server on 3010; creds file from the seed output, gitignored):
#   cd web && E2E_CREDS=../data/e2e/creds.json node scripts/e2e.mjs B C D E F   (phases: B approvals+scenarios, C Studio, D live LLM, E security, F restart; evidence in data/e2e/)
#   .venv/bin/python scripts/e2e-determinism.py   # API-level determinism probe (decision sequence identical; head hashes differ by design: random ids + wall-clock ts are hashed)
```

New port: **3010** (web dev / preview). Update the global port registry.

## Screen map

| Route | Roles | What |
|---|---|---|
| `/login` | all | Sign in; 401 ("Email or password is incorrect, or the account is temporarily locked." - wrong password and lockout are indistinguishable by design) / 422 / 413 / 5xx / network messages; generic 429 (rate limiter) shows a Retry-After countdown and locks the button. No register link (registration is disabled) |
| `/projects` | all but DESIGNER | List + "New sourcing project" (scenario picker: happy, no_deal, below_floor, locked_clause, poisoned_news, inflated_claims; need_index; seed) |
| `/projects/:id` | project readers | **One page, rendered by role.** Business users (buyer-side roles, suppliers) get the business view only: need, candidate suppliers and how they screened, price chart, award and contract, decisions, progress. Inspectors (SUPER_ADMIN, EVALUATOR, org ADMIN for its own side) also get tabs **Agents & workflow** (below) and **Agent activity**. Agents & workflow: stage stepper, split view (Buyer lane / orchestrator lane / Supplier lane), subagent chips (confidence, sources, latency), policy-block and escalation callouts (rule id only), negotiation ladder (inline SVG + table view), audit timeline with chain-verified badge (EVALUATOR/ADMIN only). SUPPLIER gets the server-filtered view and no audit panel |
| `/approvals` | buyer side, EVALUATOR (read-only), ADMIN | Role-scoped inbox + detail drawer. Keys: `j`/`k`/Enter in the list; `a` approve, `r` reject, `e` edit, Esc close. Reject/Edit need a comment of at least 10 characters. Edit is offered only for COMPLIANCE_FLAG (`{include:[ids]}` from evidence `flagged`), TECHNICAL_QUAL (`{fail:[ids]}` from `candidates`), AWARD (`{supplier:id}` from `ranking`) and CLAUSE (`{accept_change:bool}`); other kinds have no Edit. Kind-specific controls (checkboxes / radios / switch), no free JSON; a 422 `INVALID_EDIT` is shown inline per field and the approval stays pending. 409 (version conflict) reloads the item and tells the user; 409 `RUNTIME_UNAVAILABLE` shows a banner and disables Approve/Reject/Edit |
| `/studio/:section` | DESIGNER, SUPER_ADMIN; EVALUATOR read-only | Schema-driven form (sections from the JSON schema root), debounced autosave (1 s) and validation (0.6 s), invariants side panel with lock icons, Providers (write-only keys, test connection, model discovery feeds the model picker), Versions (diff, rollback: ADMIN), Publish dialog (ADMIN), Export / Import |
| `/organization` | ADMIN (own org), SUPER_ADMIN (all orgs: list, create, open), EVALUATOR read-only | Team (add person with one-time password, change role, disable/enable, reset password) and Agent settings (own-side overrides with version history and rollback) |
| `/settings` | SUPER_ADMIN edit, EVALUATOR read | `GET/PUT /settings`; secrets always `****` |

## How things work

- **Auth**: cookies only (HttpOnly access/refresh). The `csrf` cookie value is echoed in `X-CSRF-Token` on mutations.
  Concurrent 401s share ONE refresh promise (`lib/singleFlight.ts`, used in `api/http.ts`); a failed refresh clears the session.
- **SSE**: `lib/sse.ts` parses `fetch` streams (never `EventSource`), resumes with `Last-Event-ID`, reconnects with
  1 s -> 10 s backoff, treats 45 s of silence as dead (server heartbeat is 15 s) and stops on terminal runs.
  `lib/runReducer.ts` is a pure, idempotent reducer (dedupe by seq/id); `state/run.ts` is the Zustand store.
  SSE `message` events carry summaries only, so full payloads come from `GET /projects/{id}/messages`, refetched on each message event.
- **Studio form**: `lib/schemaForm.ts` maps JSON schema nodes (`$ref`, `anyOf` nullable, Decimal `number|string`, `patternProperties`,
  `widget` hints) to field specs; `studio/SchemaForm.tsx` renders them. Unknown widgets fall back to a type default; unknown shapes fall back to a JSON editor.
  Decimal values stay strings (no float rounding). Invariant lock icons come from `studio/formCtx.ts` (static path hints) and server issues carrying `invariant_id`.
- **Security**: all agent/news/evidence text is rendered as React text nodes; no tokens in storage (only the theme choice is in
  `localStorage`); provider keys live only in a password input that is cleared after submit and are never read back.
  UI role gating (`state/auth.ts: can`) is cosmetic; the server enforces everything.

## Errors and lifecycle

- `api/http.ts: CODE_MESSAGES` maps `PAYLOAD_TOO_LARGE` (and any 413), `QUOTA_EXCEEDED`, `RUNTIME_UNAVAILABLE`, `INVALID_EDIT`, `UNAUTHENTICATED` to plain messages (`errorMessage`, `lib/errors.ts: friendlyMessage`); unknown codes show the server message.
- Workspace: `RUNTIME_UNAVAILABLE` on start/resume (or a project the API reports as `STOPPED` / outcome `FAILED` with reason `RESTART_LOST_RUNTIME`, rendered as "lost on restart"; only projects that could not be rebuilt by replay end up here) shows a banner and hides Resume. Unknown statuses render safely as lower-cased text.
- `/projects/{id}/escalations` is role-filtered by the server; the "Waiting on a human" callout handles an empty or short list.
- SSE: on 401 one refresh is attempted per failure streak; a second 401 or any 403 stops reconnecting, clears the session and routes to `/login`.

## Known gaps

- Config fields that have no runtime effect carry `informational: true` / `help` in the schema widget hints and a validator warning (see docs/api-studio.md); the generic form renders `help` text under the field; there is no dedicated styling for the `informational` flag.
- No unit test for the Login 429 countdown (checked by reading).
- Exact field-error envelope of `INVALID_EDIT` is not documented; `lib/errors.ts: fieldErrors` accepts `error.fields|details|errors` as object or array and falls back to the message.
- Studio has no dedicated widgets yet for stage graph / rule what-if / prompt preview+tests (generic form only).
- Messages are fetched in full on each message event (fine for hundreds, not thousands).
- Diff view falls back to a client-side diff when the server returns none.

## Audit export and reviewer override rates

* `GET /api/v1/projects/{id}/audit/export` (EVALUATOR / ADMIN) streams the project's audit chain as NDJSON; there is no UI button, use the API.
* `GET /api/v1/reviewers/override-rates` (ADMIN / EVALUATOR) feeds the "Reviewer override rates" table on the Settings page; reviewers flagged
  `POSSIBLE_RUBBER_STAMP` are listed there.
* The approvals inbox shows an OVERDUE badge for pending items past their SLA (`due_at` / `overdue` in the list response).
