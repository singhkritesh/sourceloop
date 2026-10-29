#!/usr/bin/env sh
# Fresh e2e stack: new empty DB (data/e2e/e2e.db) -> alembic -> seed -> API on 8005. Writes gitignored data/e2e/creds.json from the seed output.
set -e
cd "$(dirname "$0")/.."
mkdir -p data/e2e
pkill -f "uvicorn sourceloop.main:app" 2>/dev/null || true
sleep 1
export DATABASE_URL="sqlite+aiosqlite:///./data/e2e/e2e.db"
# e2e-only: per-user limits raised for the whole journey (defaults stay in api/config.py; scripts/e2e-ratelimit.py checks the defaults)
# set E2E_DEFAULT_LIMITS=1 to start with defaults instead
if [ -z "$E2E_DEFAULT_LIMITS" ]; then
  export RATE_LIMIT_CREATE=1000/hour RATE_LIMIT_START=1000/hour RATE_LIMIT_RESOLVE=1000/minute RATE_LIMIT_LOGIN=1000/minute MAX_ACTIVE_PROJECTS_PER_ORG=200
fi
export AUDIT_ANCHOR_PATH="./data/e2e/anchors.jsonl"
rm -f data/e2e/e2e.db data/e2e/e2e.db-shm data/e2e/e2e.db-wal data/e2e/anchors.jsonl
.venv/bin/alembic upgrade head >/dev/null 2>&1
.venv/bin/python -m sourceloop.seed > data/e2e/seed-output.txt 2>&1
.venv/bin/python - <<'PY'
import json, re
creds = {}
for line in open("data/e2e/seed-output.txt"):
    m = re.match(r"^(\S+)@sourceloop\.test\s+\S+\s+(\S+)\s*$", line)
    if m:
        creds[m.group(1)] = [f"{m.group(1)}@sourceloop.test", m.group(2)]
# role aliases the e2e scripts use: `admin` is SBN's super admin (Studio, settings, every project); `orgadmin` is the buyer organization's admin
if "superadmin" in creds:
    creds["admin"] = creds["superadmin"]
if "buyeradmin" in creds:
    creds["orgadmin"] = creds["buyeradmin"]
json.dump(creds, open("data/e2e/creds.json", "w"))
print("creds:", sorted(creds))
PY
(nohup scripts/start.sh > data/e2e/api.log 2>&1 &)
for i in 1 2 3 4 5 6 7 8 9 10; do curl -sf localhost:8005/api/v1/health && break; sleep 1; done; echo
