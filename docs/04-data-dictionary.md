# Data dictionary — what data goes where

Every field the flow reads, its **tag** (the exact name in code and API), its **value** (type and allowed values), **where it is used**, where it
comes from **today**, where a **real** value would come from, and what happens when it is **blank**. Read with `docs/03-connectors.md`
(sources) and `docs/01-interaction-model.md` (stages and messages).

Rule that applies everywhere: **blank never means pass.** A blank mandatory value sends the supplier to human review (`REVIEW`), never through.

## 1. Supplier registry (one record per supplier)

Tag = `SupplierRec` field = `supplier` table column. "Today" is the generated synthetic registry (seed 42, 1,640 suppliers).

| tag | value | used for | blank means | real source (to map) |
|---|---|---|---|---|
| `id` | `S` + 5 digits, e.g. `S01253` | key everywhere; invites and messages address `supplier:<id>` | n/a (key) | SBN supplier / account id |
| `name`, `aliases` | text ≤200; list of alternate names | watchlist screening (fuzzy ≥88) and duplicate resolution | cannot screen: record unusable | SBN legal name + trade names |
| `country` | ISO-3166 alpha-2 (`CN VN MX DE PL TR IN US MY TH KR RU` in the synthetic set) | excluded-country rule, weather and news region, freight and tariff tables, duplicate blocking | cannot apply country rules | SBN registered address |
| `tax_id` | text ≤40 | duplicate resolution (same tax id = same entity) | resolution falls back to name only | SBN / tax registry |
| `categories` | list of category tags (section 4) | category match (`CATEGORY_MISMATCH` rejects) | supplier never matches a need | SBN commodity codes mapped to our tags |
| `certs` | list of `{type, issuer, expires_on}` | required-certificate check: absent = `CERT_MISSING`, past `expires_on` = `CERT_EXPIRED` | treated as no certificate | certification-body registries (IAF CertSearch and issuers) |
| `claimed_certs` | list of cert types the supplier says it has but the registry lacks | `UNVERIFIED_CLAIM` -> REVIEW | none | supplier-submitted documents |
| `capacity_units` | integer ≥0, units per period | **mandatory.** Must be ≥ the need's `volume_units` (`CAPACITY` rejects); also scoring | **REVIEW** `MISSING_DATA:capacity_units` | SBN supplier profile |
| `lead_time_weeks` | integer 0–104 | **mandatory.** Must be ≤ the need's `max_lead_time_weeks` (`LEAD_TIME` rejects); also scoring | **REVIEW** `MISSING_DATA:lead_time_weeks` | SBN supplier profile |
| `financial_risk` | float 0 (safe) – 1 (distressed) | **mandatory.** Must be ≤ the need's `max_financial_risk` (default 0.6, `FINANCIAL_RISK` rejects); scoring weight 0.20 | **REVIEW** `MISSING_DATA:financial_risk` | credit-risk provider; provider scores must be normalised to 0..1 |
| `years_in_business` | integer 0–300 | **mandatory.** Must be ≥ the need's `min_years_in_business` (default 3, `YEARS_IN_BUSINESS` rejects) | **REVIEW** `MISSING_DATA:years_in_business` | SBN profile or company registry |
| `forced_labor_flag` | boolean | `FORCED_LABOR_RISK` -> **BLOCKED** | treated as false | screening partner |
| `subtier_origin` | list of ISO codes | high-risk origin (e.g. `XJ`) -> **BLOCKED** | treated as none | supply-chain disclosure |
| `network_status` | `member` (1.0), `invited` (0.6), `external` (0.3) | scoring weight 0.10 | defaults to 0.3 | SBN membership |
| `on_time_pct`, `quality_score` | float 0–1 (SBN transaction history) | scoring weights 0.20 and 0.25 | neutral 0.5, noted in the reasons ("no delivery history") | SBN transaction history |
| `unit_cost` | Decimal | **never in the registry.** Supplier-private; it lives only in the supplier mandate | always blank on the buyer side, by design | n/a |

The four **mandatory** fields are `capacity_units`, `lead_time_weeks`, `financial_risk`, `years_in_business`. The synthetic generator blanks each
with 6% probability, so about one supplier in five is blank somewhere. That is the source of the "Missing data" flags.

