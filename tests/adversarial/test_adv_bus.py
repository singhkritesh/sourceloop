"""ADV-01..ADV-05, ADV-07, ADV-08: message bus + policy engine under attack.

ADV-01 envelope integrity (forged / tampered signature, replayed id)
ADV-02 wrong stage / type not allowed for stage
ADV-03 sender-kind spoofing
ADV-04 injected / malformed payload fields
ADV-05 price + round + step + band rules at the mandate boundary
ADV-07 BLOCK rules are never waivable; ESCALATE rules only via a human waiver
ADV-08 locked clauses
"""
# ruff: noqa: S311  (seeded test RNG / literal 0.0.0.0 as attack input, not security-sensitive)
import random
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from adversarial.helpers import VALID, assert_delivered, assert_rejected, offer, stage_for
from sourceloop.bus.bus import SENDERS, AgentChannel, Delivered, MessageBus, Rejected, agent_kind
from sourceloop.bus.messages import STAGE_TYPES, Envelope
from sourceloop.clock import FixedClock
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import StageEnum as S
from sourceloop.policy.engine import Action
from sourceloop.policy.mandates import BuyerMandate, SupplierMandate

# ------------------------------------------------------------------ ADV-01


def _offer_env(bus, price="100", frm="supplier:a", to="buyer"):
    return bus.build(frm, to, T.OFFER, offer(price))


async def test_adv01_valid_baseline_is_delivered(bus):
    assert_delivered(await bus.submit(_offer_env(bus)))


async def test_adv01_tampered_payload_rejected(bus):
    env = _offer_env(bus)
    forged = env.model_copy(update={"payload": {**env.payload, "unit_price": "93"}})
    assert_rejected(await bus.submit(forged), "BAD_SIGNATURE")
    assert bus.log == []


@pytest.mark.parametrize("field,value", [
    ("to_agent", "supplier:b"), ("type", T.COUNTER), ("in_reply_to", uuid.UUID(int=7)),
    ("mandate_ref", "m-999"), ("stage", S.SOURCING),
])
async def test_adv01_every_signed_field_is_covered(bus, buyer, field, value):
    env = bus.build("buyer", "supplier:a", T.RFQ, VALID[T.RFQ])
    if getattr(env, field) == value:  # not a change
        value = {"stage": S.CONTRACTING}.get(field, value)
    mutated = env.model_copy(update={field: value})
    res = await bus.submit(mutated)
    assert isinstance(res, Rejected)
    if field != "stage":  # stage mutation is caught earlier by the stage gate; still rejected
        assert res.code == "BAD_SIGNATURE"


async def test_adv01_signature_forgery_variants(bus, ring):
    env = _offer_env(bus)
    good = env.signature
    variants = {
        "empty": "", "truncated": good[:-2], "bitflip": ("0" if good[0] != "0" else "1") + good[1:],
        "uppercase": good.upper() if good.upper() != good else good + "00", "long": good + good,
        "other_org_key": ring.sign_envelope("sup-b", env).signature,
        "buyer_key": ring.sign_envelope("buyer-org", env).signature,
        "orchestrator_key": ring.sign_envelope("orchestrator", env).signature,
    }
    for name, sig in variants.items():
        res = await bus.submit(env.model_copy(update={"signature": sig}))
        assert_rejected(res, "BAD_SIGNATURE"), name


async def test_adv01_identity_spoof_with_own_key(bus, ring):
    """Supplier A signs a message that claims to come from the buyer or the orchestrator."""
    for claimed, typ, payload in (("buyer", T.RFQ, VALID[T.RFQ]), ("orchestrator", T.ESCALATE, VALID[T.ESCALATE])):
        env = Envelope(project_id="p1", stage=S.SOURCING, type=typ, from_agent=claimed, to_agent="supplier:a",
                       payload=payload)
        env = ring.sign_envelope("sup-a", env)
        assert_rejected(await bus.submit(env), "BAD_SIGNATURE")


