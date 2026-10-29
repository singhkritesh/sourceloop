#!/usr/bin/env bash
# Start SourceLoop. Default: Docker (api + web). --local: venv uvicorn on 8005 + vite on 3010.
# First run migrates and seeds; seed prints random demo credentials ONCE (saved to data/demo-creds.txt, gitignored).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
C='\033[0;36m'; G='\033[0;32m'; R='\033[0;31m'; N='\033[0m'
info(){ printf "${C}[INFO]${N}  %s\n" "$*"; }; ok(){ printf "${G}[OK]${N}    %s\n" "$*"; }; die(){ printf "${R}[ERROR]${N} %s\n" "$*" >&2; exit 1; }
LOCAL=false; [ "${1:-}" = "--local" ] && LOCAL=true
[ -f .env ] || die ".env missing — run ./install.sh first."
mkdir -p data

wait_health(){ for i in $(seq 1 60); do curl -sf localhost:8005/api/v1/health | grep -q ok && return 0; sleep 1; done; return 1; }

if $LOCAL; then
  [ -x .venv/bin/uvicorn ] || die "venv missing — run ./install.sh"
  ./stop.sh --quiet || true
  set -a; . ./.env; set +a
  .venv/bin/alembic upgrade head
  nohup .venv/bin/uvicorn sourceloop.main:app --host 127.0.0.1 --port 8005 > data/api.log 2>&1 & echo $! > data/api.pid
  (cd web && nohup npm run dev > ../data/web.log 2>&1 & echo $! > ../data/web.pid)
  wait_health || die "API unhealthy — see data/api.log"
  SEED="$PWD/.venv/bin/python -m sourceloop.seed"
else
  ./build.sh                       # checks/starts Docker, builds images, creates containers (cached = fast)
  docker compose up -d
  wait_health || { docker compose logs --tail 40 api; die "API unhealthy"; }
  SEED="docker compose exec -T api python -m sourceloop.seed"
fi
ok "API healthy"

# seed only when there are no users yet; keep the one-time credentials
if [ ! -f data/demo-creds.txt ]; then
  info "Seeding demo data"
  $SEED 2>&1 | grep -v warning | tee data/demo-creds.txt >/dev/null || true
fi
printf "\n${G}  App (open this):  http://localhost:3010${N}\n  API health:       curl localhost:8005/api/v1/health\n  Demo logins:      cat data/demo-creds.txt\n  Stop:             ./stop.sh\n\n"
