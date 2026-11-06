"""The payment release shows concrete bank checks, keeps only the last four characters of the account, and cannot be approved while a check fails."""
from api_support import create_and_start, pending, run_to_done, w  # noqa: F401
from sourceloop.policy.bank import BankCheck, check
from test_studio_api import no_network  # noqa: F401


def test_the_checks_compare_what_was_sent_with_what_is_on_record():
    ok = check({"legal_name": "Prime Castings S.A. de C.V.", "bank_account": "DE89 3704 0044 0532 0130 00"}, "Prime Castings S.A. de C.V.")
    assert ok.ok and ok.masked == "3000" and ok.external == "NOT_CONFIGURED" and "DE89" not in str(ok.as_evidence())
    assert dict(check({"legal_name": "prime castings sa de cv", "bank_account": "SYNTH-0000"}, "Prime Castings S.A. de C.V.").checks)["NAME_MATCHES_REGISTRY"]   # punctuation and case do not matter
    wrong = check({"legal_name": "Someone Else Ltd", "bank_account": "SYNTH-0000"}, "Prime Castings S.A. de C.V.")
    assert not wrong.ok and dict(wrong.checks)["NAME_MATCHES_REGISTRY"] is False
    for bad in ({}, {"legal_name": "X", "bank_account": ""}, {"legal_name": "X", "bank_account": "12"}, {"legal_name": "X", "bank_account": "bad account!!"}):
        assert not check(bad, "X").ok


async def test_the_release_carries_the_check_and_never_the_full_account(w):  # noqa: F811
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    left = await pending(w, pid)
    ev = left[0]["evidence"]
    assert left[0]["kind"] == "HOLD_RELEASE" and ev["bank_check"]["ok"] is True and ev["bank_check"]["account_ending"] == "0000"
    assert "SYNTH" not in str(left[0]) and ev["bank_check"]["external_verification"] == "NOT_CONFIGURED"
    s = (await (await w.client("buyer")).get(f"/projects/{pid}/negotiation-summary")).json()
    assert s["payment"]["passed"] is True and s["payment"]["account_ending"] == "0000"
    admin_audit = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    assert any(a["action"] == "bank_checked" and a["outcome"] == "PASS" for a in admin_audit) and "SYNTH-0000" not in str(admin_audit)


async def test_a_failed_check_blocks_the_release_but_declining_still_works(w, monkeypatch):  # noqa: F811
    from sourceloop.orchestrator import runtime
    monkeypatch.setattr(runtime, "bank_check", lambda fields, name: BankCheck(False, (("ACCOUNT_PRESENT", True), ("NAME_MATCHES_REGISTRY", False)), "0000", "NOT_CONFIGURED"))
    pid = await create_and_start(w)
    await run_to_done(w, pid)
    item = (await pending(w, pid))[0]
    b = await w.client("buyer")
    r = await b.post(f"/approvals/{item['id']}/resolve", {"status": "APPROVED", "version": item["version"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BANK_CHECK_FAILED"
    r = await b.post(f"/approvals/{item['id']}/resolve", {"status": "REJECTED", "version": item["version"], "comment": "the account holder name does not match"})
    assert r.status_code == 200
