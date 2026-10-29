"""ADV-16: SSRF / egress control. Everything is offline: DNS is faked, HTTP goes through httpx.MockTransport."""
# ruff: noqa: S104  (seeded test RNG / literal 0.0.0.0 as attack input, not security-sensitive)
import ast
import asyncio
import socket
from pathlib import Path

import httpx
import pytest

from adversarial.helpers import VALID
from sourceloop.data.enums import MessageTypeEnum as T
from sourceloop.data.enums import StageEnum as S
from sourceloop.data.synthetic import generate
from sourceloop.net import safe_http
from sourceloop.net.safe_http import MAX_BYTES, EgressBlocked, SafeHttp
from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion

SRC = Path(__file__).resolve().parents[2] / "src" / "sourceloop"
OK = "api.example.test"
REAL_CLIENT = httpx.AsyncClient
DNS: dict[str, list[str]] = {}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    DNS.clear()
    DNS[OK] = ["93.184.216.34"]

    async def getaddrinfo(self, host, port, **kw):
        if host not in DNS:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in DNS[host]]

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "getaddrinfo", getaddrinfo)


def mock_http(monkeypatch, handler):
    real = REAL_CLIENT
    seen: list[dict] = []

    def factory(*a, **kw):
        seen.append(kw)
        kw.pop("transport", None)  # the production client pins the validated IP; tests swap in a mock transport
        return real(*a, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


async def blocked(url, code, http=None):
    with pytest.raises(EgressBlocked) as e:
        await (http or SafeHttp({OK})).get(url)
    assert e.value.code == code, (url, e.value.code)


# ---- host / URL forms
@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/", "http://metadata.google.internal/", "http://100.100.100.200/",
    "http://localhost/", "http://127.0.0.1/", "http://0.0.0.0/", "http://[::1]/", "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:169.254.169.254]/", "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://127.1/",
    "http://017700000001/", "http://0x7f.0x0.0x0.0x1/", "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/",
    "http://[fd00:ec2::254]/", "http://[fe80::1]/", f"http://{OK}.evil.test/", f"http://evil.test/{OK}",
    f"http://{OK}@evil.test/", f"http://{OK}:80@169.254.169.254/", f"http://evil.test#@{OK}/",
    f"http://{OK}.", "http://evil.test/", "http://" + "a" * 300 + ".test/",
])
async def test_adv16_non_allowlisted_hosts_never_resolve_or_connect(url):
    await blocked(url, "HOST_NOT_ALLOWED")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://api.example.test/", "ftp://api.example.test/", "javascript:alert(1)",
                                 "data:text/plain,hi", "//api.example.test/x", "api.example.test/x", "", "http://", "https:///x", "ws://api.example.test"])
async def test_adv16_bad_schemes_and_malformed_urls(url):
    await blocked(url, "BAD_URL")


@pytest.mark.parametrize("url", [f"HTTP://{OK.upper()}/x", f"http://{OK}:8080/x"])
async def test_adv16_allowlisted_host_forms_still_pass_the_dns_check(url, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, text="ok"))
    assert (await SafeHttp({OK}).get(url)).text == "ok"


# ---- DNS rebinding style: allow-listed name that resolves to a forbidden address
@pytest.mark.parametrize("ip,code", [
    ("169.254.169.254", "IP_BLOCKED"), ("100.100.100.200", "IP_BLOCKED"), ("169.254.1.1", "IP_BLOCKED"),
    ("0.0.0.0", "IP_BLOCKED"), ("224.0.0.1", "IP_BLOCKED"), ("fe80::1", "IP_BLOCKED"), ("::ffff:169.254.169.254", "IP_BLOCKED"),
    ("fd00:ec2::254", "IP_BLOCKED"), ("127.0.0.1", "PRIVATE_IP_BLOCKED"), ("::1", "ANY"),
    ("10.1.2.3", "PRIVATE_IP_BLOCKED"), ("172.31.255.255", "PRIVATE_IP_BLOCKED"), ("192.168.0.10", "PRIVATE_IP_BLOCKED"),
    ("fd12:3456::1", "PRIVATE_IP_BLOCKED"), ("::ffff:10.0.0.1", "PRIVATE_IP_BLOCKED"), ("::ffff:127.0.0.1", "PRIVATE_IP_BLOCKED"),
])
async def test_adv16_allowlisted_name_resolving_to_internal_address_is_blocked(ip, code):
    DNS[OK] = [ip]
    if code == "ANY":  # both IP_BLOCKED and PRIVATE_IP_BLOCKED are acceptable refusals
        with pytest.raises(EgressBlocked):
            await SafeHttp({OK}).get(f"http://{OK}/")
        return
    await blocked(f"http://{OK}/", code)