async def test_adv01_unknown_participant_and_unsigned(bus):
    env = Envelope(project_id="p1", stage=S.SOURCING, type=T.OFFER, from_agent="supplier:evil", to_agent="buyer",
                   payload=offer("100"))
    assert_rejected(await bus.submit(env), "UNKNOWN_PARTICIPANT")
    env2 = Envelope(project_id="p1", stage=S.SOURCING, type=T.OFFER, from_agent="supplier:a", to_agent="buyer",
                    payload=offer("100"))
    assert_rejected(await bus.submit(env2), "BAD_SIGNATURE")
    env3 = env2.model_copy(update={"to_agent": "supplier:ghost"})
    assert_rejected(await bus.submit(env3), "UNKNOWN_PARTICIPANT")


async def test_adv01_replayed_message_id(bus):
    env = _offer_env(bus)
    r1 = await bus.submit(env)
    assert_delivered(r1)
    assert_rejected(await bus.submit(env), "REPLAY")
    # replay with bus-assigned fields (seq, ts) rewritten: they are outside the signature, id is what counts
    assert_rejected(await bus.submit(r1.envelope.model_copy(update={"seq": 99})), "REPLAY")
    assert len(bus.log) == 1


async def test_adv01_replay_with_new_id_needs_new_signature(bus):
    """Changing the id invalidates the signature, so an attacker cannot launder a replay by re-labelling it."""
    env = _offer_env(bus)
    assert_delivered(await bus.submit(env))
    relabelled = env.model_copy(update={"id": uuid.uuid4()})
    assert_rejected(await bus.submit(relabelled), "BAD_SIGNATURE")


async def test_adv01_replay_after_policy_block_stays_blocked(bus):
    env = _offer_env(bus, "50")  # below floor
    for _ in range(3):
        res = await bus.submit(env)
        assert isinstance(res, Rejected) and res.rule_id == "PRICE_BELOW_FLOOR"
    assert bus.log == []


async def test_adv01_concurrent_replay_exactly_one_delivery(bus):
    import asyncio
    env = _offer_env(bus)
    results = await asyncio.gather(*[bus.submit(env) for _ in range(25)])
    assert sum(isinstance(r, Delivered) for r in results) == 1
    assert all(r.code == "REPLAY" for r in results if isinstance(r, Rejected))


async def test_adv01_rejections_are_audited_without_payload(bus, audit):
    env = _offer_env(bus)
    await bus.submit(env.model_copy(update={"payload": offer("93.37")}))
    recs = await audit.store.read("p1")
    rej = [r for r in recs if r.action == "message_rejected"]
    assert rej and rej[-1].rule_id == "BAD_SIGNATURE"
    assert "93.37" not in repr(rej[-1])


# ------------------------------------------------------------------ ADV-02


async def test_adv02_stale_stage_envelope(bus):
    env = bus.build("buyer", "supplier:a", T.RFQ, VALID[T.RFQ])
    bus.set_stage(S.CONTRACTING)
    assert_rejected(await bus.submit(env), "STAGE_MISMATCH")


async def test_adv02_envelope_lying_about_stage(bus, ring):
    env = Envelope(project_id="p1", stage=S.NEED, type=T.BRIEF, from_agent="buyer", to_agent="orchestrator",
                   payload=VALID[T.BRIEF])
    env = ring.sign_envelope("buyer-org", env)  # perfectly signed, but the bus is in SOURCING
    assert_rejected(await bus.submit(env), "STAGE_MISMATCH")


async def test_adv02_wrong_project(bus, ring):
    env = Envelope(project_id="OTHER", stage=S.SOURCING, type=T.RFQ, from_agent="buyer", to_agent="supplier:a",
                   payload=VALID[T.RFQ])
    env = ring.sign_envelope("buyer-org", env)
    assert_rejected(await bus.submit(env), "WRONG_PROJECT")


@pytest.mark.parametrize("stage", list(S))
async def test_adv02_every_disallowed_type_in_every_stage(bus, stage):
    bus.set_stage(stage)
    n = 0
    for t in T:
        if t in STAGE_TYPES[stage]:
            continue
        for sender in ("buyer", "supplier:a", "orchestrator"):
            env = bus.build(sender, "buyer" if sender != "buyer" else "supplier:a", t, VALID[t])
            assert_rejected(await bus.submit(env), "INVALID_MESSAGE_FOR_STAGE")
            n += 1
    assert bus.log == [] and n > 0


