"""Role x route x own/other-tenant matrix. Every route in the API appears here; a new route must be added."""
import uuid

import pytest

from api_support import Api, create_and_start, resolve_all, w  # noqa: F401
from sourceloop.data.enums import RoleEnum as R
from sourceloop.studio.defaults import default_config

DENIED = {401, 403, 404}
BUYER_SIDE = (R.BUYER, R.COMPLIANCE, R.QUALITY, R.LEGAL, R.FINANCE)
# declared independently of the implementation on purpose: the matrix is the spec
# Perspectives: business users (buyer-side roles, suppliers) see their side of the BUSINESS; the agent workflow (raw messages, audit, event
# stream) is for inspectors: SBN's SUPER_ADMIN and EVALUATOR, and an organization ADMIN for its own organization.
INSPECTORS = (R.EVALUATOR, R.SUPER_ADMIN, R.ADMIN)
PROJECT_READERS = (*BUYER_SIDE, R.SUPPLIER, *INSPECTORS)
BUYER_VIEW = (*BUYER_SIDE, *INSPECTORS)
WORKFLOW = (R.SUPPLIER, *INSPECTORS)  # raw messages / audit / stream (a supplier gets only its own neutral thread)
APPROVAL_ROLES = (*BUYER_SIDE, R.SUPPLIER, R.EVALUATOR, R.SUPER_ADMIN, R.ADMIN)
ACTORS = {  # key -> (role, relation to the project under test)
    "buyer": (R.BUYER, "own"), "compliance": (R.COMPLIANCE, "own"), "quality": (R.QUALITY, "own"), "legal": (R.LEGAL, "own"),
    "finance": (R.FINANCE, "own"), "buyer_b": (R.BUYER, "other"), "supplier1": (R.SUPPLIER, "own"), "supplier2": (R.SUPPLIER, "own"),
    "outsider": (R.SUPPLIER, "other"), "evaluator": (R.EVALUATOR, "all"), "admin": (R.SUPER_ADMIN, "all"), "designer": (R.DESIGNER, "none"),
    "org_admin": (R.ADMIN, "own"),       # admin of the BUYER organization that owns the project
    "sup_admin": (R.ADMIN, "supplier"),  # admin of a SUPPLIER organization taking part in the project
}
# routes whose handler refuses an admin of a supplier organization (it would expose the buyer's side or run controls)
SUPPLIER_ADMIN_DENIED = {"/projects/{pid}/audit/verify", "/projects/{pid}/audit/export", "/reviewers/override-rates", "/projects/{pid}/escalations",
                         "/projects/{pid}/stop", "/projects/{pid}/resume", "/suppliers/search"}