**Filling the blanks.** Studio `data_sources.profile_source`:
* `none` (default): records are used as they are; blanks go to REVIEW.
* `simulated`: a stand-in for SBN profile + credit provider fills **only** the four mandatory fields, **only** where blank, from the complete synthetic
  registry (`connectors/simulated.py`). It never overwrites a present value and never returns `unit_cost`. If it cannot answer for a supplier, the
  blank stays and the supplier is still reviewed. Replace with `SbnSupplierProfile` + `FinancialRiskProvider` (same `SupplierProfileSource` contract).
  Note: the Suppliers browse screen reads the database table, which still shows the original blanks.

## 2. The need (what the buyer asks for)

Tag = `Need` / `NeedSpec` field. API create accepts these optional structured fields (invalid values return 422 `INVALID_NEED`); `need_text` is display-only.

| tag | value | used for |
|---|---|---|
| `category` | one of the category tags | pool of candidate suppliers |
| `industry` | text, e.g. `high_tech` | brief |
| `volume_units` | integer >0 | capacity check |
| `required_certs` | list of cert tags | certificate check and the document request |
| `excluded_countries` | list of ISO codes | `EXCLUDED_COUNTRY` rejects; also in the buyer mandate, so the policy blocks an invite to such a country |
| `max_lead_time_weeks` | integer 1–104 | lead-time check; buyer mandate limit |
| `max_financial_risk` | float, default 0.6 | financial check |
| `min_years_in_business` | integer, default 3 | years check |
| `target_unit_price` (API: `target_price`) | Decimal | negotiation target; must not exceed the buyer ceiling |

Index price per category (`index_price`: `material`, `month` `YYYY-MM`, `value`) sets the buyer's price ceiling (index x max factor) and the +/-15% price band.
No index = the price band **fails closed** (`PRICE_BAND_NO_INDEX`).

## 3. Mandates (private limits; never cross the side boundary)

Only a predicate and a rule id ever leave the policy engine. Set by the orchestrator from the pinned Studio version plus the need.

| side | tag | value |
|---|---|---|
| buyer | `max_unit_price`, `target_unit_price` | Decimal; ceiling and target |
| buyer | `max_lead_time_weeks`, `max_rounds` (1–8), `min_step_pct` (0–20) | limits for the negotiation |
| buyer | `excluded_countries`, `locked_clauses` | countries never invited; always includes `liability`, `termination`, `ip` |
| buyer | `award_value_cap`, `disclose_target`, `price_band_pct` | optional cap; whether the target may be shared; band (default 15) |
| supplier | `unit_cost` + `min_margin_pct` -> price floor | Decimal; never visible to the buyer |
| supplier | `max_capacity_units`, `lead_time_weeks` | what it can deliver |
| supplier | `provable_certificates` | claiming anything else is `CLAIM_NOT_PROVABLE` |
| supplier | `unchangeable_clauses`, `min_buyer_rating`, `max_rounds`, `min_step_pct` | clause and counterparty limits |

## 4. Tags (the allowed values)

| set | values |
|---|---|
| categories | `pcb_assembly`, `connectors`, `metal_castings`, `precision_machining` |
| certificates | `ISO9001`, `IATF16949`, `ISO14001`, `RoHS`, `UL` |
| stages | `NEED`, `DISCOVERY`, `QUALIFICATION`, `SOURCING`, `CONTRACTING`, `ONBOARDING`, `DONE` |
| qualification status | `QUALIFIED`, `REVIEW`, `REJECTED`, `BLOCKED` (set only by `compliance.rules`, never by an LLM) |
| message types | `BRIEF INVITE INTEREST_REPLY DOC_REQUEST DOC_SUBMISSION QUAL_RESULT RFQ OFFER COUNTER ACCEPT REJECT CONTRACT_DRAFT REDLINE SIGN_REQUEST ONBOARD_DATA ESCALATE` |
| approval kinds -> role | `REQUIREMENT` buyer, `COMPLIANCE_FLAG` compliance, `TECHNICAL_QUAL` quality, `AWARD` buyer, `CLAUSE` legal, `SIGNATURE` legal, `BANK_DETAILS` finance, `HOLD_RELEASE` finance; also `MANDATE_BREACH LOW_CONFIDENCE LLM_UNAVAILABLE STALLED BUDGET RULE` |
| project status | `DRAFT RUNNING ESCALATED STOPPED DONE FAILED_RECOVERABLE STALLED BUDGET_EXCEEDED` |
| outcome | `DEAL`, `NO_DEAL`, `NO_VIABLE_SUPPLIER`, `STOPPED_BY_USER`, `FAILED` (suppliers see only `DEAL` or `CLOSED`) |
| clause keys | negotiable: `payment_terms delivery warranty confidentiality quality_requirements`; **locked** (human only): `liability termination ip` |
| news signal kinds | `strike unrest port_closure sanction regulation weather none`, each with severity 0–1 |
| roles | `BUYER SUPPLIER COMPLIANCE QUALITY LEGAL FINANCE EVALUATOR DESIGNER ADMIN` |