async def test_adv02_done_stage_accepts_nothing(bus):
    bus.set_stage(S.DONE)
    for t in T:
        env = bus.build("buyer", "supplier:a", t, VALID[t])
        assert isinstance(await bus.submit(env), Rejected)


# ------------------------------------------------------------------ ADV-03


@pytest.mark.parametrize("sender", ["buyer", "supplier:a", "supplier:b", "orchestrator"])
async def test_adv03_sender_kind_matrix(bus, sender):
    kind = agent_kind(sender)
    for t in T:
        bus.set_stage(stage_for(t))
        to = "supplier:a" if kind == "buyer" else "buyer"
        env = bus.build(sender, to, t, VALID[t])
        res = await bus.submit(env)
        if kind not in SENDERS[t]:
            assert_rejected(res, "SENDER_NOT_ALLOWED")
        else:
            assert not (isinstance(res, Rejected) and res.code == "SENDER_NOT_ALLOWED")


@pytest.mark.parametrize("t", [T.BRIEF, T.INVITE, T.RFQ, T.CONTRACT_DRAFT, T.SIGN_REQUEST, T.DOC_REQUEST, T.QUAL_RESULT])
async def test_adv03_supplier_cannot_send_buyer_only_types(bus, sup_a, t):
    bus.set_stage(stage_for(t))
    assert_rejected(await sup_a.send(t, "buyer", VALID[t]), "SENDER_NOT_ALLOWED")


@pytest.mark.parametrize("t", [T.OFFER, T.INTEREST_REPLY, T.DOC_SUBMISSION, T.ONBOARD_DATA])
async def test_adv03_buyer_cannot_send_supplier_only_types(bus, buyer, t):
    bus.set_stage(stage_for(t))
    assert_rejected(await buyer.send(t, "supplier:a", VALID[t]), "SENDER_NOT_ALLOWED")


async def test_adv03_agent_channel_exposes_no_internals(buyer, sup_a):
    for ch in (buyer, sup_a):
        public = {a for a in dir(ch) if not a.startswith("_")}
        assert public == {"agent_id", "stage", "send", "inbox"}


async def test_adv03_channel_cannot_send_as_someone_else(bus, sup_a):
    """The channel always signs as its own id, there is no from_agent parameter to forge."""
    res = await sup_a.send(T.OFFER, "buyer", offer("100"))
    assert_delivered(res)
    assert res.envelope.from_agent == "supplier:a"
    assert not any(e.from_agent == "buyer" for e in bus.log)


# ------------------------------------------------------------------ ADV-04


@pytest.mark.parametrize("extra", [
    {"mandate_override": True}, {"floor": "1"}, {"__class__": "x"}, {"qualification_status": "QUALIFIED"},
    {"approved": True}, {"price": "1"}, {"": "x"}, {"unit_price_floor": "0.01"},
])
async def test_adv04_extra_keys_rejected_for_every_type(bus, extra):
    for t in T:
        bus.set_stage(stage_for(t))
        sender = "supplier:a" if "supplier" in SENDERS[t] and "buyer" not in SENDERS[t] else "buyer"
        to = "buyer" if sender != "buyer" else "supplier:a"
        env = bus.build(sender, to, t, {**VALID[t], **extra})
        res = await bus.submit(env)
        assert isinstance(res, Rejected) and res.code.startswith("INVALID_PAYLOAD"), (t, res)


async def test_adv04_nested_extra_keys_rejected(bus, sup_a):
    bus.set_stage(S.QUALIFICATION)
    bad = {"certificates": [{"type": "ISO9001", "claimed": True, "verified": True}]}
    assert_rejected(await sup_a.send(T.DOC_SUBMISSION, "buyer", bad), "INVALID_PAYLOAD:certificates.0.verified")


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity", "-1", "0", "1e400", "99999999999999999999", "1.00001", "abc", "", None, [], {}])
async def test_adv04_hostile_prices_never_delivered(sup_a, price):
    res = await sup_a.send(T.OFFER, "buyer", {**offer("100"), "unit_price": price})
    assert isinstance(res, Rejected) and res.code.startswith("INVALID_PAYLOAD")


