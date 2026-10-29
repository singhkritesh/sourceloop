#!/usr/bin/env bash
# One-time setup on a new machine: system deps (Docker, Python 3.13, Node, Ollama), venv + web deps, .env with generated secrets.
# Usage: ./install.sh [--docker-only] [--with-models]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
info(){ printf "${C}[INFO]${N}  %s\n" "$*"; }; ok(){ printf "${G}[OK]${N}    %s\n" "$*"; }
warn(){ printf "${Y}[WARN]${N}  %s\n" "$*"; }; die(){ printf "${R}[ERROR]${N} %s\n" "$*" >&2; exit 1; }
DOCKER_ONLY=false; MODELS=false
for a in "$@"; do case "$a" in --docker-only) DOCKER_ONLY=true;; --with-models) MODELS=true;; *) die "unknown flag $a";; esac; done

OS="$(uname -s)"
have(){ command -v "$1" &>/dev/null; }
APT_UPDATED=false
if [ "$OS" = Darwin ]; then
  # brew is only needed if something is actually missing; checked lazily in need_brew
  need_brew(){ have brew || die "Homebrew missing (needed to install $1). Install from https://brew.sh then re-run."; }
  pkg(){ need_brew "$1"; brew list "$1" &>/dev/null || brew install "$1"; }
elif have apt-get; then
  pkg(){ $APT_UPDATED || { sudo apt-get update -y; APT_UPDATED=true; }; sudo apt-get install -y "$@"; }
else
  pkg(){ die "Unsupported OS: install $* manually."; }
fi
skip(){ ok "$1 already installed — skipping"; }

# ---- Docker ----
if have docker || [ -d /Applications/Docker.app ]; then
  skip "docker"
else
  info "Docker not found — installing"
  if [ "$OS" = Darwin ]; then need_brew docker; brew install --cask docker; else pkg docker.io docker-compose-v2; fi
fi
if have docker && ! docker compose version &>/dev/null; then warn "docker compose plugin missing — install Docker Desktop or docker-compose-v2"; fi

# ---- Ollama (host-side; optional unless LLM_MODE=live) ----
if have ollama || [ -d /Applications/Ollama.app ]; then
  skip "ollama"
else
  info "Ollama not found — installing"
  if [ "$OS" = Darwin ]; then need_brew ollama; brew install --cask ollama; else curl -fsSL https://ollama.com/install.sh | sh; fi
fi
if $MODELS && have ollama; then
  curl -sf localhost:11434/api/tags &>/dev/null || { (ollama serve &>/dev/null &); sleep 4; }
  for m in gemma4:e2b-mlx; do
    if ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$m"; then skip "model $m"; else ollama pull "$m"; fi
  done
fi

# ---- local (non-Docker) toolchain ----
if ! $DOCKER_ONLY; then
  PY=$(command -v python3.13 || true)
  if [ -z "$PY" ]; then
    PY=$(command -v python3 || true)
    if [ -n "$PY" ] && "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)'; then skip "python ($("$PY" -V))"
    else info "Python >=3.12 not found — installing"; if [ "$OS" = Darwin ]; then pkg python@3.13; else pkg python3.13 python3.13-venv; fi; PY=$(command -v python3.13); fi
  else skip "python3.13"; fi
  if have node; then skip "node $(node -v)"; else info "node not found — installing"; pkg node; fi

  [ -d .venv ] && skip ".venv" || "$PY" -m venv .venv
  # reinstall only if the venv does not already satisfy the lock (pip --dry-run reports nothing to install)
  if .venv/bin/pip install --dry-run -q -r requirements-dev.lock 2>&1 | grep -q "Would install"; then
    info "installing python deps from lock"; .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements-dev.lock
  else skip "python deps (match requirements-dev.lock)"; fi
  .venv/bin/python -c 'import sourceloop' 2>/dev/null && skip "sourceloop package" || .venv/bin/pip install -q --no-deps -e .
  # npm ci only when node_modules is missing or older than the lockfile
  if [ -d web/node_modules ] && [ web/node_modules -nt web/package-lock.json ]; then skip "web deps"
  else info "installing web deps"; (cd web && npm ci --silent); fi
fi

# ---- .env with generated secrets (never overwrites) ----
if [ ! -f .env ]; then
  cp .env.example .env
  PY=${PY:-python3}
  J=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(48))')
  S=$("$PY" -c 'import secrets;print(secrets.token_urlsafe(48))')
  F=$("$PY" -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')  # a Fernet key is urlsafe-b64 of 32 bytes
  sed -i.bak "s|^JWT_SECRET=.*|JWT_SECRET=$J|; s|^ORG_SIGNING_SECRET=.*|ORG_SIGNING_SECRET=$S|; s|^CONFIG_ENCRYPTION_KEY=.*|CONFIG_ENCRYPTION_KEY=$F|" .env && rm -f .env.bak
  ok ".env created with generated JWT/signing/vault secrets"
else ok ".env already exists (left untouched)"; fi
[ -f llm.config.yaml ] || { [ -f llm.config.example.yaml ] && cp llm.config.example.yaml llm.config.yaml || true; }

printf "\n${G}Install done.${N} Next: ./start.sh   (Docker)  or  ./start.sh --local\n"
