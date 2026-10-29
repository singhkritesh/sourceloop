#!/usr/bin/env bash
# One-command setup for a MacBook, sized for a 4b local model (qwen3.5:4b, ~3.4 GB on disk, ~5 GB RAM while loaded).
# Installs what is missing (Homebrew, Docker Desktop, Ollama), pulls the model, writes .env, builds, starts and seeds.
# Usage: ./install-mac.sh [--model qwen3.5:4b] [--no-start] [--live]
#   --live   makes new projects use the local model (LLM_MODE=live); default is replay (recorded answers, no model needed)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
C='\033[0;36m'; G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
info(){ printf "${C}[INFO]${N}  %s\n" "$*"; }; ok(){ printf "${G}[OK]${N}    %s\n" "$*"; }
warn(){ printf "${Y}[WARN]${N}  %s\n" "$*"; }; die(){ printf "${R}[ERROR]${N} %s\n" "$*" >&2; exit 1; }
MODEL="qwen3.5:4b"; START=true; LIVE=false
while [ $# -gt 0 ]; do case "$1" in
  --model) MODEL="${2:?--model needs a name}"; shift 2;;
  --no-start) START=false; shift;;
  --live) LIVE=true; shift;;
  *) die "unknown flag $1";; esac; done

[ "$(uname -s)" = Darwin ] || die "install-mac.sh is for macOS. On Linux use ./install.sh"
[ "$(uname -m)" = arm64 ] || warn "Intel Mac detected: a 4b model will run, but slowly (no Apple GPU)."
GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
[ "$GB" -ge 8 ] || die "${GB} GB RAM: a 4b model needs at least 8 GB."
[ "$GB" -ge 16 ] || warn "${GB} GB RAM: close other apps while a project runs (the model is loaded only during a run)."
FREE=$(df -g . | awk 'NR==2{print $4}'); [ "${FREE:-0}" -ge 15 ] || warn "only ${FREE:-?} GB free; Docker images + model need about 10 GB."
ok "macOS $(sw_vers -productVersion), $(uname -m), ${GB} GB RAM"

# Homebrew (installs Docker Desktop, Ollama, Python, Node); official installer, runs only when missing.
if ! command -v brew &>/dev/null; then
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$($b shellenv)" && break; done
fi
if ! command -v brew &>/dev/null; then
  info "Homebrew not found; installing it (official installer from brew.sh, may ask for your password)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$($b shellenv)" && break; done
  command -v brew &>/dev/null || die "Homebrew install did not finish; install from https://brew.sh and re-run."
fi
ok "Homebrew $(brew --version | head -1)"

# Docker Desktop + Ollama + .env with generated secrets (existing script skips anything already present)
./install.sh --docker-only

# Ollama server, then the model; nothing stays loaded afterwards
if ! curl -sf localhost:11434/api/tags &>/dev/null; then
  info "Starting Ollama"
  open -a Ollama 2>/dev/null || (nohup ollama serve >/dev/null 2>&1 &)
  for _ in $(seq 1 30); do curl -sf localhost:11434/api/tags &>/dev/null && break; sleep 1; done
fi
curl -sf localhost:11434/api/tags &>/dev/null || die "Ollama is not answering on :11434"
if ollama list | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then ok "model $MODEL already pulled"
else info "Pulling $MODEL (one-time download)"; ollama pull "$MODEL"; fi
info "Smoke test: one short answer from $MODEL"
curl -sf localhost:11434/api/chat -d "{\"model\":\"$MODEL\",\"stream\":false,\"think\":false,\"keep_alive\":0,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word ready.\"}],\"options\":{\"num_predict\":16}}" \
  | grep -qi ready && ok "$MODEL answers (and is already unloaded)" || warn "$MODEL gave an unexpected answer; check 'ollama run $MODEL'"

# Point the first Studio config at the 4b model (read once by the seed; Studio edits it afterwards)
setenv(){ if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; else printf '%s=%s\n' "$1" "$2" >> .env; fi; }
setenv SL_FAST_MODEL "$MODEL"; setenv SL_CHAT_MODEL "$MODEL"
$LIVE && setenv LLM_MODE live
ok ".env: fast and chat models = $MODEL, LLM_MODE=$($LIVE && echo live || echo replay)"
[ -f data/demo-creds.txt ] && warn "data/demo-creds.txt exists, so the database is already seeded with its old model names. For a clean start: ./stop.sh && docker compose down -v && rm data/demo-creds.txt"

$START || { printf "\n${G}Setup done.${N} Next: ./start.sh\n"; exit 0; }
./start.sh
./doctor.sh || true
printf "\n  Model:  $MODEL (loaded only while a project runs, unloaded when it finishes or fails)\n  Mode:   switch replay/live in Settings, or re-run with --live\n"