@pytest.mark.parametrize("field,value", [
    ("round", 0), ("round", 9), ("round", "1; DROP TABLE"), ("lead_time_weeks", 0), ("lead_time_weeks", 105),
    ("capacity_units", 0), ("capacity_units", -5), ("note", "x" * 301),
])
async def test_adv04_out_of_range_fields(sup_a, field, value):
    assert isinstance(await sup_a.send(T.OFFER, "buyer", {**offer("100"), field: value}), Rejected)


async def test_adv04_error_code_never_echoes_the_value(sup_a):
    res = await sup_a.send(T.OFFER, "buyer", {**offer("100"), "unit_price": "SECRET-INJECTED-VALUE"})
    assert "SECRET" not in res.code and res.code == "INVALID_PAYLOAD:unit_price"


def test_adv04_envelope_itself_is_strict():
    with pytest.raises(ValidationError):
        Envelope(project_id="p", stage=S.NEED, type=T.BRIEF, from_agent="buyer", to_agent="orchestrator", payload={},
                 mandate_override=1)


async def test_adv04_sql_and_script_metacharacters_are_inert_data(sup_a, bus):
    bus.set_stage(S.QUALIFICATION)
    hostile = "'; DROP TABLE message;-- <script>alert(1)</script> ${jndi:ldap://x}"
    res = await sup_a.send(T.DOC_SUBMISSION, "buyer", {"certificates": [], "answers": {"k": hostile}})
    assert_delivered(res)
    assert res.envelope.payload["answers"]["k"] == hostile  # stored verbatim, never interpreted


# ------------------------------------------------------------------ ADV-05
# fixture mandates: supplier floor = 80*1.15 = 92.0000, capacity 10000, lead >= 6 weeks, max_rounds 4 (default)
# buyer max price 120, lead <= 12, max_rounds 3, min_step 0.5%


async def test_adv05_supplier_floor_boundary(sup_a):
    r = await sup_a.send(T.OFFER, "buyer", offer("91.9999"))
    assert_rejected(r, "POLICY", "PRICE_BELOW_FLOOR")
    assert r.action is Action.BLOCK and r.needs_escalation
    assert_delivered(await sup_a.send(T.OFFER, "buyer", offer("92.0000", rnd=2)))


async def test_adv05_buyer_ceiling_boundary(buyer):
    assert_rejected(await buyer.send(T.COUNTER, "supplier:a", offer("120.0001")), "POLICY", "PRICE_ABOVE_MANDATE")
    assert_delivered(await buyer.send(T.COUNTER, "supplier:a", offer("120.0000", rnd=2)))


async def test_adv05_round_caps(buyer, sup_a):
    assert_rejected(await buyer.send(T.COUNTER, "supplier:a", offer("100", rnd=4)), "POLICY", "ROUND_CAP")
    assert_rejected(await sup_a.send(T.OFFER, "buyer", offer("100", rnd=5)), "POLICY", "ROUND_CAP")
    assert_delivered(await buyer.send(T.COUNTER, "supplier:a", offer("100", rnd=3)))


async def test_adv05_step_too_small_is_a_probe_defence(buyer, sup_a):
    assert_delivered(await sup_a.send(T.OFFER, "buyer", offer("100")))
    assert_rejected(await sup_a.send(T.COUNTER, "buyer", offer("100.2", rnd=2)), "POLICY", "STEP_TOO_SMALL")
    assert_rejected(await sup_a.send(T.COUNTER, "buyer", offer("100", rnd=2)), "POLICY", "STEP_TOO_SMALL")
    assert_delivered(await sup_a.send(T.COUNTER, "buyer", offer("99.5", rnd=2)))
    assert_delivered(await buyer.send(T.COUNTER, "supplier:a", offer("95")))
    assert_rejected(await buyer.send(T.COUNTER, "supplier:a", offer("95.1", rnd=2)), "POLICY", "STEP_TOO_SMALL")


async def test_adv05_price_band_escalates_not_blocks(bus, buyer, sup_a):
    bus.cfg.index_price = Decimal("100")  # band +-15% => [85, 115]
    r = await sup_a.send(T.OFFER, "buyer", offer("115.01"))
    assert_rejected(r, "POLICY", "PRICE_BAND")
    assert r.action is Action.ESCALATE
    assert_rejected(await buyer.send(T.COUNTER, "supplier:a", offer("84.99")), "POLICY", "PRICE_BAND")
    assert_delivered(await buyer.send(T.COUNTER, "supplier:a", offer("85.00", rnd=2)))
    assert_delivered(await sup_a.send(T.OFFER, "buyer", offer("115.00", rnd=2)))


