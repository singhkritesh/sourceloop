"""ADV-06: mandate leakage probe.

Run full projects (cooperative, below-floor attacker, inflated claims, no-deal) and scan every surface the other
side could ever see for the numeric thresholds of the counterparty mandate."""
import json
import logging
from decimal import Decimal

import pytest

from adversarial.helpers import blob, number_forms
from sourceloop.agents.supplier.agent import SupplierProfile
from sourceloop.bus.bus import AgentChannel, MessageBus, Rejected
from sourceloop.data.enums import MessageTypeEnum, ProjectStatusEnum
from sourceloop.data.synthetic import generate
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
from sourceloop.policy.mandates import overlap

VIS, FULL, KEY = generate(42)


async def _scan_project(spec: ScenarioSpec, monkeypatch, caplog, capsys):
    results: list = []
    orig = MessageBus.submit

    async def spy(self, env, cap=None):
        res = await orig(self, env, cap)
        results.append((env.from_agent, env.to_agent, res))
        return res

    monkeypatch.setattr(MessageBus, "submit", spy)
    caplog.set_level(logging.DEBUG)
    rt = build_project(spec, VIS, FULL)
    st = await run_to_completion(rt)
    return rt, st, results, caplog.text + capsys.readouterr().out


def _legit_prices(rt) -> set[str]:
    out = set()
    for e in rt.bus.log:
        if "unit_price" in e.payload:
            out |= number_forms(Decimal(str(e.payload["unit_price"])))
    return out


def _supplier_secrets(rt, legit: set[str]) -> dict[str, set[str]]:
    secrets = {}
    for sid, s in rt.suppliers.items():
        forms = number_forms(s.mandate.min_unit_price) | number_forms(s.mandate.unit_cost)
        secrets[sid] = {f for f in forms if f not in legit}
    return secrets


def _buyer_secrets(rt, legit: set[str]) -> set[str]:
    forms = number_forms(rt.buyer_mandate.max_unit_price)
    if rt.buyer_mandate.target_unit_price:
        forms |= number_forms(rt.buyer_mandate.target_unit_price)
    return {f for f in forms if f not in legit}


