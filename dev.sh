#!/usr/bin/env bash
#
# Dev stack control: brings the host-side MLX server up/down *together* with the
# Docker backend, so the ~4.5GB model is only resident while you're running the
# app (on-demand, not a permanent launchd service).
#
# MLX can't run inside the Linux backend container, so it runs natively on the
# host and the container reaches it via host.docker.internal (see docker-compose).
# This script only manages MLX when LLM_BACKEND=mlx in .env — with LLM_BACKEND=
# ollama (or unset) it just runs Docker and leaves the LLM to Ollama.
#
# Remote mode: when MLX_BASE_URL in .env points at a non-local host (the RTX
# AI server over Tailscale — see ai-server/), there is no local server to
# manage; `up` just health-checks the remote endpoint and fails fast if it's
# unreachable.
#
# Usage:
#   ./dev.sh up      # start MLX (if mlx backend) + docker compose up -d
#   ./dev.sh down    # docker compose down + stop MLX
#   ./dev.sh status  # show both
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"
MLX_VENV="$HOME/mlx-venv"
MLX_LOG="$HOME/Library/Logs/mlx-server.log"
MLX_PID_FILE="$HOME/.mlx-server.pid"

# Read a KEY from .env (last wins), stripped of surrounding quotes; $2 = fallback.
get_env() {
  local line val
  line="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1)"
  val="${line#*=}"          # drop KEY=
  val="${val%\"}"; val="${val#\"}"   # strip surrounding double quotes
  echo "${val:-$2}"
}

BACKEND="$(get_env LLM_BACKEND ollama)"
MLX_MODEL="$(get_env MLX_MODEL mlx-community/Qwen3.5-2B-OptiQ-4bit)"
MLX_BASE_URL="$(get_env MLX_BASE_URL http://localhost:8080)"
MLX_PORT="8080"   # local mlx_lm.server port; matches the MLX_BASE_URL default

# A non-local MLX_BASE_URL means an OpenAI-compatible server elsewhere (the RTX
# AI box over Tailscale) — nothing to start or stop on this machine.
case "$MLX_BASE_URL" in
  http://localhost:*|http://127.0.0.1:*) MLX_REMOTE=0 ;;
  *) MLX_REMOTE=1 ;;
esac

mlx_up() { curl -sf -m 3 "$MLX_BASE_URL/v1/models" >/dev/null 2>&1; }

start_mlx() {
  if [ "$BACKEND" != "mlx" ]; then
    echo "LLM_BACKEND=$BACKEND — skipping MLX (Ollama serves the LLM)."
    return
  fi
  if [ "$MLX_REMOTE" = "1" ]; then
    if mlx_up; then
      echo "Remote LLM at $MLX_BASE_URL: up."
    else
      echo "ERROR: remote LLM at $MLX_BASE_URL is unreachable." >&2
      echo "Start it on the AI server (ai-server/: docker compose up -d) and check Tailscale on both ends." >&2
      exit 1
    fi
    return
  fi
  if mlx_up; then echo "MLX already running on :$MLX_PORT."; return; fi
  if [ ! -x "$MLX_VENV/bin/mlx_lm.server" ]; then
    echo "ERROR: $MLX_VENV/bin/mlx_lm.server not found. Install with: python3 -m venv ~/mlx-venv && ~/mlx-venv/bin/pip install mlx-lm" >&2
    exit 1
  fi
  echo "Starting MLX server ($MLX_MODEL) on :$MLX_PORT ..."
  nohup "$MLX_VENV/bin/mlx_lm.server" --model "$MLX_MODEL" --port "$MLX_PORT" >"$MLX_LOG" 2>&1 &
  echo $! >"$MLX_PID_FILE"
  for _ in $(seq 1 40); do
    if mlx_up; then echo "MLX up (log: $MLX_LOG)"; return; fi
    sleep 1
  done
  echo "ERROR: MLX did not come up in time — see $MLX_LOG" >&2
  exit 1
}

stop_mlx() {
  if [ "$MLX_REMOTE" = "1" ]; then
    echo "Remote LLM at $MLX_BASE_URL — nothing to stop locally."
    return
  fi
  if [ -f "$MLX_PID_FILE" ]; then
    kill "$(cat "$MLX_PID_FILE")" 2>/dev/null || true
    rm -f "$MLX_PID_FILE"
  fi
  pkill -f "mlx_lm.server .*--port $MLX_PORT" 2>/dev/null || true
  echo "MLX stopped (freed ~4.5GB)."
}

case "${1:-}" in
  up)
    start_mlx
    docker compose -f "$ROOT/docker-compose.yml" up -d
    ;;
  down)
    docker compose -f "$ROOT/docker-compose.yml" down
    stop_mlx
    ;;
  status)
    echo "LLM_BACKEND=$BACKEND"
    if [ "$MLX_REMOTE" = "1" ]; then
      mlx_up && echo "Remote LLM: up at $MLX_BASE_URL" || echo "Remote LLM: DOWN at $MLX_BASE_URL"
    else
      mlx_up && echo "MLX: up on :$MLX_PORT ($MLX_MODEL)" || echo "MLX: down"
    fi
    docker compose -f "$ROOT/docker-compose.yml" ps
    ;;
  *)
    echo "usage: $0 {up|down|status}" >&2
    exit 1
    ;;
esac
