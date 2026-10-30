#!/usr/bin/env sh
# Release check against the RUNNING Docker stack (./start.sh first; local Ollama with the 4b model; Chrome).
#   1. backend tests + lint + trust boundary   2. web tests + build   3. the browser journey (role picker -> typed need -> deal -> buyer asks,
#   supplier confirmation, agent performance, help).  The journey calls the local model, so it takes about a minute.
set -e
cd "$(dirname "$0")/.."
.venv/bin/ruff check src tests
.venv/bin/lint-imports | tail -1
.venv/bin/pytest -q | tail -1
( cd web && npm test --silent | tail -3 && npm run build --silent >/dev/null && echo "web build ok" )
curl -sf localhost:8005/api/v1/health >/dev/null || { echo "stack is not running: ./start.sh"; exit 1; }
mkdir -p data/ui
( cd web && node scripts/e2e-simple.mjs ) | tee data/ui/e2e-simple.log
! grep -q '^FAIL' data/ui/e2e-simple.log
