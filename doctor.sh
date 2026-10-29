#!/usr/bin/env bash
# Checks that SourceLoop is set up and running, and says what to do about anything that is not.
# Usage: ./doctor.sh        (exit code 0 = everything needed is fine)
cd "$(dirname "${BASH_SOURCE[0]}")"
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'; BAD=0
pass(){ printf "${G}  ok  ${N} %s\n" "$*"; }
warn(){ printf "${Y} note ${N} %s\n" "$*"; }
fail(){ printf "${R} FAIL ${N} %s\n    -> %s\n" "$1" "$2"; BAD=1; }

command -v docker &>/dev/null && pass "Docker is installed" || fail "Docker is not installed" "run ./install-mac.sh (Mac) or ./install.sh"
if command -v docker &>/dev/null; then
  docker info &>/dev/null && pass "Docker is running" || fail "Docker is not running" "open Docker Desktop and wait for the whale icon to stop animating"
fi
[ -f .env ] && pass ".env exists" || fail ".env is missing" "run ./install-mac.sh (Mac) or ./install.sh"

H=$(curl -sf localhost:8005/api/v1/health || true)
if echo "$H" | grep -q '"status":"ok"'; then
  pass "App is healthy ($(echo "$H" | grep -o '"llm_mode":"[a-z]*"'))"
else fail "App is not answering on port 8005" "run ./start.sh, then check: docker compose logs --tail 40 api"; fi
curl -sf -o /dev/null localhost:3010 && pass "Web page is up at http://localhost:3010" || fail "Web page is not answering on port 3010" "run ./start.sh"

if [ -f data/demo-creds.txt ]; then
  pass "Demo logins are in data/demo-creds.txt"
  E=$(awk '$2=="BUYER"{print $1; exit}' data/demo-creds.txt); P=$(awk '$2=="BUYER"{print $3; exit}' data/demo-creds.txt)
  if [ -n "$E" ] && [ -n "$P" ] && echo "$H" | grep -q ok; then
    J=$(mktemp); trap 'rm -f "$J"' EXIT
    curl -sf -c "$J" -X POST localhost:8005/api/v1/auth/login -H 'Content-Type: application/json' -d "{\"email\":\"$E\",\"password\":\"$P\"}" -o /dev/null \
      && curl -sf -b "$J" localhost:8005/api/v1/projects -o /dev/null \
      && pass "Buyer login works and the project list loads" || fail "Buyer login failed" "the database may predate data/demo-creds.txt; reset with ./stop.sh && docker compose down -v && rm data/demo-creds.txt && ./start.sh"
  fi
else warn "data/demo-creds.txt not found (created on first ./start.sh)"; fi

if command -v ollama &>/dev/null; then
  if curl -sf localhost:11434/api/tags &>/dev/null; then
    pass "Ollama is running"
    M=$(grep '^SL_FAST_MODEL=' .env 2>/dev/null | cut -d= -f2); M=${M:-qwen3.5:4b}
    ollama list | awk 'NR>1{print $1}' | grep -qx "$M" && pass "Model $M is downloaded" || warn "Model $M is not downloaded (only needed for live mode): ollama pull $M"
    L=$(curl -sf localhost:11434/api/ps | grep -o '"name"' | wc -l | tr -d ' ')
    [ "$L" = 0 ] && pass "No model is sitting in memory" || warn "$L model(s) loaded now (normal while a live run is going; they unload on their own)"
  else warn "Ollama is not running (only needed for live mode): open the Ollama app"; fi
else warn "Ollama is not installed (only needed for live mode)"; fi

echo; [ $BAD = 0 ] && printf "${G}All good.${N} Open http://localhost:3010\n" || printf "${R}Fix the FAIL lines above, then run ./doctor.sh again.${N}\n"
exit $BAD