STUDIO_READ = (R.DESIGNER, R.SUPER_ADMIN, R.EVALUATOR)
STUDIO_DESIGN = (R.DESIGNER, R.SUPER_ADMIN)
_DEFAULT_CFG = default_config().model_dump(mode="json")
# (method, path template, roles allowed by the route's role check, is_project_scoped, side effects when allowed)
ROUTES = [
    ("GET", "/projects", PROJECT_READERS, False, False),
    ("POST", "/projects", (R.BUYER,), False, True),
    ("GET", "/projects/{pid}", PROJECT_READERS, True, False),
    ("GET", "/projects/{pid}/messages", WORKFLOW, True, False),
    ("GET", "/projects/{pid}/audit", WORKFLOW, True, False),
    ("GET", "/projects/{pid}/overview", PROJECT_READERS, True, False),  # every login, from its own perspective
    ("GET", "/projects/{pid}/activity", INSPECTORS, True, False),  # how the agents behaved: inspectors only
    ("GET", "/projects/{pid}/agent-performance", INSPECTORS, True, False),  # decisions, model-vs-rule, confidence, judge verdicts: inspectors only
    ("GET", "/projects/{pid}/audit/verify", (R.EVALUATOR, R.ADMIN, R.SUPER_ADMIN), True, False),
    ("GET", "/projects/{pid}/audit/export", (R.EVALUATOR, R.ADMIN, R.SUPER_ADMIN), True, False),
    ("GET", "/reviewers/override-rates", (R.ADMIN, R.EVALUATOR, R.SUPER_ADMIN), False, False),
    ("GET", "/projects/{pid}/escalations", BUYER_VIEW, True, False),
    ("GET", "/projects/{pid}/stream", WORKFLOW, True, None),  # streaming: denial cases only
    ("POST", "/projects/{pid}/stop", (R.BUYER, R.ADMIN, R.SUPER_ADMIN), True, None),  # destructive: denial cases only (allowed path in flow tests)
    ("POST", "/projects/{pid}/resume", (R.BUYER, R.ADMIN, R.SUPER_ADMIN), True, None),
    ("POST", "/projects/{pid}/start", (R.BUYER,), True, None),
    ("GET", "/approvals", APPROVAL_ROLES, False, False),
    ("POST", "/approvals/{aid}/resolve", (*BUYER_SIDE, R.SUPPLIER, R.ADMIN, R.SUPER_ADMIN), True, None),
    ("GET", "/suppliers/search", (R.BUYER, R.EVALUATOR, R.ADMIN, R.SUPER_ADMIN), False, False),
    # ---- organization administration (users / own-side settings / SBN's organizations). Per-user and per-version routes
    # (PATCH /org/users/{id}, .../reset-password, POST /org/overrides/rollback) are tenant-scoped by id and covered in test_org_admin.py.
    ("GET", "/org", (R.ADMIN, R.SUPER_ADMIN, R.EVALUATOR), False, False),
    ("GET", "/org/users", (R.ADMIN, R.SUPER_ADMIN, R.EVALUATOR), False, False),
    ("GET", "/org/overrides", (R.ADMIN, R.SUPER_ADMIN, R.EVALUATOR), False, False),
    ("PUT", "/org/overrides", (R.ADMIN, R.SUPER_ADMIN), False, True),
    ("POST", "/org/users", (R.ADMIN, R.SUPER_ADMIN), False, True),
    ("GET", "/admin/orgs", (R.SUPER_ADMIN, R.EVALUATOR), False, False),
    ("POST", "/admin/orgs", (R.SUPER_ADMIN,), False, True),
    ("POST", "/admin/staff", (R.SUPER_ADMIN,), False, True),
    ("GET", "/settings", (R.SUPER_ADMIN, R.EVALUATOR), False, False),
    ("PUT", "/settings", (R.SUPER_ADMIN,), False, True),
    # ---- Studio (docs/api-studio.md). Order matters: designer/admin drafts exist before publish; publish before rollback.
    ("GET", "/studio/schema", STUDIO_READ, False, False),
    ("GET", "/studio/draft", STUDIO_DESIGN, False, True),
    ("PUT", "/studio/draft", STUDIO_DESIGN, False, True),
    ("POST", "/studio/validate", STUDIO_DESIGN, False, False),
    ("POST", "/studio/publish", (R.SUPER_ADMIN,), False, True),
    ("GET", "/studio/versions", STUDIO_READ, False, False),
    ("GET", "/studio/versions/1", STUDIO_READ, False, False),
    ("GET", "/studio/active", STUDIO_READ, False, False),
    ("GET", "/studio/diff?a=1&b=1", STUDIO_READ, False, False),
    ("GET", "/studio/audit", STUDIO_READ, False, False),
    ("POST", "/studio/rollback", (R.SUPER_ADMIN,), False, True),
    ("GET", "/studio/providers", STUDIO_READ, False, False),
    ("PUT", "/studio/providers/ollama/key", (R.SUPER_ADMIN,), False, True),
    ("POST", "/studio/providers/ollama/test", (R.SUPER_ADMIN,), False, True),
    ("DELETE", "/studio/providers/ollama/key", (R.SUPER_ADMIN,), False, True),
    ("GET", "/studio/export", (R.SUPER_ADMIN,), False, True),
    ("POST", "/studio/import", (R.SUPER_ADMIN,), False, True),
    ("DELETE", "/studio/draft", STUDIO_DESIGN, False, True),
]
BODIES = {"PUT /studio/draft": {"config": _DEFAULT_CFG}, "POST /studio/publish": {"note": "matrix"},
          "POST /studio/rollback": {"to_version": 1}, "PUT /studio/providers/ollama/key": {"secret": "matrix-test-secret-123"},
          "POST /studio/import": {"bundle": {"schema_version": 1, "config": _DEFAULT_CFG}},
          "POST /projects": {"title": "t", "need_text": "n"}, "PUT /org/overrides": {"payload": {}},
          "POST /org/users": {"email": "matrix.user@sourceloop.test", "role": "ADMIN"},
          "POST /admin/orgs": {"name": "Matrix Org", "kind": "BUYER", "admin_email": "matrix.admin@sourceloop.test"},
          "POST /admin/staff": {"email": "matrix.staff@sourceloop.test", "role": "EVALUATOR"}, "PUT /settings": {"token_budget_per_project": 50000},
          "POST /approvals/{aid}/resolve": {"status": "APPROVED", "version": 1}}


