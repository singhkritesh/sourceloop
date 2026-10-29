from decimal import Decimal

from sourceloop.bus.bus import Delivered, Rejected
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.policy.engine import Action
from sourceloop.policy.mandates import SignedMandate, overlap


def offer(price, rnd=1, lead=8, cap=1000):
    return dict(round=rnd, unit_price=str(price), lead_time_weeks=lead, capacity_units=cap)


async def test_valid_offer_delivered_and_audited(sup_a, buyer, bus, audit):
    r = await sup_a.send(T.OFFER, "buyer", offer("110"))
    assert isinstance(r, Delivered)
    assert len(buyer.inbox()) == 1
    assert await audit.verify("p1") == (True, None)


async def test_offer_below_supplier_floor_blocked_not_delivered(sup_a, buyer):
    r = await sup_a.send(T.OFFER, "buyer", offer("90"))  # floor is 92
    assert isinstance(r, Rejected) and r.rule_id == "PRICE_BELOW_FLOOR" and r.action is Action.BLOCK
    assert buyer.inbox() == []
    assert "92" not in repr(r)  # no threshold leak


async def test_buyer_counter_above_mandate_blocked(sup_a, buyer):
    await sup_a.send(T.OFFER, "buyer", offer("115"))
    r = await buyer.send(T.COUNTER, "supplier:a", offer("125"))
    assert isinstance(r, Rejected) and r.rule_id == "PRICE_ABOVE_MANDATE"
    assert "120" not in repr(r)


async def test_round_cap_and_min_step(sup_a, buyer):
    await sup_a.send(T.OFFER, "buyer", offer("115"))
    ok = await buyer.send(T.COUNTER, "supplier:a", offer("100", rnd=1))
    assert isinstance(ok, Delivered)
    small = await buyer.send(T.COUNTER, "supplier:a", offer("100.1", rnd=2))
    assert small.rule_id == "STEP_TOO_SMALL"
    cap = await buyer.send(T.COUNTER, "supplier:a", offer("101", rnd=4))
    assert cap.rule_id == "ROUND_CAP"


async def test_accept_checks_referenced_offer(sup_a, buyer):
    d = await sup_a.send(T.OFFER, "buyer", offer("118"))
    r = await buyer.send(T.ACCEPT, "supplier:a", {"offer_id": str(d.envelope.id)})
    assert isinstance(r, Delivered)
    bad = await buyer.send(T.ACCEPT, "supplier:a", {"offer_id": "00000000-0000-0000-0000-000000000000"})
    assert bad.rule_id == "UNKNOWN_OFFER"


async def test_bad_signature_replay_stage_sender(bus, sup_a, buyer, ring):
    env = bus.build("supplier:a", "buyer", T.OFFER, offer("110"))
    forged = env.model_copy(update={"payload": offer("111")})
    assert (await bus.submit(forged)).code == "BAD_SIGNATURE"
    assert isinstance(await bus.submit(env), Delivered)
    assert (await bus.submit(env)).code == "REPLAY"
    assert (await buyer.send(T.OFFER, "supplier:a", offer("100"))).code == "SENDER_NOT_ALLOWED"
    assert (await sup_a.send(T.RFQ, "buyer", {})).code in ("SENDER_NOT_ALLOWED", "INVALID_PAYLOAD:category")


async def test_extra_payload_keys_rejected(sup_a):
    r = await sup_a.send(T.OFFER, "buyer", {**offer("110"), "ignore_previous": "yes"})
    assert r.code.startswith("INVALID_PAYLOAD")


async def test_locked_clause_deviation_escalates(bus, buyer):
    from sourceloop.data.enums import StageEnum

    bus.set_stage(StageEnum.CONTRACTING)
    ok = await buyer.send(T.CONTRACT_DRAFT, "supplier:a", {"clause_keys": ["payment_terms", "liability", "termination", "ip"], "deviations": []})
    assert isinstance(ok, Delivered)
    r = await buyer.send(T.CONTRACT_DRAFT, "supplier:a", {"clause_keys": ["ip"], "deviations": ["liability"]})
    assert r.rule_id == "LOCKED_CLAUSE" and r.action is Action.ESCALATE


async def test_excluded_country_invite_blocked(bus):
    from sourceloop.bus.bus import AgentChannel
    from sourceloop.data.enums import StageEnum

    bus.set_stage(StageEnum.DISCOVERY)
    ch = AgentChannel("buyer", bus)
    inv = dict(category="pcb", volume_units=100, max_lead_time_weeks=10)
    assert isinstance(await ch.send(T.INVITE, "supplier:a", inv), Delivered)
    assert (await ch.send(T.INVITE, "supplier:b", inv)).rule_id == "EXCLUDED_COUNTRY"


def test_signed_mandate_roundtrip_and_tamper(ring, buyer_mandate, supplier_mandate):
    sm = SignedMandate.create(ring, "buyer-org", 1, buyer_mandate)
    assert sm.verify(ring) == buyer_mandate
    forged = sm.model_copy(update={"payload": {**sm.payload, "max_unit_price": "999"}})
    assert forged.verify(ring) is None


def test_locked_clauses_cannot_be_removed():
    import pytest

    from sourceloop.policy.mandates import BuyerMandate

    with pytest.raises(ValueError):
        BuyerMandate(max_unit_price=Decimal("1"), max_lead_time_weeks=1, locked_clauses=("ip",))


def test_overlap_is_bool_only(buyer_mandate, supplier_mandate):
    assert overlap(buyer_mandate, supplier_mandate) is True
    hi = supplier_mandate.model_copy(update={"unit_cost": Decimal("200")})
    assert overlap(buyer_mandate, hi) is False


def test_ai_generated_flag_is_derived_from_sender_only():
    from sourceloop.bus.bus import ORCHESTRATOR, ai_generated

    assert ai_generated("buyer") and ai_generated("supplier:S1") and not ai_generated(ORCHESTRATOR)
