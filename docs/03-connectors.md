# Connector map — what data the prototype needs and where it can come from

SourceLoop runs on **synthetic data by default**. The "Missing data: lead time, financial risk…" flags you see are **deliberate**:
`data/synthetic.py` blanks about 6% of each mandatory field so the fail-closed path is exercised (roughly one supplier in five has at
least one blank). They are not a connector bug. This document maps each data need to a real source, says what is built, and lists what
is still missing. The table below is generated from `src/sourceloop/connectors/catalog.py` (`python -m sourceloop.connectors list`).

| id | need | provider | status | fills | egress hosts | credentials | notes |
|---|---|---|---|---|---|---|---|
| `ofac_sdn` | Sanctions | US Treasury OFAC (SDN list) | **LIVE** | sanctioned entity names, aliases, country | www.treasury.gov, sanctionslistservice.ofac.treas.gov, wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com | no | Downloads the public SDN entity list and loads it into the watchlist (entities only, with aliases and addresses). Public, no key. Run `python -m sourceloop.connectors sync-ofac`. Individuals, vessels and aircraft are not loaded. Screening matches names across the whole list (country is metadata). |
| `open_meteo` | Weather | Open-Meteo forecast API | **LIVE** | forecast precipitation, forecast wind | api.open-meteo.com | no | 16-day severe-weather signal (heavy rain, high wind) per origin country, blended into the delay probability. Public, no key (non-commercial terms; check licensing before production). Live LLM mode + Studio data_sources.weather_source=open_meteo. |
| `synthetic_registry` | Supplier Profile | Built-in generated registry | **SYNTHETIC** | name, country, certificates, lead time, capacity, years in business, financial risk | - | no | The 1,640-supplier synthetic universe, with deliberately blanked fields to exercise fail-closed handling.  |
| `simulated_profile` | Supplier Profile | Simulated SBN profile + credit-risk (answers from the complete synthetic registry) | **SYNTHETIC** | lead_time_weeks, capacity_units, years_in_business, financial_risk | - | no | Fills blank lead time, capacity, years in business and financial risk so a run can complete without SAP access. Enabled by Studio data_sources.profile_source=simulated. Stand-in for the two STUB connectors below; same contract. Never returns unit_cost. |
| `synthetic_news` | News | Recorded news corpus | **SYNTHETIC** | regional news risk | - | no | Recorded/synthetic regional news; the only LLM-read external text in the system.  |
| `sbn_supplier_profile` | Supplier Profile | SAP Business Network supplier profile / Ariba Supplier Management | **STUB** | lead_time_weeks, capacity_units, years_in_business | - | yes | Would fill blank lead time, capacity and years in business from the supplier's own network profile. Needs a tenant, API credentials and SAP's API contract; not available in this prototype. |
| `financial_risk` | Financial Risk | Commercial credit-risk provider (e.g. Dun & Bradstreet, Creditsafe) | **STUB** | financial_risk | - | yes | Would fill the blank financial-risk score. A paid commercial licence is required. No free equivalent. Provider choice and licence are a business decision. |
| `certificate_registry` | Certificates | Certification-body registries (IAF CertSearch and issuers) | **STUB** | certificate validity, expiry, issuer | - | no | Would verify claimed certificates against the issuer instead of trusting the supplier's document. Registry access and terms differ per issuer; unverified. |
| `icertis_clauses` | Contract Clauses | Icertis contract platform | **STUB** | clause library, fallbacks | - | yes | Would supply the approved clause library and fallback positions instead of the built-in library.  |

Status meaning: **LIVE** = implemented and reaches a real public source; **STUB** = contract defined, fails closed with
`NOT_CONFIGURED` until wired; **SYNTHETIC** = the built-in generated or recorded data. Terms of use, rate limits and licensing of the
external sources are **not verified** here; check them before any use beyond a prototype.

## What is real today

