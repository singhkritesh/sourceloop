# Roles and perspectives

Every login sees a project **from its own side only**. A buyer sees a sourcing story (the need, the candidate suppliers, offers, the award, the
contract, the human decisions); it never sees the agent workflow. How the agents behave, and the end-to-end workflow, is for **inspectors**:
SBN's super admin and evaluator everywhere, and an organization admin for its own organization's side.

## Roles

| role | who | scope |
|---|---|---|
| `BUYER` | a buyer organization's procurement user | creates and starts projects and makes EVERY buyer-side decision (flagged supplier, award, changed locked clause, bank details with payment release); business view of own projects |
| `COMPLIANCE`, `QUALITY`, `LEGAL`, `FINANCE` | legacy roles (kept in the enum for old data) | own no checkpoint and are not created by the seed or offered by the login page |
| `SUPPLIER` | a supplier organization's user | sees only the projects it was invited to, and only its own thread, in a neutral form |
| `ADMIN` (**organization admin**) | one per buyer or supplier organization | manages its OWN organization: people and roles, and own-side agent settings; may inspect its own side's agent workflow |
| `SUPER_ADMIN` (**SBN super admin**) | SBN platform operator | all access: every organization, Studio (platform configuration), settings (including the platform kill switch), the full workflow; resolves operational (`ADMIN`-targeted) approvals |
| `EVALUATOR` | SBN auditor | read-only access to everything (including the workflow); every write is `403 READ_ONLY_ROLE` |
| `DESIGNER` | SBN configuration author | edits Studio drafts; cannot publish, touch keys, or see projects |

`ADMIN` used to mean the single platform admin. Migration `0005` turned every existing `ADMIN` into `SUPER_ADMIN`, so nobody lost access;
`ADMIN` now means the organization admin and is scoped to one organization.

## What each login sees of a project

| | business view (`/overview`) | agent workflow (`/messages`, `/audit`, `/stream`) | agent activity (`/activity`) | run internals (mode, tokens, config pin) |
|---|---|---|---|---|
| buyer-side roles | yes: candidates, offers, award, contract, decisions, milestones | **403** | **403** | not returned |
| `SUPPLIER` | its own thread only (own offers + the buyer's replies, neutral outcome) | its own neutral thread (filtered in SQL, densely renumbered) | **403** | not returned |
| `ADMIN`, buyer org | same business view | the full thread of its own organization's projects | its own buyer agent's calls | yes |
| `ADMIN`, supplier org | its own supplier thread | the supplier-filtered thread (never the buyer's side) | its own supplier agent(s) only | no |
| `SUPER_ADMIN`, `EVALUATOR` | yes | everything | every agent | yes |

Business users get live updates by polling `/projects/{id}` and `/overview`; there is deliberately no event stream for them.

The UI mirrors this: one project page, rendered by role. Business users see the business view; inspectors get tabs
**Overview | Agents & workflow | Agent activity**. The server is the authority: the UI hides things, the API refuses them (see
`tests/test_authz_matrix.py`, which declares every role x route and is the spec).

Both business views also carry `negotiation_review`: the judge's verdict on each negotiation turn in plain language. The buyer sees verdicts on
both sides' conduct; a supplier sees only the verdicts on its own (actor `you`). Verdicts hold no prices or limits.

## Organization administration

Routes under `/org` (own organization; `?org_id=` for `SUPER_ADMIN`/`EVALUATOR`, where another organization's id is a **404** for an org admin):

* **People** `GET/POST /org/users`, `PATCH /org/users/{id}` (`role`, `disabled`), `POST /org/users/{id}/reset-password`.
  * Roles allowed per organization kind: buyer org `BUYER ADMIN`; supplier org `SUPPLIER ADMIN`.
  * A new person gets a **one-time password**, returned once in the response of the call that created it and never retrievable afterwards.
    A password reset revokes the refresh tokens (no new sessions); an access token already issued lives out its short lifetime. Disabling is immediate.
  * Guard rails: you cannot change or disable yourself; an organization always keeps one active admin; platform staff are managed by SBN.
  * Disabling ends live sessions at once (`locked_until` set to the end of time; `current_principal` refuses it) and deletes refresh tokens;
    a role change takes effect on the very next request.
* **Own-side agent settings** `GET/PUT /org/overrides`, `POST /org/overrides/rollback`: versioned, immutable overrides layered on the
  platform configuration (table `org_override`, active pointer `organization.override_version`).
  * Buyer org: agent `timeout_s`, lead `max_tool_calls`, `model_override` for `buyer_lead` and its five tool agents, and the body of its prompts.
    Supplier org: the `supplier_lead` tool-call limit (the only supplier-side setting that has an effect).
  * **Not expressible at all:** enabling/disabling an agent, `allowed_tools`, rules, clauses, checkpoints, workflow, providers, allowlist.
  * The layered result runs through the same validator and invariants as an SBN publish (`studio/org_override.py`), at save time and
    again every time a project is built; anything that no longer passes fails closed (`409 ORG_OVERRIDE_INVALID`).
  * **Pinned per project:** the exact versions are recorded in `project.brief.org_overrides`. A later edit never changes a running
    project, and a restart restores it with the versions it was built with (deterministic replay).
  * Organization-specific provider keys are **not** built: orgs choose among the providers SBN configured.
* **Audit:** every change appends to the organization's own hash chain (`org:<org_id>`) in the same transaction (no passwords in it).

SBN (`SUPER_ADMIN`): `GET /admin/orgs`, `POST /admin/orgs` (creates an organization and its first admin), `POST /admin/staff`
(`EVALUATOR`, `DESIGNER`, `SUPER_ADMIN`). `EVALUATOR` may read the organization list.

## Demo logins (from `python -m sourceloop.seed`, shown once)

`superadmin@` (SBN), `buyeradmin@` (Acme's organization admin), `supplier1admin@` / `supplier2admin@` (supplier organization admins),
plus `buyer@ compliance@ quality@ legal@ finance@ supplier1@ supplier2@ evaluator@ designer@`, all `@sourceloop.test`.

## Limits

* Organization-specific provider connections and keys, per-organization Studio configuration beyond the whitelisted fields, SSO/MFA for admins
  and approval workflows for admin actions are not built.
* A business user can still see decision comments written by colleagues in the same organization (they are that organization's own records).


## Approvals belong to the buyer user

`orchestrator/approvals.py KIND_ROLE` maps every human checkpoint to `BUYER`, except `SUPPLIER_ACCEPT`: the awarded supplier's own user confirms the final terms before signature (Studio `workflow.checkpoints.supplier_acceptance`, on by default in live installs). A supplier user sees and decides only the checkpoint that names its own supplier (anything else is a 404), and it shows that supplier's own price and lead time, nothing of the buyer's. A supplier whose organization has no user is not asked (audited as `checkpoint_skipped`, `supplier_has_no_user_account`); a refusal needs a reason and ends the run as NO_DEAL. `ADMIN` (organization) and `SUPER_ADMIN` (SBN) set rules, manage people and mediate; they cannot resolve an approval (403), and an invariant (`INV_HOLD_RELEASE_BUYER`) fails the publish if the map is ever pointed at an admin. The login page is a role picker (`DEMO_ROLE_LOGIN`): Buyer 1 and 2, Supplier 1 and 2, SBN admin and one organization admin per organization.
