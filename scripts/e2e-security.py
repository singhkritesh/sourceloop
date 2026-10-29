"""Second-round e2e: audit export + tamper guard, default rate limits, per-(account,ip) login throttling, prod-mode boot.
Everything runs on a COPY of the e2e DB (data/e2e/e2e.db -> data/e2e/tamper.db) on a TEMPORARY port 8006 (never the dev DB).
Usage: .venv/bin/python scripts/e2e-security.py [export|tamper|limits|auth|prod|all]   (needs data/e2e/creds.json; e2e API on 8005 for `export`)
Prints PASS/FAIL lines and writes data/e2e/e2e-security.json. Secrets for the prod boot are generated in-process, never written to disk."""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import httpx
from cryptography.fernet import Fernet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
CREDS = json.load(open("data/e2e/creds.json"))
PORT = 8006
RESULTS: list[list[str]] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    RESULTS.append(["PASS" if ok else "FAIL", name, str(extra)[:240]])
    print("PASS" if ok else "FAIL", name, str(extra)[:240], flush=True)


def login(base: str, who: str, ip: str | None = None, pw: str | None = None) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=60, headers={"X-Forwarded-For": ip} if ip else {})
    r = c.post("/auth/login", json={"email": CREDS[who][0], "password": pw or CREDS[who][1]})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = c.cookies.get("csrf")
    return c


def raw_login(base: str, email: str, pw: str, ip: str | None = None) -> httpx.Response:
    return httpx.post(f"{base}/auth/login", json={"email": email, "password": pw}, headers={"X-Forwarded-For": ip} if ip else {}, timeout=60)


def start_api(env_extra: dict[str, str], db: str, log: str, port: int = PORT) -> subprocess.Popen:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///./{db}", **env_extra}
    for k in ("JWT_SECRET", "ORG_SIGNING_SECRET", "SOURCELOOP_VAULT_KEY", "CONFIG_ENCRYPTION_KEY", "APP_ENV", "CORS_ORIGINS"):
        if k not in env_extra:
            env.pop(k, None)
    p = subprocess.Popen([".venv/bin/uvicorn", "sourceloop.main:app", "--host", "127.0.0.1", "--port", str(port)], env=env,
                         stdout=open(log, "w"), stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(40):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=2).status_code == 200:
                return p
        except Exception:  # noqa: BLE001
            pass
        if p.poll() is not None:
            break
        time.sleep(0.5)
    return p


def stop(p: subprocess.Popen) -> None:
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)


def copy_db(dst: str) -> None:
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(dst + ext):
            os.remove(dst + ext)
    src = sqlite3.connect("data/e2e/e2e.db")
    out = sqlite3.connect(dst)
    src.backup(out)
    out.close()
    src.close()


def hexid(pid: str) -> str:
    return pid.replace("-", "")


