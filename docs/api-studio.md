# Studio API (`/api/v1/studio`)

Cookie auth + `X-CSRF-Token` on mutations, error envelope `{"error":{"code","message","request_id","details"?}}`. `details` carries
a validation report (`{errors:[{path,code,message,invariant_id}],warnings:[...]}`) on 422s. EVALUATOR is read-only everywhere
(a non-GET is `403 READ_ONLY_ROLE`).

Roles: **read** = DESIGNER | SUPER_ADMIN | EVALUATOR; **draft write** = DESIGNER | SUPER_ADMIN; **publish / rollback / import / export / keys / provider test** = SUPER_ADMIN. (An organization `ADMIN` has no access to Studio: it layers own-side settings through `/org/overrides`, see `docs/05-roles-and-perspectives.md`.)

| Method + path | Roles | Result |
|---|---|---|
| `GET /schema` | read | `{schema: <StudioConfig JSON schema with widget hints>, invariants:[{id,description,reason}]}` |
| `GET /draft` | draft | `{config, base_version, updated_at}`; creates the caller's draft from the active version if none (one draft per user) |
| `PUT /draft` `{config}` | draft | `200 {config, base_version, updated_at, validation}`. Parseable-but-invalid drafts are saved (inline `validation`). **422 `CONFIG_UNPARSEABLE`** (+ `details`) for unknown/smuggled keys or type errors; nothing saved |
| `DELETE /draft` | draft | `{discarded:true}`; next `GET /draft` restarts from the active version |
| `POST /validate` `{config?}` | draft | `{errors, warnings}`; default = caller's draft (404 `NO_DRAFT` if none). Does not save |
| `POST /publish` `{note, base_version?}` | SUPER_ADMIN | `201 {version, content_hash, previous}`; **422** `VALIDATION_FAILED`/`INVARIANT_VIOLATION` with `details`; **409 `BASE_VERSION_STALE`** if the active version moved since the draft started (or `base_version` != active) |
| `GET /versions` | read | `[{version, content_hash, created_by, created_at, note, rollback_of, active}]`, newest first |
| `GET /versions/{v}` , `GET /active` | read | the row above plus `config` |
| `POST /rollback` `{to_version, note?}` | SUPER_ADMIN | `201 {version, content_hash, rollback_of}`; re-validated copy published as a NEW version |
| `GET /diff?a=&b=` | read | `{a, b, changes:[{path, kind: added\|removed\|changed, before, after}]}`; secret-like paths masked |
| `GET /providers` | read | `[{name, kind, base_url, is_local, has_key}]` (from the caller's draft, else the active version) |
| `PUT /providers/{name}/key` `{secret}` | SUPER_ADMIN | `{has_key:true}`. Write-only; Fernet-encrypted in `provider_key`; the provider must exist in the draft or active config |
| `DELETE /providers/{name}/key` | SUPER_ADMIN | `{has_key:false}` |
| `POST /providers/{name}/test` | SUPER_ADMIN | `{ok, models:[...], error?}` via `net/safe_http` (`is_local` private hosts allowed; Ollama `GET /api/tags`). `error` is a code only: `UNREACHABLE, AUTH_FAILED, KEY_MISSING, URL_*, BLOCKED_*, UPSTREAM_ERROR, BAD_RESPONSE` |
| `GET /export?version=` | SUPER_ADMIN | bundle `{schema_version, config, prompts[], scenarios[]}` (default active; `key_ref` nulled, never a key) |
| `POST /import` `{bundle}` | SUPER_ADMIN | like `PUT /draft`: replaces the caller's draft (validated). 413 > 2 MB; 422 unknown `schema_version` / unparseable |
| `GET /audit?limit=` | read | `{valid, first_bad_seq, events, items:[...newest first]}` for the hash-chained `studio` stream (anchored: tail deletion is detected) |

Error codes: `NO_DRAFT` 404, `VERSION_NOT_FOUND` 404, `NO_ACTIVE_CONFIG` 404, `PROVIDER_NOT_FOUND` 404, `BASE_VERSION_STALE` 409,
`VERSION_CONFLICT` 409, `BUNDLE_TOO_LARGE` 413, `CONFIG_UNPARSEABLE` / `VALIDATION_FAILED` / `INVARIANT_VIOLATION` /
`UNSUPPORTED_SCHEMA_VERSION` / `BUNDLE_INVALID` 422.

## Persistence and audit

* `config_version` rows are immutable once PUBLISHED (ORM guard + SQLite triggers); `content_hash` = sha256 of the canonical JSON of the
  config. The runtime loads a version only through `ConfigResolver`, which re-verifies the hash (`CONFIG_TAMPERED`) and the
  invariants. Drafts live in `studio_draft` (one per user); the runtime never reads them.
* Every mutation appends a `studio.*` event (`draft_saved, draft_discarded, published, rolled_back, exported, imported,
  provider_key_set, provider_key_deleted, provider_tested`) to the hash-chained stream `audit_event.project_id = 'studio'`, in the
  SAME transaction as the change (publish = `config_version` + `app_setting.active_config_version` + audit event + anchor).
  Events carry version, content hash and changed paths, never a secret.
* Migration `0002`: `studio_draft`, `audit_anchor` (+ forward-only triggers, backfilled for existing streams),
  `config_version.rollback_of`.
* First boot (app lifespan) and `python -m sourceloop.seed` publish `default_config()` as version 1 when no version exists.

## Pinning and runtime consumption (rule 15a)

`POST /projects` pins the ACTIVE version (`project.config_version_id`; `config_version` is returned on the project and in
`brief`). Everything below is read from that version when the project is built; a later publish never affects it.
`scenario` must exist in the pinned config's `scenarios` (422 `UNKNOWN_SCENARIO`), `need_index` defaults to the scenario's.
A tampered pinned version fails creation with 409 `CONFIG_TAMPERED`.

Wired: `workflow.longlist_size / shortlist_size / negotiate_top / min_countries / disabled_stages / standard_clauses /
news_escalate_severity / news_min_confidence / news_timeout_s / concurrency`; `workflow.negotiation.*`
(max_rounds, min_step_pct -> buyer mandate; concession, opening_discount, delay_penalty_pct -> buyer policy);
`workflow.checkpoints.requirement_confirmation` (the only skippable checkpoint; skipping is audited as `checkpoint_skipped`);
`clauses` (bus clause library); `prompts` (instruction bodies via `prompt_builder`); `rules` (add-only `CFG_*` stops);
`scenarios` (factors, margin, min rating, poisoned_news, `supplier_behaviours`: adversarial = even `shortlist_N` sends an offer below
its floor, odd inflates certificate claims, `awarded` redlines `liability`; aggressive = stubborn; unreliable = declines the RFQ);
`data_sources.synthetic_seed` (only through the scenario spec; the request `seed` wins), `news_enabled`, `weather_enabled`;
`agents.<tool>.enabled` and `.timeout_s` for the 10 subagent tools (disabling an optional one fails closed: no news => `REVIEW` +
escalation), `agents.buyer_lead.max_tool_calls`; `llm.roles` / `agents.*.model_override` / `llm.tokens_per_project` / providers in
**live** mode.

`llm_mode` stays `replay` by default. `live` (Settings) needs every provider the pinned config uses to be reachable (probe through
`safe_http`; Ollama also needs the configured models installed) else 409 `LLM_PROVIDER_UNREACHABLE` / `MODEL_NOT_FOUND`; `record`
is 409 `LLM_MODE_UNSUPPORTED`. In replay the stub provider is used regardless of `llm.roles`.

Also wired (all through the pinned version): `workflow.autonomy[NEED]` (L3 or higher = "act then report": the requirement is auto-confirmed and
audited as `checkpoint_skipped`/`autonomy_act_then_report`; the contracting ceiling L2 is an invariant); `workflow.checkpoints.batched_async`
(requirement confirmation no longer blocks discovery, `checkpoint_deferred` is audited, and the first mandatory checkpoint refuses to be raised
until it is resolved; a rejection stops the run); `agents.<buyer_lead|supplier_lead>.allowed_tools` (optional tool allowlist, unset = all current tools, omitted from the payload/content hash while unset; anything else is rejected `NOT_ALLOWED` and audited as `tool_denied`; `buyer_lead` must keep `compliance_screen` = `INV_COMPLIANCE_TOOL_ALLOWED`; warnings `ALLOWED_TOOL_UNKNOWN`, `ALLOWED_TOOLS_UNUSED`; error `ALLOWED_TOOL_REQUIRED`); `agents.supplier_lead.max_tool_calls` (per handled message, fails closed with LOOP_LIMIT);
`agents.<llm agent>.prompt_ref` (picks the `prompts` entry that supplies the instruction body; wrapper, preamble and schema suffix stay code-owned;
today `regional_news_risk` is the only LLM agent and only prompt owner); `data_sources.use_recorded_fallback` (off = news and weather are
unavailable, since no live feed exists, so the run fails closed with REVIEW + escalation).

Precedence: `agents.regional_news_risk.timeout_s` wins; `workflow.news_timeout_s` is deprecated and used only when the agent entry is absent
(validator warning `NEWS_TIMEOUT_SUPERSEDED` when they differ).

### Informational / no runtime effect (kept in the schema because published versions store them; each carries a validator warning or a schema help text)

* `workflow.autonomy` for stages other than NEED (all their checkpoints are mandatory; warning `AUTONOMY_NO_EFFECT`).
* `agents.<tool>.max_tool_calls` for subagent tools and `agents.<lead>.timeout_s` (warnings `MAX_TOOL_CALLS_UNUSED`, `TIMEOUT_UNUSED`);
  `agents.<tool>.model_override` for tools that make no LLM call (`OVERRIDE_UNUSED`).
* `data_sources.weather_source = open_meteo` is wired but conditional: it needs LIVE LLM mode and `api.open-meteo.com` in `data_sources.egress_allowlist`, else the weather tool falls back to the recorded table with confidence 0.4 (`WEATHER_HOST_NOT_ALLOWED` warns at validation). The default `recorded` is omitted from the config dump so older content hashes stay valid. `data_sources.profile_source = simulated` is wired: blank mandatory supplier fields are filled by the simulated profile source (omitted from the dump while `none`). `data_sources.egress_allowlist` is now consumed by the live connectors (see `docs/03-connectors.md`).
* `llm.replay_fallback_live`: replay projects use the stub provider and never go live (`REPLAY_FALLBACK_INFORMATIONAL`).
* `llm.mode` inside the config: the project's mode comes from Settings (`app_setting.llm_mode`) (`LLM_MODE_INFORMATIONAL`).
* Per-model `thinking`/`temperature` only matter in live mode. `scenarios.<id>.need_index` is the default only.
  `agents.<tool>.enabled=false` for REQUIRED agents is rejected by the validator.

Fields were deliberately NOT removed: published versions are immutable and hash-verified on load (`CONFIG_TAMPERED`), so removing a field from the
strict schema would make existing versions unloadable.
* Restart: replay-mode projects are rebuilt from their pinned version by deterministic replay and verified against the stored audit chain (see README); only what cannot be
  reproduced is marked `STOPPED` (outcome `FAILED`, reason `RESTART_LOST_RUNTIME`) and must be re-created. Either way the project keeps its pinned version row.