SCENARIOS = {
    "cooperative": lambda: ScenarioSpec(),
    "no_deal": lambda: ScenarioSpec(buyer_max_factor=Decimal("0.5"), buyer_target_factor=Decimal("0.4")),
    "stubborn": lambda: ScenarioSpec(buyer_max_factor=Decimal("1.2"), buyer_target_factor=Decimal("0.8")),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
async def test_adv06_no_counterparty_threshold_on_any_surface(name, monkeypatch, caplog, capsys):
    rt, st, results, logs = await _scan_project(SCENARIOS[name](), monkeypatch, caplog, capsys)
    legit = _legit_prices(rt)
    sup_secrets = set().union(*_supplier_secrets(rt, legit).values())
    buyer_secrets = _buyer_secrets(rt, legit)
    assert sup_secrets and buyer_secrets

    # --- buyer-visible surfaces: must not contain any supplier floor / cost
    audit_text = json.dumps([r.evidence for r in await rt.audit.store.read("P1")], default=str)
    audit_all = blob([r for r in await rt.audit.store.read("P1")])
    approvals = blob(rt.approvals.all("P1"))
    rejected_text = " ".join(repr(r) for _, _, r in results if isinstance(r, Rejected))
    buyer_inbox = blob([e.payload for e in rt.bus.log if e.to_agent == "buyer"])
    tools = blob([v.model_dump(mode="json") for v in rt.buyer.tools._cache.values()])
    state = blob({"qual": st.qual, "ranking": st.ranking, "dropped": st.dropped, "deals": {k: v["price"] for k, v in st.deals.items()}})
    for surface_name, text in (("audit_evidence", audit_text), ("audit_all", audit_all), ("approvals", approvals),
                               ("rejected", rejected_text), ("buyer_inbox", buyer_inbox), ("buyer_tools", tools),
                               ("run_state", state), ("logs", logs)):
        for secret in sup_secrets:
            assert secret not in text, f"supplier threshold {secret} leaked via {surface_name}"

    # --- supplier-visible surfaces: must not contain the buyer's ceiling / target
    sup_inbox = blob([e.payload for e in rt.bus.log if e.to_agent.startswith("supplier:")])
    sup_tools = blob([v.model_dump(mode="json") for s in rt.suppliers.values() for v in s.agent.tools._cache.values()])
    sup_rejected = " ".join(repr(r) for f, _, r in results if f.startswith("supplier:") and isinstance(r, Rejected))
    for surface_name, text in (("supplier_inbox", sup_inbox), ("supplier_tools", sup_tools), ("supplier_rejected", sup_rejected)):
        for secret in buyer_secrets:
            assert secret not in text, f"buyer threshold {secret} leaked via {surface_name}"


async def test_adv06_below_floor_attack_leaks_only_a_rule_id(monkeypatch, caplog, capsys):
    base = build_project(ScenarioSpec(), VIS, FULL)
    victim = (await run_to_completion(base)).shortlist[0]
    rt, st, results, logs = await _scan_project(ScenarioSpec(profiles={victim: SupplierProfile(attempt_below_floor=True)}),
                                                monkeypatch, caplog, capsys)
    blocked = [r for f, _, r in results if f == f"supplier:{victim}" and isinstance(r, Rejected) and r.rule_id == "PRICE_BELOW_FLOOR"]
    assert blocked
    sm = rt.suppliers[victim].mandate
    attempted = (sm.min_unit_price * Decimal("0.9")).quantize(Decimal("0.0001"))
    everything = blob(rt.approvals.all("P1")) + blob(await rt.audit.store.read("P1")) + logs
    for secret in number_forms(sm.min_unit_price) | number_forms(attempted) | number_forms(sm.unit_cost):
        assert secret not in everything
    for r in blocked:  # the object returned to the offender is a code, never a number
        assert not any(ch.isdigit() for ch in repr(r))


async def test_adv06_probing_supplier_learns_nothing_numeric_about_the_buyer(bus, buyer, sup_a):
    """A supplier binary-searches for the buyer ceiling with COUNTER/ACCEPT/OFFER traffic. Every verdict it can observe
    concerns ITS OWN mandate; verdicts for the buyer's mandate go only to the buyer."""
    seen = []
    for i in range(30):
        res = await sup_a.send(MessageTypeEnum.OFFER, "buyer",
                               {"round": 1 + i % 4, "unit_price": str(Decimal("92") + Decimal(i) * Decimal("3.7")),
                                "lead_time_weeks": 8, "capacity_units": 100})
        seen.append(repr(res.__class__.__name__ + str(getattr(res, "rule_id", ""))))
    text = " ".join(seen)
    assert "120" not in text and "PRICE_ABOVE_MANDATE" not in text


def test_adv06_overlap_probe_returns_a_bare_bool(buyer_mandate, supplier_mandate):
    assert overlap(buyer_mandate, supplier_mandate) is True
    assert overlap(buyer_mandate.model_copy(update={"max_unit_price": Decimal("1")}), supplier_mandate) is False


def test_adv06_rejected_has_no_free_text_field():
    assert set(Rejected.__dataclass_fields__) == {"code", "rule_id", "action"}


async def test_adv06_exception_strings_carry_codes_not_numbers(bus, buyer, sup_a, ring):
    from sourceloop.data.enums import ApprovalKindEnum, ApprovalStatusEnum, RoleEnum
    from sourceloop.net.safe_http import EgressBlocked, SafeHttp
    from sourceloop.orchestrator.approvals import ApprovalError, ApprovalService
    from sourceloop.orchestrator.transitions import IllegalTransition, check_status

    svc = ApprovalService(bus.audit)
    a = await svc.ensure("p1", ApprovalKindEnum.AWARD, "k", ("R",), {"x": 1})
    errs: list[str] = []
    for fn in (lambda: svc.resolve(a.id, ApprovalStatusEnum.APPROVED, "u", RoleEnum.ADMIN),
               lambda: svc.resolve(a.id, ApprovalStatusEnum.REJECTED, "u", RoleEnum.BUYER, "x")):
        try:
            await fn()
        except ApprovalError as e:
            errs.append(str(e))
    try:
        check_status(ProjectStatusEnum.DONE, ProjectStatusEnum.RUNNING)
    except IllegalTransition as e:
        errs.append(str(e))
    try:
        await SafeHttp({"ok.test"})._check("http://169.254.169.254/")
    except EgressBlocked as e:
        errs.append(str(e))
    assert errs and all(len(e) < 40 for e in errs)
    for forbidden in ("80", "92", "120", "mandate"):
        assert not any(forbidden in e for e in errs)


def test_adv06_channel_surface_has_no_mandate_accessors(bus, buyer):
    surface = {a for a in dir(buyer) if not a.startswith("_")}
    assert not any("mandate" in a or "key" in a for a in surface)
    assert isinstance(buyer, AgentChannel)


def test_adv06_visible_registry_has_no_supplier_cost():
    assert all(s.unit_cost is None for s in VIS.suppliers)