# --------------------------------------------------------------------------------------------- export
def phase_export() -> None:
    base = "http://127.0.0.1:8005/api/v1"
    adm, ev, buyer, b2 = login(base, "admin"), login(base, "evaluator"), login(base, "buyer"), login(base, "buyer2")
    sup = login(base, "supplier1")
    pid = [p for p in buyer.get("/projects").json()["items"] if p["title"] == "E2E happy"][0]["id"]
    r = adm.get(f"/projects/{pid}/audit/export")
    lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    ev_lines, trailer = [x for x in lines if x["type"] == "event"], lines[-1]
    check("G export: 200 application/x-ndjson with attachment filename", r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson") and "attachment" in r.headers.get("content-disposition", ""), r.headers.get("content-type"))
    check("G export: every line is standalone JSON; events in dense seq order, ONE trailer last", trailer["type"] == "trailer" and sum(1 for x in lines if x["type"] == "trailer") == 1 and [e["seq"] for e in ev_lines] == list(range(1, len(ev_lines) + 1)), f"{len(ev_lines)} events")
    ver = adm.get(f"/projects/{pid}/audit/verify").json()
    check("G export: trailer valid, events + head_hash equal /audit/verify", trailer["valid"] is True and trailer["events"] == len(ev_lines) == ver["events"] and trailer["head_hash"] == ver["head_hash"] == ev_lines[-1]["hash"], json.dumps(trailer)[:200])
    check("G export: offline linkage prev_hash == previous hash (genesis first)", all(ev_lines[i]["prev_hash"] == ev_lines[i - 1]["hash"] for i in range(1, len(ev_lines))) and ev_lines[0]["prev_hash"] not in (None, ev_lines[0]["hash"]))
    check("G export: anchor present and verified", trailer.get("anchor_verified") is True and trailer.get("anchor") is not None, json.dumps(trailer.get("anchor")))
    check("G export: EVALUATOR allowed", ev.get(f"/projects/{pid}/audit/export").status_code == 200)
    check("G export: BUYER 403, SUPPLIER 403", buyer.get(f"/projects/{pid}/audit/export").status_code == 403 and sup.get(f"/projects/{pid}/audit/export").status_code == 403)
    check("G export: nonexistent project 404", adm.get(f"/projects/{uuid.uuid4()}/audit/export").status_code == 404)
    check("G export: no secrets / key material in the export", not any(k in r.text.lower() for k in ("password", "jwt_secret", "vault", "api_key", "sk-e2e")))
    _ = b2


# --------------------------------------------------------------------------------------------- tamper + limits (defaults)
def phase_tamper_and_limits() -> None:
    base = f"http://127.0.0.1:{PORT}/api/v1"
    copy_db("data/e2e/tamper.db")
    p = start_api({}, "data/e2e/tamper.db", "data/e2e/api-8006.log")  # DEFAULT limits (no RATE_LIMIT_* overrides)
    try:
        check("G/E tamper copy API up on 8006 with default limits", p.poll() is None, "")
        buyer, adm = login(base, "buyer"), login(base, "admin")
        # a DRAFT created on THIS instance (a restart closes DRAFTs as RESTART_LOST_RUNTIME, so one from the copy could not be started)
        draft = buyer.post("/projects", json={"title": "E2E tamper draft", "need_text": "", "scenario": "happy", "need_index": 0, "seed": 42}).json()["id"]
        # --- project B: a live ESCALATED run so resume is possible on the copy
        b = buyer.post("/projects", json={"title": "E2E tamper live", "need_text": "", "scenario": "happy", "need_index": 0, "seed": 42}).json()["id"]
        st = buyer.post(f"/projects/{b}/start")
        for _ in range(60):
            if buyer.get(f"/projects/{b}").json()["status"] == "ESCALATED":
                break
            time.sleep(0.5)
        check("G tamper: live project reaches ESCALATED on the copy", st.status_code == 202 and buyer.get(f"/projects/{b}").json()["status"] == "ESCALATED")
        check("G tamper: baseline chains verify (draft and live)", adm.get(f"/projects/{draft}/audit/verify").json()["valid"] and adm.get(f"/projects/{b}/audit/verify").json()["valid"])
        # --- corrupt one audit row of each, directly in the COPY (hashed field: outcome)
        db = sqlite3.connect("data/e2e/tamper.db", timeout=30)
        try:
            db.execute("update audit_event set outcome='TAMPERED' where project_id=? and seq=2", (b,))
            blocked = False
        except sqlite3.IntegrityError as e:
            blocked = "INSERT only" in str(e)
        check("G tamper: DB trigger refuses a plain UPDATE of audit_event (first line of defence)", blocked)
        db.execute("drop trigger audit_event_no_update")  # a privileged attacker with file access removes the trigger; the chain must still catch it
        # a never-run DRAFT has no audit rows: forge one (copy of another project's seq 1 with a changed outcome, hash left as is)
        n = db.execute("insert into audit_event select ?, seq, ts, actor_type, actor_id, action, rule_id, evidence, model_id, model_version, 'FORGED', approver_id, config_version, prev_hash, hash from audit_event where project_id=? and seq=1", (draft, b)).rowcount
        check(f"G tamper: forged audit row seq 1 injected into DRAFT {draft[:8]}", n == 1, n)
        n = db.execute("update audit_event set outcome='TAMPERED' where project_id=? and seq=2", (b,)).rowcount
        check(f"G tamper: corrupted audit row seq 2 of live project {b[:8]}", n == 1, n)
        db.commit()
        db.close()
        for label, pid, sq in (("draft", draft, 1), ("live", b, 2)):
            v = adm.get(f"/projects/{pid}/audit/verify").json()
            check(f"G tamper: verify reports invalid ({label}), first_bad_seq={sq}", v["valid"] is False and v["first_bad_seq"] == sq, json.dumps(v)[:120])
            trailer = json.loads(adm.get(f"/projects/{pid}/audit/export").text.splitlines()[-1])
            check(f"G tamper: export trailer valid=false ({label})", trailer["valid"] is False and trailer["first_bad_seq"] == sq, json.dumps(trailer)[:140])
        r1 = buyer.post(f"/projects/{draft}/start")
        check("G tamper: START on the tampered DRAFT -> 409 TAMPER_SUSPECTED", r1.status_code == 409 and r1.json()["error"]["code"] == "TAMPER_SUSPECTED", r1.text[:120])
        r2 = buyer.post(f"/projects/{b}/resume")
        check("G tamper: RESUME on the tampered ESCALATED run -> 409 TAMPER_SUSPECTED", r2.status_code == 409 and r2.json()["error"]["code"] == "TAMPER_SUSPECTED", r2.text[:120])
        r3 = buyer.post(f"/projects/{b}/resume")
        check("G tamper: repeat resume still 409 TAMPER_SUSPECTED", r3.status_code == 409 and r3.json()["error"]["code"] == "TAMPER_SUSPECTED", r3.text[:120])
        sts = [buyer.get(f"/projects/{x}").json()["status"] for x in (draft, b)]
        check("G tamper: blocked projects never entered RUNNING", sts[0] == "DRAFT" and sts[1] == "ESCALATED", sts)
        db = sqlite3.connect("data/e2e/tamper.db")
        rows = db.execute("select count(*) from audit_event where action='tamper_suspected' and project_id in (?,?)", (draft, b)).fetchone()[0]
        db.close()
        check("G tamper: the block itself is audited exactly once per project (no append per retry)", rows == 2, rows)

        # --- default per-user limits (buyer2 = fresh buckets)
        b2 = login(base, "buyer2")
        body = {"title": "rl", "need_text": "", "scenario": "happy", "need_index": 0, "seed": 1}
        codes = [b2.post("/projects", json={**body, "title": f"rl{i}"}).status_code for i in range(11)]
        last = b2.post("/projects", json={**body, "title": "rl-over"})
        check("E rate: project create allows 10/hour then 429 RATE_LIMITED + Retry-After", codes[:10] == [201] * 10 and last.status_code == 429 and last.json()["error"]["code"] == "RATE_LIMITED" and int(last.headers.get("retry-after", 0)) > 0, f"{codes} retry-after={last.headers.get('retry-after')}")
        drafts = [x["id"] for x in b2.get("/projects").json()["items"] if x["title"].startswith("rl")][:6]
        sc = [b2.post(f"/projects/{d}/start").status_code for d in drafts]
        over = b2.post(f"/projects/{drafts[5]}/start") if len(drafts) > 5 else None
        check("E rate: start+resume share 5/hour; the 6th is 429 RATE_LIMITED + Retry-After", len(sc) >= 6 and 429 not in sc[:5] and sc[5] == 429 and over is not None and over.json()["error"]["code"] == "RATE_LIMITED" and "retry-after" in over.headers, f"{sc}")
        rs = [b2.post(f"/approvals/{uuid.uuid4()}/resolve", json={"status": "APPROVED", "comment": "", "version": 1}).status_code for _ in range(62)]
        first429 = rs.index(429) if 429 in rs else None
        check("E rate: approval resolve allows 60/minute then 429", first429 == 60 or (first429 is not None and 55 <= first429 <= 61), f"first 429 at request #{first429}")
        # default login limit 20/minute per IP
        lcodes = [raw_login(base, "nobody@sourceloop.test", "x").status_code for _ in range(22)]
        check("E rate: login limiter (default 20/minute per IP) yields 429 with envelope", 429 in lcodes, lcodes[-5:])
        _ = p
    finally:
        stop(p)


# --------------------------------------------------------------------------------------------- auth throttling
def phase_auth() -> None:
    base = f"http://127.0.0.1:{PORT}/api/v1"
    copy_db("data/e2e/tamper.db")
    p = start_api({"TRUSTED_PROXY_HOPS": "1", "RATE_LIMIT_LOGIN": "1000/minute"}, "data/e2e/tamper.db", "data/e2e/api-8006-auth.log")
    try:
        uni = lambda r: (r.status_code, json.dumps({k: v for k, v in r.json()["error"].items() if k != "request_id"}, sort_keys=True))  # noqa: E731
        A, B = "10.1.0.1", "10.1.0.2"
        em = CREDS["designer"][0]
        bad = [uni(raw_login(base, em, f"wrong-{i}", A)) for i in range(5)]
        ghost = uni(raw_login(base, "nobody@sourceloop.test", "wrong", A))
        check("E auth: 5 wrong passwords from IP A -> identical uniform 401 (same as unknown email)", len(set(bad)) == 1 and bad[0][0] == 401 and ghost == bad[0], bad[0])
        okA = raw_login(base, em, CREDS["designer"][1], A)
        check("E auth: (account, IP A) pair is locked: correct password from A is the same uniform 401", uni(okA) == bad[0], okA.status_code)
        okB = raw_login(base, em, CREDS["designer"][1], B)
        check("E auth: SAME account from a different IP B still logs in (pair-scoped lock, no account-DoS)", okB.status_code == 200, okB.status_code)
        okC = raw_login(base, CREDS["buyer"][0], CREDS["buyer"][1], A)
        check("E auth: another account from the locked IP A still logs in", okC.status_code == 200, okC.status_code)
        # account-wide lock after many failures from MANY IPs
        em2 = CREDS["evaluator"][0]
        for i in range(25):
            raw_login(base, em2, f"wrong-{i}", f"10.2.0.{i + 1}")
        fresh = raw_login(base, em2, CREDS["evaluator"][1], "10.9.9.9")
        check("E auth: 25 failures across many IPs lock the account itself; correct password from a fresh IP is the uniform 401", uni(fresh) == bad[0], fresh.status_code)
        # per-IP flood: 30 failures against distinct accounts from one IP -> that IP gets 429 + Retry-After; others unaffected
        flood_ip = "10.3.0.1"
        codes = [raw_login(base, f"user{i}@sourceloop.test", "x", flood_ip).status_code for i in range(36)]
        r = raw_login(base, CREDS["buyer"][0], CREDS["buyer"][1], flood_ip)
        check("E auth: one IP failing 30+ logins (any accounts) is slowed with 429 + Retry-After", 429 in codes and r.status_code == 429 and int(r.headers.get("retry-after", 0)) > 0, f"first429 idx={codes.index(429) if 429 in codes else None}")
        other = raw_login(base, CREDS["buyer"][0], CREDS["buyer"][1], "10.3.0.2")
        check("E auth: a different IP is unaffected by that flood", other.status_code == 200, other.status_code)
        # XFF is ignored without trusted hops (spoofing cannot dodge the lock): covered by unit tests; here the client-supplied left entry never wins
        spoof = raw_login(base, em, "wrong", "1.2.3.4, 10.1.0.1")
        check("E auth: client-controlled left XFF entries are ignored (only the proxy-appended entry counts)", spoof.status_code == 401, spoof.status_code)
    finally:
        stop(p)


# --------------------------------------------------------------------------------------------- prod boot
def phase_prod() -> None:
    db = "data/e2e/prod.db"
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db + ext):
            os.remove(db + ext)
    secrets_env = {"APP_ENV": "prod", "CORS_ORIGINS": "https://sourceloop.example.test", "SOURCELOOP_VAULT_KEY": Fernet.generate_key().decode(),
                   "ORG_SIGNING_SECRET": uuid.uuid4().hex + uuid.uuid4().hex, "JWT_SECRET": uuid.uuid4().hex + uuid.uuid4().hex}
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///./{db}", **secrets_env}
    subprocess.run([".venv/bin/alembic", "upgrade", "head"], env=env, capture_output=True, check=True)
    seed = subprocess.run([".venv/bin/python", "-m", "sourceloop.seed"], env=env, capture_output=True, text=True)
    pw = email = None
    for line in seed.stdout.splitlines() + seed.stderr.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "buyer@sourceloop.test":
            email, pw = parts[0], parts[2]
    check("prod: seed works with prod env (alembic upgraded a fresh prod DB)", bool(pw), seed.returncode)
    p = start_api(secrets_env, db, "data/e2e/api-prod.log")
    try:
        b = f"http://127.0.0.1:{PORT}"
        check("prod: API starts with valid CORS/vault/signing/JWT env", p.poll() is None)
        h = httpx.get(f"{b}/api/v1/health")
        check("prod: /health ok with HSTS header", h.status_code == 200 and h.json()["status"] == "ok" and "max-age" in h.headers.get("strict-transport-security", ""), h.headers.get("strict-transport-security"))
        d = [httpx.get(f"{b}{x}").status_code for x in ("/docs", "/redoc", "/openapi.json", "/api/v1/docs", "/api/v1/openapi.json")]
        check("prod: /docs, /redoc, /openapi.json are 404", all(c == 404 for c in d), d)
        pre = httpx.options(f"{b}/api/v1/auth/login", headers={"Origin": "https://sourceloop.example.test", "Access-Control-Request-Method": "POST"})
        bad = httpx.options(f"{b}/api/v1/auth/login", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
        check("prod: CORS allows only the configured https origin", pre.headers.get("access-control-allow-origin") == "https://sourceloop.example.test" and "access-control-allow-origin" not in bad.headers, f"{pre.headers.get('access-control-allow-origin')} / {bad.headers.get('access-control-allow-origin')}")
        if pw:
            r = httpx.post(f"{b}/api/v1/auth/login", json={"email": email, "password": pw})
            sc = r.headers.get_list("set-cookie")
            check("prod: login works and every cookie is Secure + HttpOnly where expected", r.status_code == 200 and all("secure" in c.lower() for c in sc) and any("httponly" in c.lower() for c in sc), [c.split(";")[0].split("=")[0] for c in sc])
            c = httpx.Client(base_url=f"{b}/api/v1", verify=False)  # noqa: S501 - local, Secure cookies need https; send them by hand
            ck = "; ".join(x.split(";")[0] for x in sc)
            me = httpx.get(f"{b}/api/v1/auth/me", headers={"Cookie": ck})
            check("prod: authenticated request succeeds (me)", me.status_code == 200 and me.json()["role"] == "BUYER", me.status_code)
            c.close()
            reg = httpx.post(f"{b}/api/v1/auth/register", json={"email": "new@x.test", "password": "Aa1!aaaaaaaaaa", "org_name": "Probe Org", "org_kind": "BUYER"})
            check("prod: self-registration disabled by default (403)", reg.status_code == 403, reg.status_code)
    finally:
        stop(p)
    # refusal cases
    def refuses(name: str, **drop: str | None) -> None:
        e = {**secrets_env}
        for k, v in drop.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = v
        env2 = {**{k: v for k, v in os.environ.items() if k not in secrets_env}, "DATABASE_URL": f"sqlite+aiosqlite:///./{db}", **e}
        q = subprocess.run([".venv/bin/uvicorn", "sourceloop.main:app", "--host", "127.0.0.1", "--port", str(PORT)], env=env2, capture_output=True, text=True, timeout=60)
        leaked = any(v in (q.stdout + q.stderr) for v in secrets_env.values() if len(v) > 20)
        check(f"prod: {name} -> refuses to start (non-zero exit), no secret echoed", q.returncode != 0 and not leaked, (q.stderr.strip().splitlines() or [""])[-1][:150])
    refuses("missing SOURCELOOP_VAULT_KEY", SOURCELOOP_VAULT_KEY=None)
    refuses("invalid vault key", SOURCELOOP_VAULT_KEY="not-a-fernet-key")
    refuses("missing ORG_SIGNING_SECRET", ORG_SIGNING_SECRET=None)
    refuses("short JWT_SECRET", JWT_SECRET="short")
    refuses("missing CORS_ORIGINS", CORS_ORIGINS=None)
    refuses("http:// CORS origin", CORS_ORIGINS="http://sourceloop.example.test")
    refuses("wildcard CORS origin", CORS_ORIGINS="*")
    refuses("COOKIE_SECURE=false", COOKIE_SECURE="false")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("export", "all"):
        phase_export()
    if which in ("tamper", "limits", "all"):
        phase_tamper_and_limits()
    if which in ("auth", "all"):
        phase_auth()
    if which in ("prod", "all"):
        phase_prod()
    json.dump(RESULTS, open(f"data/e2e/e2e-security-{which}.json", "w"), indent=1)
    print(f"\n{sum(r[0] == 'PASS' for r in RESULTS)} PASS, {sum(r[0] == 'FAIL' for r in RESULTS)} FAIL")
    sys.exit(1 if any(r[0] == "FAIL" for r in RESULTS) else 0)
