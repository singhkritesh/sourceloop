#!/usr/bin/env sh
# Full real-stack e2e on a FRESH DB (data/e2e/e2e.db). Needs: web dev server on 3010 (cd web && npm run dev), Chrome, local Ollama with the fast model for phase D.
# Usage: scripts/e2e-all.sh [tag]   -> logs in data/e2e/run-<tag>-*.log ; exit 1 if any phase fails.
cd "$(dirname "$0")/.."
TAG="${1:-x}"; FAIL=0
sh scripts/e2e-fresh.sh > "data/e2e/run-$TAG-fresh.log" 2>&1 || exit 1
run() { name="$1"; shift; ( cd web && E2E_CREDS=../data/e2e/creds.json "$@" ) > "data/e2e/run-$TAG-$name.log" 2>&1; rc=$?; echo "$name rc=$rc pass=$(grep -c '^PASS  ' data/e2e/run-$TAG-$name.log) fail=$(grep -c '^FAIL  ' data/e2e/run-$TAG-$name.log)"; [ $rc -ne 0 ] && FAIL=1; }
run B node scripts/e2e.mjs B
run N env RESULT_PREFIX=e2e-new node scripts/e2e-new.mjs N
run P env RESULT_PREFIX=e2e-persp node scripts/e2e-persp.mjs P
run O env RESULT_PREFIX=e2e-persp node scripts/e2e-persp.mjs O
run C node scripts/e2e.mjs C
run C2 env RESULT_PREFIX=e2e-new node scripts/e2e-new.mjs C2
run E node scripts/e2e.mjs E
[ -n "$E2E_LIVE" ] && run D node scripts/e2e.mjs D   # phase D needs local Ollama: opt in with E2E_LIVE=1
run F node scripts/e2e.mjs F
run F2 env RESULT_PREFIX=e2e-new node scripts/e2e-new.mjs F2
for i in 1 2 3; do .venv/bin/python scripts/e2e-determinism.py; done > "data/e2e/run-$TAG-determinism.log" 2>&1
[ "$(grep -o '"decision_sequence_sha": "[0-9a-f]*"' data/e2e/run-$TAG-determinism.log | sort -u | wc -l | tr -d ' ')" = 1 ] && echo "determinism OK (3 runs, identical decision sequence)" || { echo "determinism FAIL"; FAIL=1; }
.venv/bin/python scripts/e2e-security.py all > "data/e2e/run-$TAG-security.log" 2>&1; rc=$?; echo "security rc=$rc $(tail -1 data/e2e/run-$TAG-security.log)"; [ $rc -ne 0 ] && FAIL=1
# smoke.mjs needs existing projects (runs on the used DB); smoke-studio expects a pristine DB (publishes v2, counts rows): reseed fresh for it
( cd web && U=$(python3 -c "import json;print(json.dumps(json.load(open('../data/e2e/creds.json'))))") && SMOKE_USERS="$U" SHOTS=../data/e2e node scripts/smoke.mjs ) > "data/e2e/run-$TAG-smoke.log" 2>&1; rc=$?; echo "smoke rc=$rc FAIL lines: $(grep -c '^FAIL' data/e2e/run-$TAG-smoke.log)"; [ $rc -ne 0 ] && FAIL=1
sh scripts/e2e-fresh.sh > "data/e2e/run-$TAG-fresh2.log" 2>&1
( cd web && A=$(python3 -c "import json;print(json.dumps(json.load(open('../data/e2e/creds.json'))['admin']))") && SMOKE_ADMIN="$A" SHOTS=../data/e2e node scripts/smoke-studio.mjs ) > "data/e2e/run-$TAG-smoke-studio.log" 2>&1; rc=$?; echo "smoke-studio rc=$rc FAIL lines: $(grep -c '^FAIL' data/e2e/run-$TAG-smoke-studio.log)"; [ $rc -ne 0 ] && FAIL=1
exit $FAIL