async def test_adv16_any_forbidden_answer_in_a_multi_record_response_blocks():
    DNS[OK] = ["93.184.216.34", "169.254.169.254"]
    await blocked(f"http://{OK}/", "IP_BLOCKED")
    DNS[OK] = ["10.0.0.9", "93.184.216.34"]
    await blocked(f"http://{OK}/", "PRIVATE_IP_BLOCKED")


async def test_adv16_dns_is_re_resolved_on_every_request(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, text="ok"))
    http = SafeHttp({OK})
    assert (await http.get(f"http://{OK}/")).status_code == 200
    DNS[OK] = ["169.254.169.254"]  # rebinding after the first successful call
    await blocked(f"http://{OK}/", "IP_BLOCKED", http)


async def test_adv16_dns_failure_is_a_block_not_a_fallthrough():
    await blocked("http://unresolvable.example.test/", "HOST_NOT_ALLOWED")
    del DNS[OK]
    await blocked(f"http://{OK}/", "DNS_FAILED")


@pytest.mark.parametrize("literal", ["169.254.169.254", "10.0.0.5", "127.0.0.1", "[::1]"])
async def test_adv16_allowlisting_an_ip_literal_does_not_open_internal_ranges(literal):
    host = literal.strip("[]")
    DNS[host] = [host]
    with pytest.raises(EgressBlocked):
        await SafeHttp({host}).get(f"http://{literal}/")


async def test_adv16_local_hosts_are_loopback_only_never_metadata(monkeypatch):
    DNS["ollama.local"] = ["127.0.0.1"]
    mock_http(monkeypatch, lambda req: httpx.Response(200, text="ok"))
    local = SafeHttp(set(), local_hosts={"ollama.local"})
    assert (await local.get("http://ollama.local/api/tags")).status_code == 200
    DNS["ollama.local"] = ["169.254.169.254"]
    await blocked("http://ollama.local/", "IP_BLOCKED", local)
    DNS["ollama.local"] = ["100.100.100.200"]
    await blocked("http://ollama.local/", "IP_BLOCKED", local)
    await blocked("http://127.0.0.1/", "HOST_NOT_ALLOWED", local)  # only the configured NAME is local
    DNS[OK] = ["127.0.0.1"]
    await blocked(f"http://{OK}/", "PRIVATE_IP_BLOCKED", SafeHttp({OK}, local_hosts={"ollama.local"}))


@pytest.mark.parametrize("ip", ["100.64.0.1", "100.127.255.254"])
async def test_adv16_shared_address_space_is_blocked(ip):
    DNS[OK] = [ip]
    with pytest.raises(EgressBlocked):
        await SafeHttp({OK}).get(f"http://{OK}/")


# ---- redirects
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_adv16_redirects_are_never_followed(status, monkeypatch):
    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(status, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    seen = mock_http(monkeypatch, handler)
    await blocked(f"http://{OK}/", "REDIRECT_BLOCKED")
    assert hits == [f"http://{OK}/"] and seen[0]["follow_redirects"] is False


async def test_adv16_redirect_to_allowlisted_host_is_still_not_followed(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(302, headers={"Location": f"http://{OK}/other"}))
    await blocked(f"http://{OK}/", "REDIRECT_BLOCKED")


# ---- size cap
async def test_adv16_response_over_2mb_is_rejected_exactly_at_the_cap(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, content=b"x" * (MAX_BYTES + 1)))
    await blocked(f"http://{OK}/", "RESPONSE_TOO_LARGE")
    mock_http(monkeypatch, lambda req: httpx.Response(200, content=b"x" * MAX_BYTES))
    assert len((await SafeHttp({OK}).get(f"http://{OK}/")).content) == MAX_BYTES


