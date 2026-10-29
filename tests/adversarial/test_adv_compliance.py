"""ADV-12 watchlist look-alike / duplicate-entity evasion, ADV-13 certificates and missing data."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.tools import ToolRunner
from sourceloop.compliance.resolution import resolve
from sourceloop.compliance.rules import WatchlistIndex, qualify
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import QualStatusEnum as Q
from sourceloop.data.enums import StageEnum as S
from sourceloop.data.news import SyntheticNews
from sourceloop.data.registry import Cert, Need, Registry, SupplierRec, WatchEntry
from sourceloop.data.synthetic import TODAY
from sourceloop.llm.client import LLMClient, MemoryReplayStore
from sourceloop.llm.config import stub_config
from sourceloop.llm.providers import StubProvider
from sourceloop.subagents.buyer_tools import build_buyer_tools

NEED = Need("N", "pcb", "high_tech", 1000, ("ISO9001",), ("RU",), 12)
WL = [WatchEntry("W1", "Acme Holding Group", "XX", "SDN", aliases=("Volkov Trading",)),
      WatchEntry("W2", "Nordwind Elektronik GmbH", "DE", "SDN")]


def rec(**kw) -> SupplierRec:
    base = dict(id="S1", name="Clean Circuits Ltd", country="DE", tax_id="DE1", categories=("pcb",),
                certs=(Cert("ISO9001", "cb", TODAY + timedelta(days=100)),), capacity_units=5000, lead_time_weeks=8,
                financial_risk=0.2, years_in_business=10, on_time_pct=0.9, quality_score=0.9, unit_cost=Decimal("50"))
    base.update(kw)
    return SupplierRec(**base)


# ------------------------------------------------------------------ ADV-12

LOOKALIKES = [
    "Acme Holding Group", "ACME HOLDING GROUP LTD", "Acme Holding Group Inc.", "Acme Holding Group GmbH",
    "Ácme Hölding Group", "Acmé  Holding  Group", "Holding Group Acme", "Group Holding Acme GmbH",
    "A.C.M.E. Holding Group", "Acme-Holding-Group", "Acme Holdings Group", "Acme Hldg Group", "Acme Holding Grp",
    "Acne Holding Group", "Acme​Holding Group", "Acme Holding Group", "Асme Holding Group",  # Cyrillic A
    "Ａｃｍｅ Ｈｏｌｄｉｎｇ Ｇｒｏｕｐ",  # full-width
    "Volkov Trading", "VOLKOV TRADING CO.", "Trading Volkov", "Nordwind Elektronik", "Elektronik Nordwind GmbH",
    "Nordwind Elektronic GmbH", "Nord-wind Elektronik GmbH",
]


@pytest.mark.parametrize("name", LOOKALIKES)
def test_adv12_lookalike_names_are_blocked_never_qualified(name):
    d = qualify(rec(name=name), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is Q.BLOCKED and "WATCHLIST_HIT" in d.reasons


@pytest.mark.parametrize("name", ["Nordwind Electronics Gmbh", "Totally Different Corp", "Clean Circuits Ltd"])
def test_adv12_negative_control_unrelated_names_are_not_blocked(name):
    assert qualify(rec(name=name), NEED, WatchlistIndex(WL), TODAY).status is Q.QUALIFIED


def test_adv12_alias_of_the_supplier_is_screened_too():
    d = qualify(rec(aliases=("Acme Holding Group",)), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is Q.BLOCKED


def test_adv12_watchlist_alias_matching():
    assert WatchlistIndex(WL).hit("Trading Volkov Co") == "W1"


def test_adv12_nothing_in_the_record_can_clear_a_hit():
    """No amount of good data (certs, years, low risk) or claims offsets a watchlist hit."""
    good = rec(name="Acme Holding Group", years_in_business=99, financial_risk=0.0, claimed_certs=("ISO9001",),
               network_status="member", on_time_pct=1.0, quality_score=1.0)
    assert qualify(good, NEED, WatchlistIndex(WL), TODAY).status is Q.BLOCKED


def test_adv12_blocked_outranks_every_other_status():
    d = qualify(rec(name="Acme Holding Group", country="RU", certs=(), capacity_units=None), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is Q.BLOCKED and {"WATCHLIST_HIT", "EXCLUDED_COUNTRY"} <= set(d.reasons)


@pytest.mark.xfail(strict=True, reason="LIMIT-1: compliance/rules.py cutoff 88 on ratio/token_sort: dropping a whole token "
                   "('Acme Holding') from a watchlisted name is not matched (false negative; no partial/subset matching)")
def test_adv12_dropped_token_variant_is_screened():
    assert WatchlistIndex(WL).hit("Acme Holding") is not None


def test_adv12_entity_resolution_never_merges_on_conflicting_facts():
    a = rec(id="A", name="Zenith Metalworks Inc", country="DE", tax_id="DE111")
    same_name_other_country = rec(id="B", name="Zenith Metalworks Inc", country="US", tax_id="US222")
    same_name_other_tax = rec(id="C", name="Zenith Metalworks Inc.", country="DE", tax_id="DE999")
    m = resolve([a, same_name_other_country, same_name_other_tax])
    assert len({m["A"], m["B"], m["C"]}) == 3
    dup = rec(id="D", name="ZENITH METALWORKS", country="DE", tax_id=None)
    assert resolve([a, dup])["D"] == "A"
    other_spelling_same_tax = rec(id="E", name="Completely Renamed Trading", country="DE", tax_id="DE111")
    assert resolve([a, other_spelling_same_tax])["E"] == "A"


def _research_registry(clean_more_complete: bool):
    clean = rec(id="S1", name="Sunrise Components", tax_id="DE777", categories=("pcb",))
    listed = rec(id="S2", name="Acme Holding Group", tax_id="DE777", categories=("pcb",),
                 capacity_units=None if clean_more_complete else 5000, on_time_pct=None if clean_more_complete else 0.9)
    return Registry([clean, listed], WL)


@pytest.mark.parametrize("clean_more_complete", [True, False])
async def test_adv12_duplicate_entity_cannot_hide_a_watchlisted_name(clean_more_complete):
    llm = LLMClient(stub_config(), {"stub": StubProvider()}, MemoryReplayStore())
    tools = ToolRunner(build_buyer_tools(_research_registry(clean_more_complete), llm, SyntheticNews(), "P", TODAY))
    need = K.NeedSpec(id="N", category="pcb", industry="high_tech", volume_units=1000, required_certs=("ISO9001",),
                      max_lead_time_weeks=12)
    res = await tools.call(K.RESEARCH, K.ResearchIn(need=need))
    assert res.ok and res.value.candidates == () and res.value.filtered["blocked"] >= 1


# ------------------------------------------------------------------ ADV-13


async def test_adv13_inflated_claim_never_reaches_the_buyer(bus, sup_a, buyer):
    bus.set_stage(S.QUALIFICATION)
    for claims in (["ISO9999"], ["iso9001"], ["ISO9001 "], ["ISO9001", "IATF16949"], ["ISO9001", "ISO9001​"]):
        res = await sup_a.send(T.DOC_SUBMISSION, "buyer", {"certificates": [{"type": c, "claimed": True} for c in claims]})
        assert getattr(res, "rule_id", None) == "CLAIM_NOT_PROVABLE", claims
    assert buyer.inbox() == []
    ok = await sup_a.send(T.DOC_SUBMISSION, "buyer", {"certificates": [{"type": "ISO9001", "claimed": True}]})
    assert hasattr(ok, "envelope")
    assert hasattr(await sup_a.send(T.DOC_SUBMISSION, "buyer", {"certificates": [{"type": "ISO9999", "claimed": False}]}), "envelope")


def test_adv13_free_text_claim_is_review_not_pass():
    d = qualify(rec(certs=(), claimed_certs=("ISO9001",)), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is Q.REVIEW and "UNVERIFIED_CLAIM:ISO9001" in d.reasons


def test_adv13_missing_certificate_is_rejected():
    d = qualify(rec(certs=()), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is Q.REJECTED and "CERT_MISSING:ISO9001" in d.reasons


def test_adv13_cert_type_matching_is_exact():
    for t in ("iso9001", "ISO 9001", "ISO9001​", " ISO9001"):
        d = qualify(rec(certs=(Cert(t, "cb", TODAY + timedelta(days=500)),)), NEED, WatchlistIndex(WL), TODAY)
        assert d.status is not Q.QUALIFIED, t


@pytest.mark.parametrize("delta,expected", [(-1, Q.REJECTED), (-700, Q.REJECTED), (0, Q.QUALIFIED), (1, Q.QUALIFIED)])
def test_adv13_expiry_boundary(delta, expected):
    d = qualify(rec(certs=(Cert("ISO9001", "cb", TODAY + timedelta(days=delta)),)), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is expected
    if expected is Q.REJECTED:
        assert "CERT_EXPIRED:ISO9001" in d.reasons


def test_adv13_one_expired_cert_among_valid_ones_still_rejects():
    need = replace(NEED, required_certs=("ISO9001", "RoHS"))
    certs = (Cert("ISO9001", "cb", TODAY + timedelta(days=100)), Cert("RoHS", "cb", TODAY - timedelta(days=1)))
    assert qualify(rec(certs=certs), need, WatchlistIndex(WL), TODAY).status is Q.REJECTED


@pytest.mark.parametrize("field", ["capacity_units", "lead_time_weeks", "financial_risk", "years_in_business"])
def test_adv13_missing_mandatory_field_is_review_never_qualified(field):
    d = qualify(rec(**{field: None}), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is Q.REVIEW and any(r.startswith("MISSING_DATA") and field in r for r in d.reasons)


def test_adv13_all_data_missing_is_review():
    d = qualify(rec(capacity_units=None, lead_time_weeks=None, financial_risk=None, years_in_business=None), NEED,
                WatchlistIndex(WL), TODAY)
    assert d.status is Q.REVIEW


@pytest.mark.parametrize("field,value", [("financial_risk", float("inf")),
                                         ("capacity_units", -1), ("years_in_business", -5)])
def test_adv13_garbage_values_are_not_a_pass(field, value):
    d = qualify(rec(**{field: value}), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is not Q.QUALIFIED


def test_adv13_nan_financial_risk_is_not_a_pass():
    d = qualify(rec(financial_risk=float("nan")), NEED, WatchlistIndex(WL), TODAY)
    assert d.status is not Q.QUALIFIED


def test_adv13_hard_rules_beat_good_data():
    assert qualify(rec(forced_labor_flag=True), NEED, WatchlistIndex(WL), TODAY).status is Q.BLOCKED
    assert qualify(rec(subtier_origin=("XJ",)), NEED, WatchlistIndex(WL), TODAY).status is Q.BLOCKED
    assert qualify(rec(country="RU"), NEED, WatchlistIndex(WL), TODAY).status is Q.REJECTED
    assert qualify(rec(categories=("other",)), NEED, WatchlistIndex(WL), TODAY).status is Q.REJECTED
