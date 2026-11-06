"""The shared test sets for agent review: 12 buyer requests, 16 supplier questions (10 answerable from the files, 6 not), 6 contract drafts with a
known deviation from the playbook and 2 clean ones. The expected answers are checked here against the code's own types, so the sets cannot drift.
The supplier-question and contract-review agents do not exist yet: these files are what their first tests will read."""
import json
from pathlib import Path

from sourceloop.data.clauses import DEFAULT_CLAUSES
from sourceloop.data.synthetic import CATEGORIES, CERTS, COUNTRIES
from sourceloop.subagents.need_intake import NeedRead, NeedUnclear, to_fields

F = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((F / name).read_text())


def test_twelve_buyer_requests_expect_what_the_intake_schema_can_hold():
    rs = load("buyer_requests.json")
    assert len(rs) == 12 and len({r["text"] for r in rs}) == 12
    unclear = 0
    for r in rs:
        e = r["expected"]
        read = NeedRead(category=e["category"], volume_units=e["volume_units"], required_certs=e["required_certs"],
                        excluded_countries=e["excluded_countries"], max_lead_time_weeks=e["max_lead_time_weeks"], target_unit_price=e["target_unit_price"])
        assert e["category"] in (*CATEGORIES, "none") and set(e["required_certs"]) <= set(CERTS) and set(e["excluded_countries"]) <= set(COUNTRIES)
        if e["category"] == "none":
            unclear += 1
            try:
                to_fields(read)
                raise AssertionError("an unclear request must not become a need")
            except NeedUnclear:
                pass
        else:
            assert to_fields(read)["category"] == e["category"]
    assert unclear == 2


def test_sixteen_supplier_questions_ten_answerable_six_not():
    qs = load("supplier_questions.json")
    assert len(qs) == 16 and sum(q["answerable"] for q in qs) == 10
    assert all((q["answer_contains"] and q["source"]) if q["answerable"] else (q["answer_contains"] is None and q["source"] is None) for q in qs)


def test_contract_drafts_six_deviate_two_are_clean():
    ds = load("contract_drafts.json")
    assert len(ds) == 8
    clean = [d for d in ds if not d["deviations"]]
    assert len(clean) == 2 and len(ds) - len(clean) == 6
    for d in ds:
        assert set(d["clauses"]) <= set(DEFAULT_CLAUSES) and set(d["deviations"]) <= set(d["clauses"])


async def test_the_fairness_measurement_is_deterministic_and_the_truthful_row_is_neutral():
    from sourceloop.eval.fairness import measure
    a, b = await measure((42,), 2), await measure((42,), 2)
    assert a == b and a["supplier"][0][3] == "+0.000" and a["buyer"][0][3] == "+0.000" and a["supplier"][0][2] == "100%"
