"""Supplier-side subagents. Each supplier agent gets its own tool set closed over ITS OWN private data;
nothing here is reachable from the buyer side."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.tools import Tool, ToolOutput
from sourceloop.policy.mandates import SupplierMandate


@dataclass(frozen=True)
class BuyerProfile:
    payment_days_avg: float = 35.0
    disputes_12m: int = 0
    resource_churn_pct: float = 0.1  # how often the buyer re-sources a awarded project


def _frac(s: str) -> float:
    return int(hashlib.sha256(s.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def build_supplier_tools(supplier_id: str, mandate: SupplierMandate, buyer_profiles: dict[str, BuyerProfile],
                         inflate_claims: tuple[str, ...] = ()) -> list[Tool]:
    q4 = Decimal("0.0001")

    async def demand(inp: K.DemandIn) -> ToolOutput[K.DemandOut]:
        load = 0.3 + 0.55 * _frac(f"{supplier_id}:load")  # deterministic synthetic forecast
        return ToolOutput[K.DemandOut](value=K.DemandOut(load_pct=round(load, 3)), confidence=0.6,
                                       assumptions=("synthetic demand forecast",))

    async def verify(inp: K.BuyerVerifyIn) -> ToolOutput[K.BuyerVerifyOut]:
        p = buyer_profiles.get(inp.buyer_org)
        if p is None:
            return ToolOutput[K.BuyerVerifyOut](value=K.BuyerVerifyOut(rating=0.5, acceptable=0.5 >= float(mandate.min_buyer_rating),
                                                                       flags=("NO_HISTORY",)), confidence=0.4)
        rating = max(0.0, min(1.0, 1 - p.payment_days_avg / 120 - p.disputes_12m * 0.05 - p.resource_churn_pct * 0.5))
        flags = tuple(f for f, c in (("SLOW_PAYER", p.payment_days_avg > 60), ("DISPUTES", p.disputes_12m >= 3),
                                     ("HIGH_RESOURCING", p.resource_churn_pct > 0.4)) if c)
        return ToolOutput[K.BuyerVerifyOut](value=K.BuyerVerifyOut(rating=round(rating, 3),
                                                                   acceptable=rating >= float(mandate.min_buyer_rating), flags=flags))

    async def capacity(inp: K.CapacityIn) -> ToolOutput[K.CapacityOut]:
        load = 0.3 + 0.55 * _frac(f"{supplier_id}:load")
        available = int(mandate.max_capacity_units * (1 - load * 0.6))
        feasible = available >= inp.volume_units
        return ToolOutput[K.CapacityOut](value=K.CapacityOut(
            feasible=feasible, offered_capacity=min(available, max(inp.volume_units, 1)) if available > 0 else 1,
            lead_time_weeks=mandate.lead_time_weeks, reason_code=None if feasible else "CAPACITY"), confidence=0.8)

    async def quote(inp: K.QuoteIn) -> ToolOutput[K.QuoteOut]:
        floor = mandate.min_unit_price
        market = (inp.index_price * Decimal("1.08")) if inp.index_price else floor * Decimal("1.25")
        opening = max(floor * Decimal("1.1"), market).quantize(q4)
        n = 5
        ladder = tuple((opening - (opening - floor) * Decimal(i) / Decimal(n - 1)).quantize(q4) for i in range(n))
        ladder = ladder[:-1] + (floor,)
        return ToolOutput[K.QuoteOut](value=K.QuoteOut(ladder=ladder, lead_time_weeks=mandate.lead_time_weeks,
                                                       capacity_units=inp.capacity_units, rationale="cost-plus with market adjustment"))

    async def docs(inp: K.DocsIn) -> ToolOutput[K.DocsOut]:
        provable = set(mandate.provable_certificates)
        claims = tuple(c for c in inp.required_certificates if c in provable) + tuple(inflate_claims)
        return ToolOutput[K.DocsOut](value=K.DocsOut(claims=claims))

    return [
        Tool(K.DEMAND, K.DemandIn, K.DemandOut, demand, timeout_s=5, retries=0),
        Tool(K.BUYER_VERIFY, K.BuyerVerifyIn, K.BuyerVerifyOut, verify, timeout_s=5, retries=0),
        Tool(K.CAPACITY, K.CapacityIn, K.CapacityOut, capacity, timeout_s=5, retries=0),
        Tool(K.QUOTE, K.QuoteIn, K.QuoteOut, quote, timeout_s=5, retries=0),
        Tool(K.DOCS, K.DocsIn, K.DocsOut, docs, timeout_s=5, retries=0),
    ]
