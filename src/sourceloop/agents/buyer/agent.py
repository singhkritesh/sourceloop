"""Buyer lead agent. Talks only through its AgentChannel and typed tools (no data/policy/orchestrator imports).

Strategy lives in code (concession schedule); an LLM may later *choose among code-supplied options*
via the `Chooser` hook, never invent prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sourceloop.agents.common import contracts as K
from sourceloop.agents.common import par
from sourceloop.agents.common.tools import ToolRunner, TurnGuard
from sourceloop.bus.bus import AgentChannel, Delivered, SendResult
from sourceloop.bus.messages import Envelope
from sourceloop.data.enums import MessageTypeEnum as T

Q4 = Decimal("0.0001")


class Chooser(Protocol):
    def choose(self, options: tuple[Decimal, ...], context: dict) -> int: ...


class RuleChooser:
    def choose(self, options: tuple[Decimal, ...], context: dict) -> int:
        return 0


@dataclass
class BuyerPolicy:
    """Negotiation behaviour (all editable in the Studio via workflow/agent config)."""
    concession: Decimal = Decimal("0.45")      # fraction of the gap closed per round
    opening_discount: Decimal = Decimal("0.90")  # opening counter as a share of the supplier's offer
    delay_penalty_pct: Decimal = Decimal("20")
    news_escalate_severity: float = 0.7
    news_min_confidence: float = 0.6
    max_tool_calls_per_turn: int = 12


@dataclass
class Evaluated:
    supplier_id: str
    offer: Envelope
    landed_total: Decimal
    delay_probability: float
    news_severity: float


class BuyerAgent:
    id = "buyer"

    def __init__(self, channel: AgentChannel, tools: ToolRunner, max_unit_price: Decimal, target_price: Decimal | None,
                 max_rounds: int, min_step_pct: Decimal, policy: BuyerPolicy | None = None,
                 chooser: Chooser | None = None) -> None:
        self.ch, self.tools = channel, tools
        self.max_price, self.target = max_unit_price, target_price   # own mandate values: allowed to know
        self.max_rounds, self.min_step_pct = max_rounds, min_step_pct
        self.policy = policy or BuyerPolicy()
        self.chooser = chooser or RuleChooser()

    def _guard(self) -> TurnGuard:
        return TurnGuard(max_calls=self.policy.max_tool_calls_per_turn)

    # ---------- discovery / qualification ----------
    async def discover(self, need: K.NeedSpec, longlist: int, min_countries: int):
        return await self.tools.call(K.RESEARCH, K.ResearchIn(need=need, longlist_size=longlist, min_countries=min_countries),
                                     guard=self._guard())

    async def send_all(self, type_: T, targets: list[str], payload_of) -> dict[str, SendResult]:
        res = await par.gather(*[self.ch.send(type_, t, payload_of(t)) for t in targets])
        return dict(zip(targets, res, strict=True))

    async def screen(self, need: K.NeedSpec, supplier_id: str):
        return await self.tools.call(K.COMPLIANCE, K.ComplianceIn(need=need, supplier_id=supplier_id), guard=self._guard())

    async def region_risk(self, country: str, lead_weeks: int):
        g = self._guard()
        news, weather = await par.gather(self.tools.call(K.NEWS, K.NewsIn(country=country), guard=g),
                                       self.tools.call(K.WEATHER, K.WeatherIn(origin_country=country, lead_time_weeks=lead_weeks), guard=g))
        return news, weather

    async def report_qualification(self, supplier_id: str, status: str, reasons: tuple[str, ...]) -> SendResult:
        return await self.ch.send(T.QUAL_RESULT, "orchestrator", {"status": status, "reasons": list(reasons)[:20]})

    # ---------- sourcing ----------
    async def evaluate(self, supplier_id: str, offer: Envelope, country: str, weather_p: float, news_sev: float) -> Evaluated:
        price = Decimal(str(offer.payload["unit_price"]))
        lc = await self.tools.call(K.LANDED, K.LandedIn(unit_price=price, origin_country=country,
                                                        delay_probability=min(1.0, weather_p + news_sev * 0.1),
                                                        delay_penalty_pct=self.policy.delay_penalty_pct), guard=self._guard())
        total = lc.value.total if lc.ok and lc.value else Decimal("Infinity")
        return Evaluated(supplier_id, offer, total, weather_p, news_sev)

    def _reservation(self, rnd: int) -> Decimal:
        """Highest price the buyer will ACCEPT in round `rnd` (never above its mandate)."""
        lo = self.target if self.target is not None else self.max_price * Decimal("0.85")
        frac = Decimal(rnd) / Decimal(max(self.max_rounds, 1))
        return min(self.max_price, lo + (self.max_price - lo) * frac)

    def decide(self, last_supplier_price: Decimal, my_last_counter: Decimal | None, rnd: int) -> tuple[str, Decimal | None]:
        """Returns ("accept", None) | ("counter", price) | ("walk", None). Prices come from code, not an LLM."""
        if last_supplier_price <= self._reservation(rnd):
            return "accept", None
        if rnd >= self.max_rounds:
            return "walk", None
        base = my_last_counter if my_last_counter is not None else (last_supplier_price * self.policy.opening_discount)
        if my_last_counter is not None:
            base = my_last_counter + (last_supplier_price - my_last_counter) * self.policy.concession
        price = min(base, self.max_price).quantize(Q4)
        if my_last_counter is not None and price <= my_last_counter * (1 + self.min_step_pct / 100):
            price = min(self.max_price, my_last_counter * (1 + self.min_step_pct / 100 * 2)).quantize(Q4)
        if price >= last_supplier_price:
            return "accept", None
        options = (price,)
        return "counter", options[self.chooser.choose(options, {"round": rnd})]

    async def counter(self, supplier_id: str, last: Envelope, price: Decimal, rnd: int) -> SendResult:
        return await self.ch.send(T.COUNTER, f"supplier:{supplier_id}",
                                  {"round": rnd, "unit_price": str(price), "lead_time_weeks": int(last.payload["lead_time_weeks"]),
                                   "capacity_units": int(last.payload["capacity_units"])}, in_reply_to=last.id)

    async def accept(self, supplier_id: str, target: Envelope) -> SendResult:
        return await self.ch.send(T.ACCEPT, f"supplier:{supplier_id}", {"offer_id": str(target.id)}, in_reply_to=target.id)

    async def walk(self, supplier_id: str, code: str = "NO_AGREEMENT") -> SendResult:
        return await self.ch.send(T.REJECT, f"supplier:{supplier_id}", {"reason_code": code})

    # ---------- contracting ----------
    async def draft_contract(self, supplier_id: str, clause_keys: tuple[str, ...], deviations: tuple[str, ...] = ()) -> SendResult:
        return await self.ch.send(T.CONTRACT_DRAFT, f"supplier:{supplier_id}",
                                  {"clause_keys": list(clause_keys), "deviations": list(deviations)})

    async def request_signature(self, supplier_id: str, contract_id: str) -> SendResult:
        return await self.ch.send(T.SIGN_REQUEST, f"supplier:{supplier_id}", {"contract_id": contract_id})


def delivered(res: SendResult) -> bool:
    return isinstance(res, Delivered)
