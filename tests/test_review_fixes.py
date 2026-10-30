"""Regression tests for the review fixes F1/F2/F3/F8/F9/F10/F13/F15/F18/F19."""
import asyncio
import socket

import httpx
import pytest
from cryptography.fernet import Fernet

from api_support import ROLE_OF_KIND, create_and_start, pending, resolve_all, w  # noqa: F401
from sourceloop.api.config import load_settings
from sourceloop.audit.canonical import dumps, sha256_hex
from sourceloop.net import safe_http
from sourceloop.net.safe_http import SafeHttp


# ---- F1 / F18: prod secrets
def _env(monkeypatch, **kw):
    for k in ("JWT_SECRET", "ORG_SIGNING_SECRET", "SOURCELOOP_VAULT_KEY", "CONFIG_ENCRYPTION_KEY", "APP_ENV", "CORS_ORIGINS", "COOKIE_SECURE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_prod_requires_valid_fernet_and_signing_secret(monkeypatch):
    jwt, sign, fk = "j" * 40, "s" * 40, Fernet.generate_key().decode()
    _env(monkeypatch, APP_ENV="prod", JWT_SECRET=jwt, ORG_SIGNING_SECRET=sign)
    with pytest.raises(RuntimeError, match="VAULT_KEY"):
        load_settings()
    _env(monkeypatch, APP_ENV="prod", JWT_SECRET=jwt, ORG_SIGNING_SECRET=sign, SOURCELOOP_VAULT_KEY="not-a-key")
    with pytest.raises(RuntimeError, match="VAULT_KEY"):
        load_settings()
    _env(monkeypatch, APP_ENV="prod", JWT_SECRET=jwt, SOURCELOOP_VAULT_KEY=fk)
    with pytest.raises(RuntimeError, match="ORG_SIGNING_SECRET"):
        load_settings()
    _env(monkeypatch, APP_ENV="prod", JWT_SECRET=jwt, ORG_SIGNING_SECRET=sign, CONFIG_ENCRYPTION_KEY=fk,
         CORS_ORIGINS="https://app.example.test")  # legacy alias
    s = load_settings()
    assert s.config_encryption_key == fk and s.org_signing_secret == sign
    _env(monkeypatch, JWT_SECRET=jwt)  # dev keeps the fallbacks
    assert load_settings().org_signing_secret == jwt


# ---- F2 / F3: approvals
async def _to_flag(w, pid):  # noqa: F811
    """Drive the run to the buyer's compliance-flag checkpoint (the checkpoint whose edit carries a payload and that exists by default)."""
    for _ in range(10):
        items = [a for a in await pending(w, pid) if a["kind"] == "COMPLIANCE_FLAG"]
        if items:
            return items[0]
        await resolve_all(w, pid)
        await w.service.join(pid)
    raise AssertionError("no COMPLIANCE_FLAG approval")


async def test_malformed_edit_is_422_and_project_stays_resumable(w):  # noqa: F811
    pid = await create_and_start(w)
    a = await _to_flag(w, pid)
    b = await w.client("buyer")
    url = f"/approvals/{a['id']}/resolve"
    base = {"status": "EDITED", "version": a["version"], "comment": "clearing one supplier here"}
    for bad in ({"include": 5}, {"include": [1]}, {"include": ["NOT-A-SUPPLIER"]}, {"include": [], "x": 1}, {"include": ["x" * 500]}):
        r = await b.post(url, {**base, "edit": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_EDIT", bad
    r = await b.post(url, {"status": "APPROVED", "version": a["version"], "edit": {"include": []}})
    assert r.status_code == 422  # edits only with EDITED
    # still pending, same version, and resolvable afterwards
    again = [x for x in await pending(w, pid) if x["id"] == a["id"]]
    assert again and again[0]["version"] == a["version"]
    ok = await b.post(url, {"status": "APPROVED", "version": a["version"]})
    assert ok.status_code == 200


async def test_valid_edit_accepted_and_audit_binds_edit_hash(w):  # noqa: F811
    pid = await create_and_start(w)
    a = await _to_flag(w, pid)
    b = await w.client("buyer")
    cand = next(iter(a["evidence"]["flagged"]))
    edit, comment = {"include": [cand]}, "clearing one flagged supplier after review"
    r = await b.post(f"/approvals/{a['id']}/resolve", {"status": "EDITED", "version": a["version"], "comment": comment, "edit": edit})
    assert r.status_code == 200, r.text
    ev = (await (await w.client("evaluator")).get(f"/projects/{pid}/audit")).json()
    items = ev["items"] if isinstance(ev, dict) else ev
    rec = [e for e in items if e["action"] == "approval_resolved" and e["rule_id"] == "COMPLIANCE_FLAG"][-1]
    evi = rec["evidence"][0]
    assert evi["decision_hash"] == sha256_hex(dumps({"edit": edit, "comment": comment}))
    assert evi["edit_keys"] == ["include"] and evi["decision"] == "EDITED"


# ---- F8: escalations scoped by role
async def test_escalations_are_role_scoped(w):  # noqa: F811
    pid = await create_and_start(w)
    await _to_flag(w, pid)
    b = await w.client("buyer")
    bk = {x["kind"] for x in (await b.get(f"/projects/{pid}/escalations")).json()["items"]}
    assert "COMPLIANCE_FLAG" in bk and all(ROLE_OF_KIND[k] == "buyer" for k in bk)
    qk = {x["kind"] for x in (await (await w.client("quality")).get(f"/projects/{pid}/escalations")).json()["items"]}
    assert "COMPLIANCE_FLAG" not in qk       # a role that no longer owns any checkpoint sees none
    ek = {x["kind"] for x in (await (await w.client("evaluator")).get(f"/projects/{pid}/escalations")).json()["items"]}
    assert "COMPLIANCE_FLAG" in ek


# ---- F9 / F19: bus
async def test_waiver_is_single_use_and_scoped():
    from sourceloop.bus.bus import MessageBus
    b = MessageBus.__new__(MessageBus)
    b.waivers = set()
    key = b.grant_waiver("LOCKED_CLAUSE", "sup", "ip")
    assert not b._consume_waiver("LOCKED_CLAUSE", "other", "ip")
    assert not b._consume_waiver("LOCKED_CLAUSE", "sup", "liability")
    assert b._consume_waiver("LOCKED_CLAUSE", "sup", "ip")
    assert not b._consume_waiver("LOCKED_CLAUSE", "sup", "ip") and key not in b.waivers


def test_runtime_revokes_waiver_in_finally():
    import inspect

    from sourceloop.orchestrator import runtime
    src = inspect.getsource(runtime)
    i = src.index("grant_waiver")
    assert "try:" in src[i:i + 200] and "finally:" in src[i:i + 500] and "revoke_waiver" in src[i:i + 600]


# ---- F10: pinned IP, no env proxies
async def test_safe_http_pins_validated_ip_and_ignores_env(monkeypatch):
    async def gai(self, host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "getaddrinfo", gai)
    seen = {}
    real = httpx.AsyncClient

    def factory(*a, **kw):
        seen.update(kw)
        t = kw.pop("transport")
        assert isinstance(t, safe_http._PinnedTransport) and t._ip == "93.184.216.34"
        return real(*a, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")), **kw)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    assert (await SafeHttp({"api.example.test"}).get("http://api.example.test/")).text == "ok"
    assert seen["trust_env"] is False


# ---- F13: lockout is uniform
async def test_locked_and_wrong_password_are_indistinguishable(w):  # noqa: F811
    from api_support import Api
    c = Api(w.app)
    wrong = await c.login(w.emails["legal"], "wrong-password-value")
    for _ in range(5):
        await c.login(w.emails["legal"], "wrong-password-value")
    locked = await c.login(w.emails["legal"])
    assert locked.status_code == wrong.status_code == 401
    assert locked.json()["error"]["code"] == wrong.json()["error"]["code"] and "retry-after" not in locked.headers
    await c.close()


# ---- F15: chunked body cap
async def test_chunked_body_over_cap_is_413(w):  # noqa: F811
    async def gen():
        for _ in range(3):
            yield b"x" * 600_000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=w.app), base_url="http://testserver") as c:
        r = await c.post("/api/v1/auth/login", content=gen(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
