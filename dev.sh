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
MLX_MODEL="$(get_env MLX_MODEL mlx-community/Qwen3-4B-4bit)"
MLX_PORT="8080"   # matches MLX_BASE_URL in docker-compose (host.docker.internal:8080)

mlx_up() { curl -sf -m 2 "http://localhost:$MLX_PORT/v1/models" >/dev/null 2>&1; }

start_mlx() {
  if [ "$BACKEND" != "mlx" ]; then
    echo "LLM_BACKEND=$BACKEND — skipping MLX (Ollama serves the LLM)."
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
    mlx_up && echo "MLX: up on :$MLX_PORT ($MLX_MODEL)" || echo "MLX: down"
    docker compose -f "$ROOT/docker-compose.yml" ps
    ;;
  *)
    echo "usage: $0 {up|down|status}" >&2
    exit 1
    ;;
esac
