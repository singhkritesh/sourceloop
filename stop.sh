#!/usr/bin/env bash
# Stop SourceLoop (Docker containers and/or local processes). Data is preserved. Wipe Docker data: docker compose down -v
cd "$(dirname "${BASH_SOURCE[0]}")"
Q=false; [ "${1:-}" = "--quiet" ] && Q=true
say(){ $Q || echo "[INFO]  $*"; }
if docker info &>/dev/null; then docker compose down 2>/dev/null && say "containers stopped"; fi
for f in data/api.pid data/web.pid; do
  [ -f "$f" ] && { kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; }
done
pkill -f "uvicorn sourceloop.main:app" 2>/dev/null || true
lsof -ti tcp:3010 2>/dev/null | xargs kill 2>/dev/null || true
# release Ollama VRAM if a model is loaded
curl -sf localhost:11434/api/ps 2>/dev/null | python3 -c "import sys,json;[print(m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null | while read -r m; do
  curl -s localhost:11434/api/generate -d "{\"model\":\"$m\",\"keep_alive\":0}" >/dev/null; say "unloaded $m"; done
say "stopped"
