"""Shared helpers for API tests: a throwaway app + SQLite file per test, users per role, CSRF-aware client."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest_asyncio

from sourceloop.api.app import create_app
from sourceloop.api.config import Settings
from sourceloop.api.security import Passwords
from sourceloop.api.service import build_spec, registry, supplier_org_id
from sourceloop.clock import SystemClock
from sourceloop.data import db as dbm
from sourceloop.data.enums import OrgKindEnum
from sourceloop.data.enums import RoleEnum as R
from sourceloop.data.models import Organization, Supplier, User
from sourceloop.orchestrator.scenario import build_project, run_to_completion

PASSWORD = "correct-horse-battery-staple"  # test-only password
API = "/api/v1"
BUYER_ROLES = (R.BUYER, R.COMPLIANCE, R.QUALITY, R.LEGAL, R.FINANCE)


def make_settings(tmp: Path, **kw: Any) -> Settings:
    base = dict(app_env="dev", database_url=f"sqlite+aiosqlite:///{tmp}/t.db", jwt_secret="j" * 40, org_signing_secret="s" * 40,
                default_limit="100000/minute", login_limit="100000/minute", argon_time_cost=1, argon_memory_cost=64,
                argon_parallelism=1, cookie_secure=False, log_level="ERROR")
    base.update(kw)
    return Settings(**base)


class Api:
    """httpx client that behaves like the web app: cookies kept, X-CSRF-Token attached on mutations."""

    def __init__(self, app: Any, csrf: bool = True) -> None:
        self.c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        self.csrf = csrf

    def _h(self, method: str, headers: dict[str, str] | None) -> dict[str, str]:
        h = dict(headers or {})
        tok = self.c.cookies.get("csrf")
        if self.csrf and method != "GET" and tok and "X-CSRF-Token" not in h:
            h["X-CSRF-Token"] = tok
        return h

    async def request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        kw["headers"] = self._h(method, kw.get("headers"))
        return await self.c.request(method, API + path, **kw)

    async def get(self, path: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.request("POST", path, json=json if json is not None else {}, **kw)

    async def put(self, path: str, json: Any = None, **kw: Any) -> httpx.Response:
        return await self.request("PUT", path, json=json if json is not None else {}, **kw)

    async def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        return await self.post("/auth/login", {"email": email, "password": password})

    async def close(self) -> None:
        await self.c.aclose()


@dataclass
class World:
    app: Any
    settings: Settings
    emails: dict[str, str] = field(default_factory=dict)  # key -> email
    ids: dict[str, uuid.UUID] = field(default_factory=dict)
    orgs: dict[str, uuid.UUID] = field(default_factory=dict)
    clients: dict[str, Api] = field(default_factory=dict)
    demo_suppliers: list[str] = field(default_factory=list)

    @property
    def service(self) -> Any:
        return self.app.state.service

    async def client(self, key: str) -> Api:
        if key not in self.clients:
            c = Api(self.app)
            r = await c.login(self.emails[key])
            assert r.status_code == 200, r.text
            self.clients[key] = c
        return self.clients[key]

    async def close(self) -> None:
        for c in self.clients.values():
            await c.close()


async def build_world(tmp: Path, *, fixed_ids: bool = False, **settings_kw: Any) -> World:
    settings = make_settings(tmp, **settings_kw)
    app = create_app(settings)
    await dbm.create_all()
    w = World(app, settings)
    pw = Passwords(settings).hash(PASSWORD)
    base = await run_to_completion(build_project(await build_spec("happy", 0, 42), *registry(42)))
    w.demo_suppliers = list(base.shortlist[:2])
    now = SystemClock().now()
    org_specs = [("acme", "Acme Buyer", OrgKindEnum.BUYER), ("globex", "Globex Buyer", OrgKindEnum.BUYER),
                 ("platform", "Platform", OrgKindEnum.BUYER)]
    async with dbm.session_scope() as s:
        for key, name, kind in org_specs:
            oid = uuid.uuid5(uuid.NAMESPACE_DNS, f"org-{key}") if fixed_ids else uuid.uuid4()
            s.add(Organization(id=oid, name=name, kind=kind, created_at=now))
            w.orgs[key] = oid
        await s.flush()
        for i, sid in enumerate(w.demo_suppliers + ["OUTSIDER"], start=1):
            if sid == "OUTSIDER":
                oid = uuid.uuid4()
                s.add(Organization(id=oid, name="Outsider Supplier", kind=OrgKindEnum.SUPPLIER, created_at=now))
            else:
                oid = supplier_org_id(sid)
                rec = registry(42)[0].get(sid)
                assert rec is not None
                s.add(Organization(id=oid, name=rec.name, kind=OrgKindEnum.SUPPLIER, created_at=now))
            w.orgs[f"sup{i}"] = oid
        await s.flush()
        users: list[tuple[str, R, str]] = [(r.value.lower(), r, "acme") for r in BUYER_ROLES]
        users += [("buyer_b", R.BUYER, "globex"), ("evaluator", R.EVALUATOR, "platform"), ("admin", R.SUPER_ADMIN, "platform"),
                  ("org_admin", R.ADMIN, "acme"), ("sup_admin", R.ADMIN, "sup1"),
                  ("designer", R.DESIGNER, "platform"), ("supplier1", R.SUPPLIER, "sup1"), ("supplier2", R.SUPPLIER, "sup2"),
                  ("outsider", R.SUPPLIER, "sup3")]
        for key, role, org in users:
            uid = uuid.uuid5(uuid.NAMESPACE_DNS, f"user-{key}") if fixed_ids else uuid.uuid4()
            email = f"{key}@sourceloop.test"
            s.add(User(id=uid, email=email, password_hash=pw, role=role, org_id=w.orgs[org], created_at=now))
            w.emails[key], w.ids[key] = email, uid
    # pre-link supplier registry rows to their orgs (what the seed script does)
    async with dbm.session_scope() as s:
        for i, sid in enumerate(w.demo_suppliers, start=1):
            if await s.get(Supplier, sid) is None:
                continue
            (await s.get(Supplier, sid)).org_id = w.orgs[f"sup{i}"]  # type: ignore[union-attr]
    return w


# every human checkpoint is the buyer user's; admins never approve
ROLE_OF_KIND = {k: "buyer" for k in ("REQUIREMENT", "COMPLIANCE_FLAG", "TECHNICAL_QUAL", "AWARD", "CLAUSE", "SIGNATURE", "BANK_DETAILS",
                                     "HOLD_RELEASE", "MANDATE_BREACH", "LOW_CONFIDENCE", "RULE")}


async def pending(w: World, pid: str) -> list[dict]:
    out: list[dict] = []
    for key in ("buyer",):
        c = await w.client(key)
        r = await c.get(f"/approvals?status=PENDING&project_id={pid}")
        assert r.status_code == 200, r.text
        out += r.json()["items"]
    return out


async def resolve_all(w: World, pid: str, status: str = "APPROVED", only: set[str] | None = None) -> int:
    n = 0
    for a in await pending(w, pid):
        if only and a["kind"] not in only:
            continue
        c = await w.client(ROLE_OF_KIND[a["kind"]])
        r = await c.post(f"/approvals/{a['id']}/resolve", {"status": status, "version": a["version"]})
        assert r.status_code == 200, r.text
        n += 1
    return n


async def create_and_start(w: World, scenario: str = "happy") -> str:
    b = await w.client("buyer")
    r = await b.post("/projects", {"title": "PCB sourcing", "need_text": "500 PCB assemblies", "scenario": scenario})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = await b.post(f"/projects/{pid}/start")
    assert r.status_code == 202, r.text
    await w.service.join(pid)
    return pid


async def run_to_done(w: World, pid: str, max_cycles: int = 30) -> dict:
    b = await w.client("buyer")
    for _ in range(max_cycles):
        proj = (await b.get(f"/projects/{pid}")).json()
        if proj["status"] in ("DONE", "STOPPED", "FAILED_RECOVERABLE"):
            return proj
        n = await resolve_all(w, pid, only={k for k in ROLE_OF_KIND if k != "HOLD_RELEASE"})
        await w.service.join(pid)
        if n == 0:
            break
    return (await b.get(f"/projects/{pid}")).json()


@pytest_asyncio.fixture
async def w(tmp_path):
    """A fresh app + database + users for every test. Import into a test module to use."""
    world = await build_world(tmp_path)
    async with world.app.router.lifespan_context(world.app):
        yield world
        await world.close()
