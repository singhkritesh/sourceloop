"""Supplier lead agent: answers invitations/RFQs inside its own mandate; protects margin; vets buyers.

`profile` flags exist so the demo/adversarial suite can drive misbehaving suppliers whose messages the
platform must stop: they do NOT weaken any control — they exercise it."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.tools import ToolRunner, TurnGuard
from sourceloop.bus.bus import AgentChannel, SendResult
from sourceloop.bus.messages import Envelope
from sourceloop.data.enums import MessageTypeEnum as T

Q4 = Decimal("0.0001")


@dataclass
class SupplierProfile:
    attempt_below_floor: bool = False       # sends an OFFER under its own floor once (must be blocked)
    redline_locked_clause: str | None = None  # tries to alter e.g. "liability" (must reach Legal)
    inflate_claims: bool = False            # claims certificates it cannot prove (must be blocked)
    decline_rfq: bool = False
    stubborn: bool = False                  # never concedes below opening


@dataclass
class SupplierAgent:
    supplier_id: str
    channel: AgentChannel
    tools: ToolRunner
    unit_floor: Decimal                      # own mandate value (private to this agent)
    max_rounds: int
    profile: SupplierProfile = field(default_factory=SupplierProfile)
    buyer_org: str = "buyer-org"
    index_price: Decimal | None = None
    _ladder: tuple[Decimal, ...] = ()
    _last_price: Decimal | None = None
    _capacity: int = 0
    _lead: int = 0
    max_tool_calls: int = 12                 # Studio agents.supplier_lead.max_tool_calls (per handled message)

    def _guard(self) -> TurnGuard:
        return TurnGuard(max_calls=self.max_tool_calls)

    @property
    def id(self) -> str:
        return f"supplier:{self.supplier_id}"

    # ---------- discovery ----------
    async def on_invite(self, env: Envelope) -> SendResult:
        cap = await self.tools.call(K.CAPACITY, K.CapacityIn(volume_units=int(env.payload["volume_units"]),
                                                             target_lead_time_weeks=int(env.payload["max_lead_time_weeks"])),
                                  guard=self._guard())
        ok = bool(cap.ok and cap.value and cap.value.feasible)
        return await self.channel.send(T.INTEREST_REPLY, "buyer", {
            "interested": ok, "capacity_units": cap.value.offered_capacity if ok and cap.value else None,
            "reason_code": None if ok else "CAPACITY"}, in_reply_to=env.id)

    # ---------- qualification ----------
    async def on_doc_request(self, env: Envelope) -> SendResult:
        req = tuple(env.payload["required_certificates"])
        docs = await self.tools.call(K.DOCS, K.DocsIn(required_certificates=req), guard=self._guard())
        claims = list(docs.value.claims) if docs.ok and docs.value else []
        if self.profile.inflate_claims:
            claims += [c for c in req if c not in claims] or ["ISO9999"]
        return await self.channel.send(T.DOC_SUBMISSION, "buyer", {"certificates": [{"type": c, "claimed": True} for c in claims]},
                                       in_reply_to=env.id)

    # ---------- sourcing ----------
    async def on_rfq(self, env: Envelope, buyer_org: str, index_price: Decimal | None) -> SendResult:
        if self.profile.decline_rfq:
            return await self.channel.send(T.REJECT, "buyer", {"reason_code": "NOT_INTERESTED"}, in_reply_to=env.id)
        g = self._guard()
        ver = await self.tools.call(K.BUYER_VERIFY, K.BuyerVerifyIn(buyer_org=buyer_org), guard=g)
        if not (ver.ok and ver.value and ver.value.acceptable):
            return await self.channel.send(T.REJECT, "buyer", {"reason_code": "BUYER_RATING"}, in_reply_to=env.id)  # no rating disclosed
        vol, lead = int(env.payload["volume_units"]), int(env.payload["target_lead_time_weeks"])
        cap = await self.tools.call(K.CAPACITY, K.CapacityIn(volume_units=vol, target_lead_time_weeks=lead), guard=g)
        if not (cap.ok and cap.value and cap.value.feasible):
            return await self.channel.send(T.REJECT, "buyer", {"reason_code": "CAPACITY"}, in_reply_to=env.id)
        q = await self.tools.call(K.QUOTE, K.QuoteIn(volume_units=vol, lead_time_weeks=lead,
                                                     capacity_units=cap.value.offered_capacity, index_price=index_price),
                                  guard=g)
        if not (q.ok and q.value):
            return await self.channel.send(T.REJECT, "buyer", {"reason_code": "NO_QUOTE"}, in_reply_to=env.id)
        self._ladder, self._capacity, self._lead = q.value.ladder, q.value.capacity_units, q.value.lead_time_weeks
        price = self._ladder[0]
        if self.profile.attempt_below_floor:
            price = (self.unit_floor * Decimal("0.9")).quantize(Q4)
        self._last_price = price
        return await self.channel.send(T.OFFER, "buyer", {"round": 1, "unit_price": str(price), "lead_time_weeks": self._lead,
                                                          "capacity_units": self._capacity}, in_reply_to=env.id)

    def _reservation(self, rnd: int) -> Decimal:
        """Lowest price this supplier accepts in round `rnd`: decays from opening to floor, never below floor."""
        opening = self._ladder[0]
        frac = Decimal(min(rnd, self.max_rounds)) / Decimal(max(self.max_rounds, 1))
        conc = Decimal("0.5") if self.profile.stubborn else Decimal("1")
        return max(self.unit_floor, (opening - (opening - self.unit_floor) * frac * conc).quantize(Q4))

    async def on_counter(self, env: Envelope) -> SendResult:
        rnd = int(env.payload["round"])
        buyer_price = Decimal(str(env.payload["unit_price"]))
        if buyer_price >= self._reservation(rnd):
            return await self.channel.send(T.ACCEPT, "buyer", {"offer_id": str(env.id)}, in_reply_to=env.id)
        last = self._last_price if self._last_price is not None else self._ladder[0]
        if rnd >= self.max_rounds or last <= self.unit_floor:
            return await self.channel.send(T.REJECT, "buyer", {"reason_code": "NO_AGREEMENT"}, in_reply_to=env.id)
        target = max(self.unit_floor, ((last + buyer_price) / 2).quantize(Q4))
        if target >= last:  # cannot move without breaking the step rule: hold at reservation
            target = self._reservation(rnd)
        self._last_price = target
        return await self.channel.send(T.COUNTER, "buyer", {"round": rnd + 1, "unit_price": str(target), "lead_time_weeks": self._lead,
                                                            "capacity_units": self._capacity}, in_reply_to=env.id)

    # ---------- contracting ----------
    async def on_contract_draft(self, env: Envelope) -> list[SendResult]:
        out: list[SendResult] = []
        for k in env.payload["clause_keys"]:
            if self.profile.redline_locked_clause == k:
                out.append(await self.channel.send(T.REDLINE, "buyer", {"clause_key": k, "action": "propose_alternative",
                                                                        "alternative_key": k}, in_reply_to=env.id))
            else:
                out.append(await self.channel.send(T.REDLINE, "buyer", {"clause_key": k, "action": "accept"}, in_reply_to=env.id))
        return out

    async def send_onboarding(self) -> SendResult:
        return await self.channel.send(T.ONBOARD_DATA, "orchestrator",
                                       {"fields": {"legal_name": self.supplier_id, "bank_account": "SYNTH-0000", "edi": "on"}})

    async def redline(self, clause_key: str, action: str, alternative_key: str | None = None) -> SendResult:
        return await self.channel.send(T.REDLINE, "buyer", {"clause_key": clause_key, "action": action,
                                                            "alternative_key": alternative_key})

    async def step(self) -> list[SendResult]:
        """Reactive loop: drain own inbox and answer each message. Returns the send results for the orchestrator."""
        out: list[SendResult] = []
        for env in self.channel.inbox():
            if env.type == T.INVITE:
                out.append(await self.on_invite(env))
            elif env.type == T.DOC_REQUEST:
                out.append(await self.on_doc_request(env))
            elif env.type == T.RFQ:
                out.append(await self.on_rfq(env, self.buyer_org, self.index_price))
            elif env.type == T.COUNTER:
                out.append(await self.on_counter(env))
            elif env.type == T.CONTRACT_DRAFT:
                out.extend(await self.on_contract_draft(env))
        return out