## 5. Lists and external data

| tag | value | used for |
|---|---|---|
| `watchlist_entry` | `name`, `aliases[]`, `country` (ISO or `XX`), `list_name` (`SYNTH-SDN`, `OFAC_SDN`) | screening: fuzzy name match ≥88 across **all** lists -> `WATCHLIST_HIT` -> **BLOCKED**, never auto-cleared |
| OFAC sync | `python -m sourceloop.connectors sync-ofac` | loads `OFAC_SDN` rows; **every new project merges the database lists into its screening** (lists the synthetic registry already carries are skipped) |
| news | recorded articles per country; LLM extracts `NewsSignal`s | regional risk; only LLM-read external text, wrapped as untrusted |
| weather | seasonal table, optionally the live forecast | delay probability for landed cost |
| freight / tariff | `FREIGHT_PCT`, `TARIFF_PCT` per country (code tables) | landed cost = price + freight + duty + risk |

## 6. Configuration tags (where settings live)

| where | tag | value |
|---|---|---|
| `.env` (bootstrap, secrets) | `APP_ENV` | `dev` or `prod` (prod needs the secrets below and https CORS) |
| | `JWT_SECRET`, `ORG_SIGNING_SECRET` | ≥32 bytes; install.sh generates them |
| | `CONFIG_ENCRYPTION_KEY` (alias `SOURCELOOP_VAULT_KEY`) | Fernet key for stored provider keys |
| | `DATABASE_URL`, `CORS_ORIGINS`, `OLLAMA_BASE_URL`, `LLM_MODE` | connection and fallback settings |
| | `RATE_LIMIT_*`, `MAX_ACTIVE_PROJECTS_PER_ORG`, `TRUSTED_PROXY_HOPS`, `REGISTRATION_ENABLED` | limits and proxy trust |
| Studio `data_sources` (pinned per project) | `synthetic_seed` | int; the registry and needs are generated from it |
| | `news_enabled`, `weather_enabled`, `use_recorded_fallback` | feed switches |
| | `weather_source` | `recorded` (default) or `open_meteo` (live mode and an allowlisted host) |
| | `profile_source` | `none` (default) or `simulated` (fills blank mandatory fields) |
| | `egress_allowlist` | hosts live connectors may reach (admin only) |
| Settings screen (admin, in the database) | LLM mode (`replay` `live` `record`), token budget | override `.env` |

## 7. What enters at each stage

| stage | data in | data produced |
|---|---|---|
| NEED | the need fields (section 2), buyer mandate | `BRIEF`; REQUIREMENT approval |
| DISCOVERY | registry (section 1), category | resolved, scored candidate list (country-diverse) |
| QUALIFICATION | certs, mandatory fields, flags, watchlists, news and weather | `QUAL_RESULT` per supplier; COMPLIANCE_FLAG and TECHNICAL_QUAL approvals |
| SOURCING | index price, supplier and buyer mandates | `RFQ`, `OFFER`/`COUNTER` rounds (policy-checked), AWARD approval |
| CONTRACTING | clause library, deviations | `CONTRACT_DRAFT`, `REDLINE`; CLAUSE and SIGNATURE approvals |
| ONBOARDING | supplier onboarding fields (bank details are provided by the supplier, confirmed by Finance) | `ONBOARD_DATA`; BANK_DETAILS approval; ledger hold; HOLD_RELEASE |

## 8. Gaps

* Real-system field names (SBN, Ariba, the credit provider) are **not mapped** because no API specification or credentials are available here; the
  "real source" column names the system, not its field.
* `years_in_business` and `lead_time_weeks` have no free public source identified.
* The Suppliers browse screen shows registry blanks even when `profile_source=simulated`.
* Certificates in the registry are single `expires_on` dates; there is no renewal or revocation feed.