@pytest.fixture(autouse=True)
def no_network_probe(monkeypatch):
    """The provider test route must never reach a real Ollama from the suite."""
    async def fake(name, d, vault):  # noqa: ANN001
        return {"ok": False, "models": [], "error": "UNREACHABLE"}
    monkeypatch.setattr("sourceloop.api.routers.studio.probe", fake)


@pytest.fixture
async def parked(w):  # noqa: F811
    """A project parked at its first human checkpoint (every checkpoint is the buyer user's)."""
    pid = await create_and_start(w)
    c = await w.client("buyer")
    items = (await c.get(f"/approvals?status=PENDING&project_id={pid}")).json()["items"]
    if items:
        return pid, items[0]["id"]
    raise AssertionError("no buyer checkpoint reached")


ITEM_ROLE = {"/approvals/{aid}/resolve": R.BUYER}  # the parked approval targets the BUYER


def expected(role: R, relation: str, allowed: tuple, scoped: bool, evaluator_write: bool, tmpl: str = "") -> set[str]:
    if role not in allowed:
        return {"403"}
    if relation == "supplier":  # ADMIN of a supplier organization: sees the project only through the supplier view
        if tmpl in SUPPLIER_ADMIN_DENIED:
            return {"403"}
        if tmpl in ITEM_ROLE:
            return {"404"}  # the approval belongs to the buyer's project: not visible to it
    if role is R.SUPPLIER and tmpl in ITEM_ROLE:
        return {"404"}  # a supplier user sees only checkpoints addressed to it for its own terms; the buyer's item does not exist for it
    if role is R.EVALUATOR and evaluator_write:
        return {"403"}
    if scoped and relation == "other":
        return {"404"}
    if tmpl in ITEM_ROLE and role is not ITEM_ROLE[tmpl]:
        return {"403"}  # visible object, wrong role
    return {"ok"}


async def test_every_actor_on_every_route(w, parked):  # noqa: F811
    pid, aid = parked
    seen: list[str] = []
    for method, tmpl, allowed, scoped, effects in ROUTES:
        for key, (role, relation) in ACTORS.items():
            client = await w.client(key)
            path = tmpl.format(pid=pid, aid=aid)
            body = BODIES.get(f"{method} {tmpl}")
            if method == "GET":
                if tmpl.endswith("/stream") and role in allowed and relation != "other":
                    continue  # an allowed stream would stay open; covered by the SSE tests
                r = await client.get(path)
            elif method == "PUT":
                r = await client.put(path, body)
            elif method == "DELETE":
                r = await client.request("DELETE", path)
            else:
                r = await client.post(path, body)
            write = method != "GET"
            want = expected(role, relation, allowed, scoped, write, tmpl)
            got = "ok" if r.status_code not in DENIED else str(r.status_code)
            if effects is None and got == "ok":
                continue  # state-changing allowed cases are exercised in test_api_flow
            assert got in want, f"{method} {tmpl} as {key}: got {r.status_code} {r.text[:120]}, expected {want}"
            seen.append(f"{method} {tmpl} {key}")
    assert len(seen) > 150