**Sanctions (OFAC SDN, LIVE).** `python -m sourceloop.connectors sync-ofac [--dry-run]` downloads the public SDN, ALT (aliases) and
ADD (addresses) files and loads **entities** into `watchlist_entry` with `list_name = OFAC_SDN` (about 10,800 rows: one per entity per
address country; 130 have no country and get `XX`). Individuals, vessels and aircraft are not loaded because suppliers are companies.
It replaces only its own list, refuses a truncated feed (under 100 rows), and is an explicit admin action, never part of a run. **Every
new project merges the database watchlist lists into its screening** (`ProjectService._db_watchlist`; lists the synthetic registry already
carries are skipped), so a synced list really screens suppliers; a project that already started keeps the list it began with. (An earlier
revision of this document said the sync fed the watchlist, but no run read the table; that was fixed and is covered by an API-level test.)
Screening matches names across the whole list; country is metadata.
Measured on the synthetic universe (seed 42, 1,640 suppliers): 5 additional suppliers match by fuzzy name, all on generic words such
as "Electronics"; each is BLOCKED and goes to a human (watchlist hits are never auto-cleared). The four standard needs reach the same
shortlists and awardees with and without the real list. Matching all 1,640 suppliers against about 20k names takes roughly 26 s.

**Weather (Open-Meteo, LIVE, opt-in).** Studio `data_sources.weather_source = open_meteo` blends a 16-day severe-weather signal (a day
with at least 50 mm rain or 60 km/h wind counts as severe; +0.05 delay probability per severe day, capped at +0.25) into the existing
seasonal table. It is used **only in LIVE LLM mode** and only if `api.open-meteo.com` is in `data_sources.egress_allowlist`; if either
is missing or the call fails, the tool falls back to the table with confidence 0.4 (below the 0.6 escalation line) and says why in its
assumptions. The forecast horizon (16 days) is far shorter than a typical lead time, so this is a near-term disruption signal, not a
lead-time forecast. One representative manufacturing point per country is used (24 countries; others return `UNSUPPORTED_COUNTRY`).

**Egress control.** All fetches go through `net.safe_http`. New in `SafeHttp.get_following`: redirects are followed by hand and every
hop must be https and on the allowlist (and is re-resolved and IP-checked); a larger per-call cap (hard ceiling 16 MB, default stays
2 MB). OFAC publishes through a redirect chain that ends at an S3 URL, so its three hosts are listed in `connectors/catalog.py`; if
OFAC changes the storage host, the sync fails closed with `EGRESS_REDIRECT_BLOCKED` until the catalog is updated.
`data_sources.egress_allowlist` is now consumed (admin-only): a live connector whose host is not listed is unavailable.

## What is a stub, and what wiring it needs

| need | to make it real |
|---|---|
| Lead time, capacity, years in business (the most common blanks) | SAP Business Network / Ariba supplier profile. Needs a tenant URL, OAuth client and SAP's API contract. Implement `SbnSupplierProfile.profile()`; `fill_blanks()` only fills blanks and never overwrites a present value. |
| Financial risk | A commercial provider (Dun & Bradstreet, Creditsafe or similar), which needs a paid licence. No free equivalent was identified. Implement `FinancialRiskProvider.risk()` and normalise to 0..1. |
| Certificate validity | Certification-body registries (IAF CertSearch and issuers). Access differs per issuer; unverified. Implement `CertificateRegistry.check()`. |
| Clause library and fallbacks | Icertis. Needs credentials; would replace the built-in clause library. |

Every stub raises `ConnectorError("NOT_CONFIGURED")`. A caller must treat that as "no data" (REVIEW and escalate), never as a pass.

## Not built (decisions needed)

* No UI for the connector list or a "sync now" button; it is CLI only (`python -m sourceloop.connectors ...`; in Docker:
  `docker compose exec api python -m sourceloop.connectors ...`).
* No scheduled refresh of the OFAC list, no EU/UN/UK lists, no UFLPA entity list, no staleness warning.
* Fuzzy name matching is weak on generic company words (see the measurement above); a stricter matcher or a human false-positive
  workflow is the real fix, and a design decision.
* The real SBN and credit-risk connectors are stubs. The research tool already calls whatever `SupplierProfileSource` it is given (Studio
  `data_sources.profile_source`); `none` leaves blanks for review and `simulated` answers from the complete synthetic registry, so the flow runs end
  to end. A real source must be added behind the same contract, with batching and a concurrency cap.
* News stays recorded/synthetic; there is no live news connector.