async def test_adv05_capacity_and_lead_time(buyer, sup_a):
    assert_rejected(await sup_a.send(T.OFFER, "buyer", offer("100", cap=10001)), "POLICY", "CAPACITY_EXCEEDED")
    assert_rejected(await sup_a.send(T.OFFER, "buyer", offer("100", lead=5)), "POLICY", "LEAD_TIME_UNACHIEVABLE")
    assert_rejected(await buyer.send(T.COUNTER, "supplier:a", offer("100", lead=13)), "POLICY", "LEAD_TIME_EXCEEDS_MANDATE")


async def test_adv05_accept_checks_the_referenced_offer(bus, buyer, sup_a):
    hi = await sup_a.send(T.OFFER, "buyer", offer("130"))
    assert_delivered(hi)
    assert_rejected(await buyer.send(T.ACCEPT, "supplier:a", {"offer_id": str(hi.envelope.id)}), "POLICY",
                    "ACCEPT_OUTSIDE_MANDATE")
    low = await buyer.send(T.COUNTER, "supplier:a", offer("90", rnd=1))
    assert_delivered(low)
    assert_rejected(await sup_a.send(T.ACCEPT, "buyer", {"offer_id": str(low.envelope.id)}), "POLICY",
                    "ACCEPT_BELOW_FLOOR")
    assert_rejected(await buyer.send(T.ACCEPT, "supplier:a", {"offer_id": str(uuid.uuid4())}), "POLICY", "UNKNOWN_OFFER")
    assert_rejected(await sup_a.send(T.ACCEPT, "buyer", {"offer_id": str(uuid.uuid4())}), "POLICY", "UNKNOWN_OFFER")


async def test_adv05_buyer_cannot_accept_its_own_counter(bus, buyer):
    """ACCEPT must reference a COUNTERPARTY offer: self-accepting a counter would fabricate a deal at a price
    the supplier never agreed to (here below the supplier floor)."""
    c = await buyer.send(T.COUNTER, "supplier:a", offer("90"))
    assert_delivered(c)
    res = await buyer.send(T.ACCEPT, "supplier:a", {"offer_id": str(c.envelope.id)})
    assert isinstance(res, Rejected)


async def test_adv05_buyer_cannot_accept_another_suppliers_offer_in_this_thread(bus, buyer, sup_a):
    """An ACCEPT addressed to supplier:a must reference an offer that supplier:a made."""
    sup_b = AgentChannel("supplier:b", bus)
    ob = await sup_b.send(T.OFFER, "buyer", offer("100"))
    assert_delivered(ob)
    res = await buyer.send(T.ACCEPT, "supplier:a", {"offer_id": str(ob.envelope.id)})
    assert isinstance(res, Rejected)


async def test_adv05_property_no_delivered_message_ever_breaks_a_limit(bus, buyer, sup_a):
    rnd = random.Random(1337)
    floor, ceiling = Decimal("92.0000"), Decimal("120")
    delivered_s = delivered_b = 0
    for _ in range(400):
        price = Decimal(rnd.randint(1, 20000)) / Decimal(100)
        r_no, lead, cap = rnd.randint(1, 8), rnd.randint(1, 30), rnd.choice([1, 500, 10000, 10001, 99999])
        if rnd.random() < 0.5:
            res = await sup_a.send(T.OFFER, "buyer", offer(price, r_no, lead, cap))
            if isinstance(res, Delivered):
                delivered_s += 1
                assert price >= floor and r_no <= 4 and cap <= 10000 and lead >= 6
        else:
            res = await buyer.send(T.COUNTER, "supplier:a", offer(price, r_no, lead, cap))
            if isinstance(res, Delivered):
                delivered_b += 1
                assert price <= ceiling and r_no <= 3 and lead <= 12
    assert delivered_s > 0 and delivered_b > 0  # the loop exercised both outcomes


# ------------------------------------------------------------------ ADV-07

