"""The connector map as data: what each data need maps to, what is real, what is a stub. docs/03-connectors.md renders this."""
from __future__ import annotations

from sourceloop.connectors.base import Capability as C
from sourceloop.connectors.base import ConnectorInfo, Status

OFAC_HOSTS = ("www.treasury.gov", "sanctionslistservice.ofac.treas.gov", "wc2h-sls-prod-public-published.s3.us-gov-west-1.amazonaws.com")
OPEN_METEO_HOSTS = ("api.open-meteo.com",)

CATALOG: tuple[ConnectorInfo, ...] = (
    ConnectorInfo("ofac_sdn", C.SANCTIONS, "US Treasury OFAC (SDN list)", Status.LIVE,
                  "Downloads the public SDN entity list and loads it into the watchlist (entities only, with aliases and addresses).",
                  fields=("sanctioned entity names", "aliases", "country"), hosts=OFAC_HOSTS,
                  notes="Public, no key. Run `python -m sourceloop.connectors sync-ofac`. Individuals, vessels and aircraft are not loaded. Screening matches names across the whole list (country is metadata)."),
    ConnectorInfo("open_meteo", C.WEATHER, "Open-Meteo forecast API", Status.LIVE,
                  "16-day severe-weather signal (heavy rain, high wind) per origin country, blended into the delay probability.",
                  fields=("forecast precipitation", "forecast wind"), hosts=OPEN_METEO_HOSTS,
                  notes="Public, no key (non-commercial terms; check licensing before production). Live LLM mode + Studio data_sources.weather_source=open_meteo."),
    ConnectorInfo("synthetic_registry", C.SUPPLIER_PROFILE, "Built-in generated registry", Status.SYNTHETIC,
                  "The 1,640-supplier synthetic universe, with deliberately blanked fields to exercise fail-closed handling.",
                  fields=("name", "country", "certificates", "lead time", "capacity", "years in business", "financial risk")),
    ConnectorInfo("simulated_profile", C.SUPPLIER_PROFILE, "Simulated SBN profile + credit-risk (answers from the complete synthetic registry)", Status.SYNTHETIC,
                  "Fills blank lead time, capacity, years in business and financial risk so a run can complete without SAP access. Enabled by Studio data_sources.profile_source=simulated.",
                  fields=("lead_time_weeks", "capacity_units", "years_in_business", "financial_risk"),
                  notes="Stand-in for the two STUB connectors below; same contract. Never returns unit_cost."),
    ConnectorInfo("synthetic_news", C.NEWS, "Recorded news corpus", Status.SYNTHETIC,
                  "Recorded/synthetic regional news; the only LLM-read external text in the system.", fields=("regional news risk",)),
    ConnectorInfo("sbn_supplier_profile", C.SUPPLIER_PROFILE, "SAP Business Network supplier profile / Ariba Supplier Management", Status.STUB,
                  "Would fill blank lead time, capacity and years in business from the supplier's own network profile.",
                  fields=("lead_time_weeks", "capacity_units", "years_in_business"), needs_credentials=True, sap_system="SAP Business Network / Ariba",
                  notes="Needs a tenant, API credentials and SAP's API contract; not available in this prototype."),
    ConnectorInfo("financial_risk", C.FINANCIAL_RISK, "Commercial credit-risk provider (e.g. Dun & Bradstreet, Creditsafe)", Status.STUB,
                  "Would fill the blank financial-risk score. A paid commercial licence is required.",
                  fields=("financial_risk",), needs_credentials=True, notes="No free equivalent. Provider choice and licence are a business decision."),
    ConnectorInfo("certificate_registry", C.CERTIFICATES, "Certification-body registries (IAF CertSearch and issuers)", Status.STUB,
                  "Would verify claimed certificates against the issuer instead of trusting the supplier's document.",
                  fields=("certificate validity", "expiry", "issuer"), needs_credentials=False,
                  notes="Registry access and terms differ per issuer; unverified."),
    ConnectorInfo("icertis_clauses", C.CONTRACT_CLAUSES, "Icertis contract platform", Status.STUB,
                  "Would supply the approved clause library and fallback positions instead of the built-in library.",
                  fields=("clause library", "fallbacks"), needs_credentials=True, sap_system="Icertis (SAP partner)"),
)


def by_id(connector_id: str) -> ConnectorInfo | None:
    return next((c for c in CATALOG if c.id == connector_id), None)