async def test_adv16_cap_is_enforced_while_streaming_not_after(monkeypatch):
    pulled = 0
    chunk = b"x" * 65536

    async def body():
        nonlocal pulled
        for _ in range(400):  # 25 MB offered
            pulled += len(chunk)
            yield chunk

    mock_http(monkeypatch, lambda req: httpx.Response(200, content=body()))
    with pytest.raises(EgressBlocked):
        await SafeHttp({OK}).get(f"http://{OK}/")
    assert pulled <= MAX_BYTES + 2 * len(chunk)


async def test_adv16_post_goes_through_the_same_checks(monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, text="ok"))
    with pytest.raises(EgressBlocked):
        await SafeHttp({OK}).post("http://169.254.169.254/", json={"a": 1})


def test_adv16_update_replaces_the_allowlist():
    h = SafeHttp({OK})
    h.update({"other.test"}, set())
    assert h.allowed == {"other.test"} and not h.local


# ---- supplier-supplied URLs are never fetched
def test_adv16_only_safe_http_may_touch_the_network():
    """Rule 19: no module besides net/safe_http.py may import an HTTP client or a raw socket."""
    banned = {"httpx", "requests", "urllib3", "aiohttp", "http.client", "urllib.request", "socket", "websockets"}
    offenders = []
    for f in sorted(SRC.rglob("*.py")):
        if f == SRC / "net" / "safe_http.py":
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            if any(x == b or x.startswith(b + ".") for x in names for b in banned):
                offenders.append(f"{f.relative_to(SRC)}:{n.lineno}")
    assert not offenders, offenders


URLS = ["http://169.254.169.254/latest/meta-data/", "https://evil.test/x", "file:///etc/passwd", "gopher://127.0.0.1:6379/_FLUSHALL"]


async def test_adv16_urls_in_supplier_controlled_fields_cause_no_fetch(bus, sup_a, monkeypatch):
    calls = []

    async def spy(self, method, url, **kw):
        calls.append(url)
        raise AssertionError("egress attempted")

    monkeypatch.setattr(SafeHttp, "request", spy)
    sends = [
        (S.DISCOVERY, T.INTEREST_REPLY, {"interested": True, "reason_code": URLS[0][:40]}),
        (S.QUALIFICATION, T.DOC_SUBMISSION, {"certificates": [], "answers": {f"k{i}": u for i, u in enumerate(URLS)}}),
        (S.SOURCING, T.OFFER, {**VALID[T.OFFER], "note": " ".join(URLS)[:300]}),
        (S.CONTRACTING, T.REDLINE, {"clause_key": "payment_terms", "action": "accept"}),
        (S.ONBOARDING, T.ONBOARD_DATA, {"fields": {"website": URLS[1], "logo": URLS[0], "docs": URLS[2]}}),
    ]
    for stage, t, payload in sends:
        bus.set_stage(stage)
        await sup_a.send(t, "buyer" if t is not T.ONBOARD_DATA else "orchestrator", payload)
    assert calls == []


async def test_adv16_full_run_with_hostile_supplier_data_makes_no_network_calls(monkeypatch):
    """The autouse no_network fixture fails the test on any socket/DNS use; additionally SafeHttp must be untouched."""
    calls = []

    async def spy(self, method, url, **kw):
        calls.append(url)
        raise AssertionError("egress attempted")

    monkeypatch.setattr(SafeHttp, "request", spy)
    vis, full, _ = generate(42)
    from dataclasses import replace
    poisoned = {r.id: replace(r, aliases=(*r.aliases, "http://169.254.169.254/"), claimed_certs=(*r.claimed_certs, "http://evil.test/"))
                for r in full.suppliers[:50]}
    full.suppliers[:50] = [poisoned[r.id] if r.id in poisoned else r for r in full.suppliers[:50]]
    rt = build_project(ScenarioSpec(), vis, full)
    await run_to_completion(rt)
    assert calls == []


def test_adv16_module_exports_are_unchanged():
    assert safe_http.MAX_BYTES == 2 * 1024 * 1024