ALL_BLOCK_RULES = ["PRICE_BELOW_FLOOR", "PRICE_ABOVE_MANDATE", "ROUND_CAP", "STEP_TOO_SMALL", "CAPACITY_EXCEEDED",
                   "LEAD_TIME_UNACHIEVABLE", "LEAD_TIME_EXCEEDS_MANDATE", "UNKNOWN_OFFER", "ACCEPT_BELOW_FLOOR",
                   "ACCEPT_OUTSIDE_MANDATE", "CLAIM_NOT_PROVABLE", "EXCLUDED_COUNTRY", "TARGET_DISCLOSURE_NOT_ALLOWED"]


async def test_adv07_block_rules_ignore_waivers(bus, buyer, sup_a):
    bus.waivers.update(ALL_BLOCK_RULES + ["PRICE_BAND", "LOCKED_CLAUSE", "CLAUSE_NOT_IN_LIBRARY"])
    cases = [
        (sup_a, T.OFFER, "buyer", offer("1")), (buyer, T.COUNTER, "supplier:a", offer("999")),
        (sup_a, T.OFFER, "buyer", offer("100", rnd=7)), (sup_a, T.OFFER, "buyer", offer("100", cap=10**6)),
        (sup_a, T.OFFER, "buyer", offer("100", lead=1)), (buyer, T.COUNTER, "supplier:a", offer("100", lead=99)),
        (buyer, T.ACCEPT, "supplier:a", {"offer_id": str(uuid.uuid4())}),
    ]
    for ch, t, to, payload in cases:
        res = await ch.send(t, to, payload)
        assert isinstance(res, Rejected) and res.action is Action.BLOCK, (t, payload, res)
    bus.set_stage(S.DISCOVERY)  # excluded country (supplier:b is RU)
    r = await buyer.send(T.INVITE, "supplier:b", {"category": "x", "volume_units": 1, "max_lead_time_weeks": 5})
    assert_rejected(r, "POLICY", "EXCLUDED_COUNTRY")
    assert r.action is Action.BLOCK
    bus.set_stage(S.QUALIFICATION)
    r = await sup_a.send(T.DOC_SUBMISSION, "buyer", {"certificates": [{"type": "ISO9999", "claimed": True}]})
    assert_rejected(r, "POLICY", "CLAIM_NOT_PROVABLE")
    bus.set_stage(S.SOURCING)
    r = await buyer.send(T.RFQ, "supplier:a", {**VALID[T.RFQ], "disclosed_target_price": "50"})
    assert_rejected(r, "POLICY", "TARGET_DISCLOSURE_NOT_ALLOWED")


async def test_adv07_escalate_rules_need_an_explicit_waiver_and_only_for_that_rule(bus, buyer, sup_a, audit):
    bus.cfg.index_price = Decimal("100")
    res = await sup_a.send(T.OFFER, "buyer", offer("120"))
    assert_rejected(res, "POLICY", "PRICE_BAND")
    bus.waivers.add("LOCKED_CLAUSE")  # a waiver for a different rule changes nothing
    assert_rejected(await sup_a.send(T.OFFER, "buyer", offer("121", rnd=2)), "POLICY", "PRICE_BAND")
    bus.waivers.add("PRICE_BAND")
    assert_delivered(await sup_a.send(T.OFFER, "buyer", offer("122", rnd=2)))
    assert "policy_waived" in [r.action for r in await audit.store.read("p1")]


async def test_adv07_waiver_does_not_unblock_other_violations_in_same_message(bus, sup_a):
    """Band waiver must not let a below-floor price through (BLOCK is evaluated first, and is final)."""
    bus.cfg.index_price = Decimal("1000")
    bus.waivers.add("PRICE_BAND")
    assert_rejected(await sup_a.send(T.OFFER, "buyer", offer("50")), "POLICY", "PRICE_BELOW_FLOOR")


def test_adv07_agents_have_no_waiver_handle(buyer, sup_a):
    for ch in (buyer, sup_a):
        assert not hasattr(ch, "waivers") and not hasattr(ch, "bus") and not hasattr(ch, "register")


# ------------------------------------------------------------------ ADV-08

LOCKED = ["liability", "termination", "ip"]


@pytest.mark.parametrize("clause", LOCKED)
async def test_adv08_buyer_draft_with_locked_deviation_escalates(bus, buyer, clause):
    bus.set_stage(S.CONTRACTING)
    r = await buyer.send(T.CONTRACT_DRAFT, "supplier:a", {"clause_keys": ["payment_terms", clause], "deviations": [clause]})
    assert_rejected(r, "POLICY", "LOCKED_CLAUSE")
    assert r.action is Action.ESCALATE


