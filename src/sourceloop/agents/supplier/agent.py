"""Supplier lead agent: answers invitations/RFQs inside its own mandate; protects margin; vets buyers.

`profile` flags exist so the demo/adversarial suite can drive misbehaving suppliers whose messages the
platform must stop: they do NOT weaken any control — they exercise it."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.deliberate import Deliberator, Move, Pick, RuleDeliberator
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
    slow_reply: bool = False                # ignores the first RFQ; answers once the buyer follows up
    chases_needed: int = 1                  # with slow_reply: how many follow-ups it takes before it answers
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
    deliberator: Deliberator = field(default_factory=RuleDeliberator)
    last_pick: Pick | None = None
    facts: dict[str, float] = field(default_factory=dict)  # this supplier's own figures, shared only when the buyer asks (RFI)
    _rfq_seen: int = 0
    _chased: int = 0
    _open_rfq: Envelope | None = None
    pressure: dict = field(default_factory=dict)  # what the buyer said about competing offers (closed signals and a rounded band), if anything

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

    async def on_rfi(self, env: Envelope) -> SendResult:
        """Answer a request for information with the figures this supplier can stand behind; a field it does not know is left out."""
        return await self.channel.send(T.RFI_REPLY, "buyer", {"values": {k: self.facts[k] for k in env.payload["fields"] if k in self.facts}},
                                       in_reply_to=env.id)

    # ---------- sourcing ----------
    async def on_rfq(self, env: Envelope, buyer_org: str, index_price: Decimal | None) -> SendResult | None:
        if self.profile.slow_reply and self._rfq_seen == 0:
            self._rfq_seen = 1
            return None  # no answer yet; the buyer's agent will follow up
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

    def _counter_moves(self, buyer_price: Decimal, rnd: int) -> list[Move]:
        """The legal moves for this supplier, the rule-based one FIRST. Every counter price is at or above the supplier's own floor and below
        its previous price; accepting is only offered at or above the floor."""
        last = self._last_price if self._last_price is not None else self._ladder[0]
        out: list[Move] = []
        if buyer_price >= self._reservation(rnd):
            out.append(Move("ACCEPT", "accept", buyer_price, f"Accept the buyer's counter of {buyer_price}."))
        elif rnd >= self.max_rounds or last <= self.unit_floor:
            out.append(Move("WALK", "walk", None, "End the negotiation without a deal."))
        else:
            target = max(self.unit_floor, ((last + buyer_price) / 2).quantize(Q4))
            if target >= last:
                target = self._reservation(rnd)
            out.append(Move("COUNTER_STEADY", "counter", target, f"Counter at {target}, the planned step towards the buyer."))

        def add(m: Move) -> None:
            if all((m.kind, m.price) != (o.kind, o.price) for o in out):
                out.append(m)
        if buyer_price >= self.unit_floor:
            add(Move("ACCEPT", "accept", buyer_price, f"Accept the buyer's counter of {buyer_price}."))
        if rnd < self.max_rounds and last > self.unit_floor:
            steady = out[0].price if out[0].kind == "counter" and out[0].price is not None else self._reservation(rnd)
            firm = max(self.unit_floor, ((last + steady) / 2).quantize(Q4))
            generous = max(self.unit_floor, ((steady + buyer_price) / 2).quantize(Q4))
            for lab, pr, txt in (("COUNTER_FIRM", firm, "a firm counter that gives up little"), ("COUNTER_GENEROUS", generous, "a counter that moves further towards the buyer")):
                if self.unit_floor <= pr < last:
                    add(Move(lab, "counter", pr, f"Counter at {pr}: {txt}."))
        if self._strong_pressure() and out[0].kind == "counter":  # a clear price gap against a rival moves the planned counter towards the buyer, never below the floor
            gen = next((m for m in out if m.label == "COUNTER_GENEROUS"), None)
            if gen is not None:
                out.remove(gen)
                out.insert(0, gen)
        if out[0].kind != "walk":
            add(Move("WALK", "walk", None, "End the negotiation without a deal."))
        return out

    def _strong_pressure(self) -> bool:
        return "PRICE_BELOW" in self.pressure.get("signals", ()) and self.pressure.get("band") in ("5_TO_10", "10_TO_20", "OVER_20")

    def _pressure_line(self) -> tuple[str, ...]:
        s = self.pressure.get("signals", ())
        if not s:
            return ()
        said = {"OTHER_OFFERS": "other qualified offers exist", "PRICE_BELOW": "at least one is priced below yours", "FASTER_DELIVERY": "one promises faster delivery",
                "MORE_RELIABLE": "one has a better delivery record", "PAID_LESS_BEFORE": "the buyer has paid less for this before"}
        return ("The buyer has told you: " + "; ".join(said[x] for x in s if x in said) + ". Weigh this, but never go below your floor.",)

    async def on_counter(self, env: Envelope) -> SendResult:
        rnd = int(env.payload["round"])
        buyer_price = Decimal(str(env.payload["unit_price"]))
        last = self._last_price if self._last_price is not None else self._ladder[0]
        moves = self._counter_moves(buyer_price, rnd)
        situation = (f"You are the supplier. Round {rnd} of {self.max_rounds}.",
                     f"The buyer's counter is {buyer_price}; your own previous price was {last}; your floor is {self.unit_floor}.",
                     f"The buyer's counter is {((buyer_price - self.unit_floor) / self.unit_floor * 100).quantize(Decimal('0.1'))}% above your floor.",
                     *self._pressure_line())
        pick = await self.deliberator.pick(f"supplier:{self.supplier_id}", self.supplier_id, rnd, situation, moves)
        self.last_pick = pick
        m = moves[pick.index]
        if m.kind == "accept":
            return await self.channel.send(T.ACCEPT, "buyer", {"offer_id": str(env.id)}, in_reply_to=env.id)
        if m.kind == "walk" or m.price is None:
            return await self.channel.send(T.REJECT, "buyer", {"reason_code": "NO_AGREEMENT"}, in_reply_to=env.id)
        self._last_price = m.price
        return await self.channel.send(T.COUNTER, "buyer", {"round": rnd + 1, "unit_price": str(m.price), "lead_time_weeks": self._lead,
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
            elif env.type == T.RFI:
                out.append(await self.on_rfi(env))
            elif env.type == T.FOLLOW_UP:
                self._chased += 1
                if self._chased >= self.profile.chases_needed:
                    self._rfq_seen = 2  # reminded enough: answer the RFQ that is still open
                if self._rfq_seen == 2 and self._open_rfq is not None:
                    out.append(await self.on_rfq(self._open_rfq, self.buyer_org, self.index_price))
            elif env.type == T.RFQ:
                self._open_rfq = env
                r = await self.on_rfq(env, self.buyer_org, self.index_price)
                if r is not None:
                    out.append(r)
            elif env.type == T.LEVERAGE:
                self.pressure = {"signals": tuple(env.payload["signals"]), "band": env.payload.get("price_gap_band")}
            elif env.type == T.COUNTER:
                out.append(await self.on_counter(env))
            elif env.type == T.CONTRACT_DRAFT:
                out.extend(await self.on_contract_draft(env))
        return out
