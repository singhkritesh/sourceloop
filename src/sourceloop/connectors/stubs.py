"""SAP-side and commercial connectors: contract defined, fail closed until wired to a real system.
Each raises ConnectorError("NOT_CONFIGURED"): a caller must treat that as 'no data' (REVIEW / escalate), never as a pass."""
from __future__ import annotations

from sourceloop.connectors.base import CertificateStatus, ConnectorError, FinancialRisk, SupplierProfilePatch


class SbnSupplierProfile:
    """SAP Business Network / Ariba supplier profile. TODO(wire): GET the supplier's network profile by SBN id; map lead time,
    capacity and years in business into SupplierProfilePatch. Needs tenant URL + OAuth client; add the host to the egress allowlist."""

    async def profile(self, supplier_id: str) -> SupplierProfilePatch:
        raise ConnectorError("NOT_CONFIGURED")


class FinancialRiskProvider:
    """Commercial credit-risk API. TODO(wire): call the provider with the supplier's tax id / DUNS and normalise its score to 0..1."""

    async def risk(self, supplier_id: str) -> FinancialRisk:
        raise ConnectorError("NOT_CONFIGURED")


class CertificateRegistry:
    """Issuer / IAF CertSearch lookups. TODO(wire): verify (supplier, cert_type) against the issuer; return validity and expiry."""

    async def check(self, supplier_id: str, cert_type: str) -> CertificateStatus:
        raise ConnectorError("NOT_CONFIGURED")


PROFILE_FIELDS = ("lead_time_weeks", "capacity_units", "years_in_business", "financial_risk")


def fill_blanks(existing: dict[str, object], patch: SupplierProfilePatch) -> dict[str, object]:
    """Merge a profile patch into a registry record: only blank (None) fields are filled; a present value always wins."""
    out = dict(existing)
    for k in PROFILE_FIELDS:
        v = getattr(patch, k)
        if out.get(k) is None and v is not None:
            out[k] = v
    return out
