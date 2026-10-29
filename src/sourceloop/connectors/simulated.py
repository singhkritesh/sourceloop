"""Simulated supplier-profile source: stands in for the SBN supplier profile + a credit-risk provider so the whole flow can run
end to end without SAP access. It answers from the generator's COMPLETE registry (the true values the buyer-visible registry had
blanked) and returns profile fields only: never unit_cost or any supplier-private value. Replace with SbnSupplierProfile +
FinancialRiskProvider (same SupplierProfileSource contract) when real credentials exist."""
from __future__ import annotations

from sourceloop.connectors.base import ConnectorError, SupplierProfilePatch
from sourceloop.data.registry import Registry

SOURCE = "simulated: SBN supplier profile + credit-risk provider"


class SimulatedProfile:
    def __init__(self, complete: Registry):
        self._full = complete

    async def profile(self, supplier_id: str) -> SupplierProfilePatch:
        s = self._full.get(supplier_id)
        if s is None:
            raise ConnectorError("NOT_FOUND")
        fr = s.financial_risk
        return SupplierProfilePatch(supplier_id=supplier_id, lead_time_weeks=s.lead_time_weeks, capacity_units=s.capacity_units,
                                    years_in_business=s.years_in_business,
                                    financial_risk=None if fr is None or fr != fr else float(fr), source=SOURCE)
