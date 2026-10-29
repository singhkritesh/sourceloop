#!/usr/bin/env bash
# Build the Docker images (api, web) and CREATE the containers + network + volume, without starting them.
# Usage: ./build.sh [--no-cache]      Then: ./start.sh   (or `docker compose up -d`)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
C='\033[0;36m'; G='\033[0;32m'; R='\033[0;31m'; N='\033[0m'
info(){ printf "${C}[INFO]${N}  %s\n" "$*"; }; ok(){ printf "${G}[OK]${N}    %s\n" "$*"; }; die(){ printf "${R}[ERROR]${N} %s\n" "$*" >&2; exit 1; }

[ -f .env ] || die ".env missing — run ./install.sh first."
command -v docker &>/dev/null || die "docker not installed — run ./install.sh"
if ! docker info &>/dev/null; then
  [ "$(uname -s)" = Darwin ] && { info "Starting Docker Desktop"; open -a Docker; }
  for _ in $(seq 1 90); do docker info &>/dev/null && break; sleep 2; done
  docker info &>/dev/null || die "Docker daemon is not running."
fi
ok "Docker is running"

docker compose config -q || die "docker-compose.yml is invalid"
info "Building images (api, web)"
docker compose build ${1:+"$1"}
info "Creating containers (not starting)"
docker compose up --no-start
ok "Built and created:"
docker compose images 2>/dev/null || true
docker compose ps -a --format '  {{.Name}}  {{.Status}}'