@pytest.mark.parametrize("clause", LOCKED)
@pytest.mark.parametrize("action", ["propose_alternative", "reject"])
async def test_adv08_supplier_redline_of_locked_clause_escalates(bus, sup_a, clause, action):
    bus.set_stage(S.CONTRACTING)
    r = await sup_a.send(T.REDLINE, "buyer", {"clause_key": clause, "action": action, "alternative_key": "delivery"})
    assert_rejected(r, "POLICY", "LOCKED_CLAUSE")


@pytest.mark.parametrize("clause", LOCKED)
async def test_adv08_accepting_a_locked_clause_is_fine(bus, sup_a, clause):
    bus.set_stage(S.CONTRACTING)
    assert_delivered(await sup_a.send(T.REDLINE, "buyer", {"clause_key": clause, "action": "accept"}))


async def test_adv08_unknown_and_case_variant_clause_keys_fail_closed(bus, sup_a, buyer):
    bus.set_stage(S.CONTRACTING)
    for key in ("Liability", "liability ", "LIABILITY", "li​ability", "liabil1ty", "../liability", ""):
        r = await sup_a.send(T.REDLINE, "buyer", {"clause_key": key, "action": "propose_alternative"})
        assert_rejected(r, "POLICY", "CLAUSE_NOT_IN_LIBRARY")
        r = await buyer.send(T.CONTRACT_DRAFT, "supplier:a", {"clause_keys": ["payment_terms"], "deviations": [key]})
        assert isinstance(r, Rejected) and r.action is Action.ESCALATE


async def test_adv08_alternative_key_must_exist(bus, sup_a):
    bus.set_stage(S.CONTRACTING)
    r = await sup_a.send(T.REDLINE, "buyer", {"clause_key": "payment_terms", "action": "propose_alternative",
                                              "alternative_key": "free_lunch"})
    assert_rejected(r, "POLICY", "CLAUSE_NOT_IN_LIBRARY")


async def test_adv08_non_negotiable_unlocked_clause_cannot_be_redlined(bus, sup_a):
    bus.cfg.clause_library["warranty"] = {"negotiable": False, "locked": False}
    bus.set_stage(S.CONTRACTING)
    r = await sup_a.send(T.REDLINE, "buyer", {"clause_key": "warranty", "action": "reject"})
    assert_rejected(r, "POLICY", "CLAUSE_NOT_NEGOTIABLE")


async def test_adv08_locked_flag_in_library_protects_even_if_mandate_forgot(bus, ring, audit):
    weak = SupplierMandate(unit_cost=Decimal("80"), min_margin_pct=Decimal("15"), max_capacity_units=10, lead_time_weeks=6,
                           unchangeable_clauses=())
    b = MessageBus("p1", audit, ring, {"buyer": "buyer-org", "supplier:a": "sup-a"}, {"supplier:a": weak},
                   config=bus.cfg, clock=FixedClock())
    b.set_stage(S.CONTRACTING)
    r = await AgentChannel("supplier:a", b).send(T.REDLINE, "buyer", {"clause_key": "ip", "action": "reject"})
    assert_rejected(r, "POLICY", "LOCKED_CLAUSE")


@pytest.mark.parametrize("keep", [(), ("liability",), ("liability", "termination"), ("ip", "termination")])
def test_adv08_buyer_mandate_cannot_drop_locked_clauses(keep):
    with pytest.raises(ValidationError):
        BuyerMandate(max_unit_price=Decimal("1"), max_lead_time_weeks=1, locked_clauses=keep)


async def test_adv08_draft_cannot_silently_omit_locked_clauses(bus, buyer):
    """Leaving liability/termination/ip OUT of the contract is a deviation from the locked set; it must route to Legal."""
    bus.set_stage(S.CONTRACTING)
    r = await buyer.send(T.CONTRACT_DRAFT, "supplier:a", {"clause_keys": ["payment_terms", "delivery"], "deviations": []})
    assert isinstance(r, Rejected) and r.rule_id == "LOCKED_CLAUSE"
