"""Drive one 'happy' project through the real API (approvals resolved per role) and print the audit head hash.
Usage: .venv/bin/python scripts/e2e-determinism.py  (needs data/e2e/creds.json; API on 8005, replay mode)"""
import json
import time

import httpx

API = "http://127.0.0.1:8005/api/v1"
creds = json.load(open("data/e2e/creds.json"))
ROLE = {"REQUIREMENT": "buyer", "AWARD": "buyer", "COMPLIANCE_FLAG": "compliance", "TECHNICAL_QUAL": "quality",
        "SIGNATURE": "legal", "CLAUSE": "legal", "BANK_DETAILS": "finance", "HOLD_RELEASE": "finance", "MANDATE_BREACH": "buyer"}


def client(who):
    c = httpx.Client(base_url=API, timeout=30)
    r = c.post("/auth/login", json={"email": creds[who][0], "password": creds[who][1]})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = c.cookies.get("csrf")
    return c


cl = {w: client(w) for w in ("buyer", "compliance", "quality", "legal", "finance", "admin")}
pid = cl["buyer"].post("/projects", json={"title": "determinism", "need_text": "", "scenario": "happy", "need_index": 0, "seed": 42}).json()["id"]
cl["buyer"].post(f"/projects/{pid}/start").raise_for_status()
deadline = time.time() + 120
while time.time() < deadline:
    p = cl["buyer"].get(f"/projects/{pid}").json()
    for who, c in cl.items():
        if who == "admin":
            continue
        for a in c.get("/approvals", params={"status": "PENDING"}).json()["items"]:
            if a["project_id"] == pid and ROLE.get(a["kind"]) == who:
                c.post(f"/approvals/{a['id']}/resolve", json={"status": "APPROVED", "comment": "", "version": a["version"]}).raise_for_status()
    if p["status"] in ("DONE", "STOPPED", "FAILED") :
        time.sleep(1.5)
        left = [a for a in cl["finance"].get("/approvals", params={"status": "PENDING"}).json()["items"] if a["project_id"] == pid]
        if not left:
            break
    time.sleep(0.4)
v = cl["admin"].get(f"/projects/{pid}/audit/verify").json()
import hashlib

ev = []
after = 0
while True:
    page = cl["admin"].get(f"/projects/{pid}/audit", params={"after": after, "limit": 500}).json()
    items = page.get("items", page.get("events", []))
    if not items:
        break
    ev += items
    after = items[-1]["seq"]
    if len(items) < 500:
        break
# ids / timestamps are random per real run (they ARE hashed), so compare the id-free decision sequence as well
shape = hashlib.sha256(json.dumps([(e["seq"], e["actor_type"], e["action"], e.get("rule_id"), e.get("outcome")) for e in ev]).encode()).hexdigest()
print(json.dumps({"status": p["status"], "outcome": p["outcome"], "valid": v["valid"], "events": v["events"], "head": v["head_hash"], "decision_sequence_sha": shape, "n": len(ev)}))
