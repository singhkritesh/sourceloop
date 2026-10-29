"""Auth, CSRF, lockout, rate limiting and the error envelope."""
import uuid

from api_support import API, PASSWORD, Api, build_world, w  # noqa: F401
from sourceloop.data import db as dbm


def assert_envelope(r, status, code=None):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message", "request_id"}
    uuid.UUID(body["error"]["request_id"])
    assert body["error"]["request_id"] == r.headers["x-request-id"]
    if code:
        assert body["error"]["code"] == code


async def test_health_is_public(w):  # noqa: F811
    c = Api(w.app)
    r = await c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "db": True, "llm_mode": "replay"}
    await c.close()


async def test_error_envelope_everywhere(w):  # noqa: F811
    c = Api(w.app)
    assert_envelope(await c.get("/nope"), 404, "NOT_FOUND")
    assert_envelope(await c.get("/auth/me"), 401, "UNAUTHENTICATED")
    assert_envelope(await c.get("/projects"), 401, "UNAUTHENTICATED")
    r = await c.post("/auth/login", {"email": "a@b.c", "password": "x", "extra": 1})  # extra=forbid
    assert_envelope(r, 422, "VALIDATION_ERROR")
    assert "extra" in r.json()["error"]["message"]
    r = await c.post("/auth/login", {"email": "a@b.c", "password": "hunter2-secret-value", "extra": 1})
    assert "hunter2" not in r.text  # submitted values are never echoed
    big = await c.c.post(API + "/auth/login", content=b"x" * 1_100_000, headers={"content-type": "application/json"})
    assert_envelope(big, 413, "PAYLOAD_TOO_LARGE")
    await c.close()
    b = await w.client("buyer")
    assert_envelope(await b.get("/projects/not-a-uuid"), 422)
    assert_envelope(await b.get(f"/projects/{uuid.uuid4()}"), 404, "NOT_FOUND")
    assert_envelope(await b.get("/settings"), 403, "FORBIDDEN")
    r = await b.post("/projects", {"title": "t", "need_text": "x" * 4001})
    assert_envelope(r, 413, "PAYLOAD_TOO_LARGE")


async def test_login_me_logout_cookie_flags(w):  # noqa: F811
    c = Api(w.app)
    bad = await c.login(w.emails["buyer"], "wrong-password-value")
    assert_envelope(bad, 401, "UNAUTHENTICATED")
    unknown = await c.login("nobody@sourceloop.test")
    assert unknown.json()["error"]["message"] == bad.json()["error"]["message"]  # generic, no user enumeration
    r = await c.login(w.emails["buyer"])
    assert r.status_code == 200 and r.json()["role"] == "BUYER" and "password" not in r.text
    cookies = {h.split("=", 1)[0]: h.lower() for h in r.headers.get_list("set-cookie")}
    assert "httponly" in cookies["access_token"] and "samesite=strict" in cookies["access_token"] and "path=/api/v1" in cookies["access_token"]
    assert "httponly" in cookies["refresh_token"]
    assert "httponly" not in cookies["csrf"]  # JS must be able to read it for the double-submit header
    assert (await c.get("/auth/me")).json()["email"] == w.emails["buyer"]
    assert (await c.post("/auth/logout")).status_code == 204
    assert_envelope(await c.get("/auth/me"), 401)
    await c.close()


async def test_logout_revokes_access_token_even_if_replayed(w):  # noqa: F811
    c = Api(w.app)
    await c.login(w.emails["buyer"])
    stolen = c.c.cookies.get("access_token")
    await c.post("/auth/logout")
    c2 = Api(w.app)
    c2.c.cookies.set("access_token", stolen, path="/api/v1")
    assert_envelope(await c2.get("/auth/me"), 401)
    await c.close()
    await c2.close()


async def test_register_creates_self_service_role_only(w):  # noqa: F811
    c = Api(w.app)
    body = {"email": "new.user@sourceloop.test", "password": PASSWORD, "org_name": "New Org", "org_kind": "SUPPLIER"}
    r = await c.post("/auth/register", body)
    assert r.status_code == 201 and r.json()["role"] == "SUPPLIER" and r.json()["org"]["kind"] == "SUPPLIER"
    assert_envelope(await c.post("/auth/register", body), 409, "EMAIL_TAKEN")
    assert_envelope(await c.post("/auth/register", {**body, "email": "x@y.z", "password": "short"}), 422)
    assert_envelope(await c.post("/auth/register", {**body, "email": "z@y.z", "role": "ADMIN"}), 422)  # cannot pick a role
    await c.close()


async def test_lockout_after_five_failures(w):  # noqa: F811
    c = Api(w.app)
    for _ in range(5):
        assert_envelope(await c.login(w.emails["legal"], "wrong-password-value"), 401)
    r = await c.login(w.emails["legal"])  # even the right password is refused while locked
    assert_envelope(r, 401, "UNAUTHENTICATED")  # uniform with a wrong password: no lock/existence oracle
    assert "retry-after" not in r.headers
    other = await c.login(w.emails["finance"])  # other accounts unaffected
    assert other.status_code == 200
    await c.close()


async def test_refresh_rotates_and_reuse_revokes_all(w):  # noqa: F811
    c = Api(w.app)
    await c.login(w.emails["buyer"])
    old_refresh = c.c.cookies.get("refresh_token")
    r = await c.post("/auth/refresh")
    assert r.status_code == 200
    new_refresh = c.c.cookies.get("refresh_token")
    assert new_refresh != old_refresh
    # replay of the rotated token => theft assumed: everything for that user is revoked
    thief = Api(w.app)
    await thief.c.get(API + "/health")
    thief.c.cookies.set("refresh_token", old_refresh, path="/api/v1")
    thief.c.cookies.set("csrf", "t", path="/")
    r = await thief.post("/auth/refresh", headers={"X-CSRF-Token": "t"})
    assert_envelope(r, 401, "UNAUTHENTICATED")
    assert_envelope(await c.post("/auth/refresh"), 401)  # the legitimate (new) token was revoked too
    await c.close()
    await thief.close()


async def test_csrf_double_submit(w):  # noqa: F811
    c = Api(w.app, csrf=False)
    assert (await c.login(w.emails["buyer"])).status_code == 200  # login itself is exempt
    body = {"title": "t", "need_text": "n"}
    assert_envelope(await c.post("/projects", body), 403, "CSRF_FAILED")  # no header
    assert_envelope(await c.post("/projects", body, headers={"X-CSRF-Token": "wrong"}), 403, "CSRF_FAILED")
    ok = await c.post("/projects", body, headers={"X-CSRF-Token": c.c.cookies.get("csrf")})
    assert ok.status_code == 201
    assert (await c.get("/projects")).status_code == 200  # safe methods need no token
    await c.close()


async def test_rate_limit_429_with_retry_after(tmp_path):
    world = await build_world(tmp_path, login_limit="3/minute")
    async with world.app.router.lifespan_context(world.app):
        c = Api(world.app)
        codes = [(await c.login(world.emails["buyer"], "wrong-password-value")).status_code for _ in range(4)]
        assert codes[:3] == [401, 401, 401] and codes[3] == 429
        r = await c.login(world.emails["buyer"], "wrong-password-value")
        assert_envelope(r, 429, "RATE_LIMITED")
        assert int(r.headers["retry-after"]) > 0
        await c.close()
    await dbm.dispose()