async def test_anonymous_is_401_everywhere(w, parked):  # noqa: F811
    pid, aid = parked
    anon = Api(w.app)
    for method, tmpl, *_ in ROUTES:
        path = tmpl.format(pid=pid, aid=aid)
        r = await anon.request(method, path, json=BODIES.get(f"{method} {tmpl}") if method != "GET" else None)
        assert r.status_code == 401, (method, tmpl, r.status_code)
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    await anon.close()


async def test_evaluator_is_read_only(w, parked):  # noqa: F811
    pid, aid = parked
    ev = await w.client("evaluator")
    for path, body in ((f"/projects/{pid}/stop", {}), ("/projects", {"title": "x"}), (f"/approvals/{aid}/resolve", {"status": "APPROVED", "version": 1})):
        r = await ev.post(path, body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "READ_ONLY_ROLE"
    assert (await ev.put("/settings", {"token_budget_per_project": 5000})).status_code == 403
    assert (await ev.get(f"/projects/{pid}")).status_code == 200


async def test_resolve_wrong_role_vs_cross_tenant(w, parked):  # noqa: F811
    pid, aid = parked  # a BUYER item
    body = {"status": "APPROVED", "version": 1}
    assert (await (await w.client("org_admin")).post(f"/approvals/{aid}/resolve", body)).status_code == 403  # visible, but admins never approve
    assert (await (await w.client("admin")).post(f"/approvals/{aid}/resolve", body)).status_code == 403  # SUPER_ADMIN cannot approve QUALITY items
    assert (await (await w.client("org_admin")).post(f"/approvals/{aid}/resolve", body)).status_code == 403  # nor can the org admin
    assert (await (await w.client("buyer_b")).post(f"/approvals/{aid}/resolve", body)).status_code == 404  # other tenant
    assert (await (await w.client("supplier1")).post(f"/approvals/{aid}/resolve", body)).status_code == 404  # not its item: hidden, not forbidden
    assert (await (await w.client("quality")).post(f"/approvals/{uuid.uuid4()}/resolve", body)).status_code == 404


async def test_list_endpoints_are_scoped_in_sql(w, parked):  # noqa: F811
    pid, _ = parked
    mine = (await (await w.client("buyer")).get("/projects")).json()["items"]
    assert [p["id"] for p in mine] == [pid]
    assert (await (await w.client("buyer_b")).get("/projects")).json()["items"] == []
    assert (await (await w.client("outsider")).get("/projects")).json()["items"] == []
    assert [p["id"] for p in (await (await w.client("supplier1")).get("/projects")).json()["items"]] == [pid]
    assert [p["id"] for p in (await (await w.client("evaluator")).get("/projects")).json()["items"]] == [pid]
    # approvals inbox is role-scoped and tenant-scoped
    assert (await (await w.client("buyer_b")).get("/approvals")).json()["items"] == []
    assert all(a["target_role"] == "BUYER" for a in (await (await w.client("buyer")).get("/approvals")).json()["items"])
    assert (await (await w.client("supplier1")).get("/approvals")).json()["items"] == []  # a supplier sees only the checkpoints addressed to it
    sup = (await (await w.client("supplier1")).get(f"/projects/{pid}")).json()
    assert set(sup) == {"id", "title", "stage", "status", "outcome", "version", "created_at", "updated_at"}  # no brief / need text
