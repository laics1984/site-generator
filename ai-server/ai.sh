#!/usr/bin/env bash
#
# AI server control — the single owner of the LLM lifecycle, on any host.
#
# Everything about WHICH model runs and HOW lives in ai-server/.env; the
# site-generator backend only ever holds a URL. That means this script, not
# dev.sh and not the backend, decides whether the model is a container on this
# box, a host-native process on a Mac, or something already running elsewhere.
#
# Engines (LLM_ENGINE in ai-server/.env):
#   ollama    — container, GPU, model by registry tag
#   llamacpp  — container, GPU, model by HuggingFace GGUF spec
#   mlx       — HOST-NATIVE process (Apple Silicon only — Metal can't be
#               containerized, so there is no image to run)
#   external  — already running elsewhere; health-check only, never start/stop
#
# Usage:
#   ./ai.sh up       # start the engine (if any) and wait until it serves /v1/models
#   ./ai.sh down     # stop it
#   ./ai.sh status   # engine, readiness, container state
#   ./ai.sh logs     # follow engine logs
#   ./ai.sh pull     # fetch/refresh the model named in .env
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"

# Read a KEY from .env (last wins); $2 = fallback.
#
# Strips an inline ` # comment` and surrounding quotes. Both matter: a trailing
# comment on a value line is easy to write and silently corrupts the value —
# `LLM_MODEL=some-model #old-model` would otherwise be pulled verbatim and fail
# with "invalid model name". Only a `#` preceded by whitespace starts a comment,
# so it can't eat a `#` that is genuinely part of a value.
get_env() {
  local line val
  line="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1)"
  [ -z "$line" ] && { echo "$2"; return; }
  val="${line#*=}"                                        # drop KEY=
  val="$(printf '%s' "$val" | sed -E 's/[[:space:]]+#.*$//')"  # drop inline comment
  val="$(printf '%s' "$val" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"  # trim
  val="${val%\"}"; val="${val#\"}"                        # strip double quotes
  val="${val%\'}"; val="${val#\'}"                        # strip single quotes
  echo "${val:-$2}"
}

LLM_ENGINE="$(get_env LLM_ENGINE ollama)"
LLM_MODEL="$(get_env LLM_MODEL qwen3.6:35b-a3b)"
OLLAMA_PORT="$(get_env OLLAMA_PORT 11434)"
LLAMACPP_PORT="$(get_env LLAMACPP_PORT 8080)"
MLX_PORT="$(get_env MLX_PORT 8080)"
LLM_CTX="$(get_env LLM_CTX 16384)"
TAILSCALE_ENABLED="$(get_env TAILSCALE_ENABLED 0)"
EXTERNAL_BASE_URL="$(get_env EXTERNAL_BASE_URL "")"
MLX_VENV="$(get_env MLX_VENV "$HOME/mlx-venv")"
MLX_VENV="${MLX_VENV/#\~/$HOME}"      # .env can't expand ~ itself
MLX_LOG="$HOME/Library/Logs/mlx-server.log"
MLX_PID_FILE="$HOME/.mlx-server.pid"

# Where this engine serves. Probed from the HOST shell, so host.docker.internal
# (which resolves only inside containers) is rewritten to localhost.
case "$LLM_ENGINE" in
  ollama)   BASE_URL="http://localhost:$OLLAMA_PORT" ;;
  llamacpp) BASE_URL="http://localhost:$LLAMACPP_PORT" ;;
  mlx)      BASE_URL="http://localhost:$MLX_PORT" ;;
  external) BASE_URL="${EXTERNAL_BASE_URL%/}" ;;
  *) echo "ERROR: unknown LLM_ENGINE='$LLM_ENGINE' (ollama|llamacpp|mlx|external)" >&2; exit 1 ;;
esac
PROBE_URL="${BASE_URL//host.docker.internal/localhost}"

compose() {
  local files=(-f "$ROOT/docker-compose.yml")
  [ "$TAILSCALE_ENABLED" = "1" ] && files+=(-f "$ROOT/docker-compose.tailscale.yml")
  docker compose "${files[@]}" --profile "$LLM_ENGINE" "$@"
}

# Ready == serves the OpenAI model list, which is exactly what the backend needs.
#
# Tries the configured URL first, then the localhost-rewritten form. Both are
# needed: `host.docker.internal` resolves from a WSL shell when Docker Desktop is
# installed but NOT from a plain macOS/Linux host shell, so neither address works
# everywhere. Probing as-is first means a legitimately reachable URL is never
# discarded by the rewrite.
llm_up() {
  curl -sf -m 3 "$BASE_URL/v1/models" >/dev/null 2>&1 && return 0
  [ "$PROBE_URL" != "$BASE_URL" ] || return 1
  curl -sf -m 3 "$PROBE_URL/v1/models" >/dev/null 2>&1
}

# The backend discovers its model from /v1/models. Ollama advertises EVERY
# pulled model there, not just the one LLM_MODEL names, so with more than one
# present the backend picks the alphabetically first — which may not be the one
# selected here. llama.cpp and MLX serve exactly one, so this can't arise there.
warn_if_ambiguous() {
  [ "$LLM_ENGINE" = "ollama" ] || return 0
  local ids count
  ids="$(curl -sf -m 3 "$PROBE_URL/v1/models" 2>/dev/null \
        | tr ',' '\n' | grep -o '"id":"[^"]*"' | cut -d'"' -f4)" || return 0
  count="$(printf '%s\n' "$ids" | grep -c . || true)"
  [ "${count:-0}" -gt 1 ] || return 0
  echo
  echo "WARNING: this server advertises $count models:"
  printf '  - %s\n' $ids
  echo "  The backend auto-discovers one and will use: $(printf '%s\n' $ids | sort | head -1)"
  echo "  To make LLM_MODEL=$LLM_MODEL authoritative, either remove the others"
  echo "  (docker compose exec ollama ollama rm <tag>) or pin LLM_MODEL in the"
  echo "  site-generator .env."
}

# A model's own Modelfile can pin num_ctx, which OVERRIDES OLLAMA_CONTEXT_LENGTH
# (i.e. LLM_CTX here) — silently. That matters because the backend sizes its
# batches from LLM_CONTEXT_TOKENS, which is supposed to mirror LLM_CTX. Measured
# on a model shipping num_ctx=16384 against LLM_CTX=16384: the KV cache ballooned
# the resident footprint from 28GB to 32GB and pushed the split from 42%/58% to
# 55%/45% CPU/GPU, costing ~6x in latency.
warn_if_ctx_mismatch() {
  [ "$LLM_ENGINE" = "ollama" ] || return 0
  local model_ctx
  # No `exit` in the awk: quitting early SIGPIPEs `ollama show`, and `pipefail`
  # then reports the whole pipeline as failed — which the `|| return 0` below
  # would swallow, silently disabling this check.
  model_ctx="$(compose exec -T ollama ollama show "$LLM_MODEL" 2>/dev/null \
               | awk '$1=="num_ctx"{n=$2} END{print n}')" || return 0
  [ -n "$model_ctx" ] || return 0
  # `!=` with `||`, not `=` with `&&`: under `set -e` an AND-list whose test
  # fails returns 1 and aborts the script — silently, right when the values
  # differ and the warning matters most.
  [ "$model_ctx" != "$LLM_CTX" ] || return 0
  echo
  echo "WARNING: $LLM_MODEL pins num_ctx=$model_ctx in its own Modelfile,"
  echo "  overriding LLM_CTX=$LLM_CTX. A far larger context inflates the KV cache and"
  echo "  pushes more of the model into system RAM. To force your value, derive a model:"
  echo "    docker compose exec -T ollama sh -c \\"
  echo "      'printf \"FROM $LLM_MODEL\\nPARAMETER num_ctx $LLM_CTX\\n\" > /tmp/Mf && ollama create my-model -f /tmp/Mf'"
  echo "  then set LLM_MODEL=my-model here. Keep LLM_CONTEXT_TOKENS in the"
  echo "  site-generator .env in step with whatever is actually served."
}

wait_ready() {
  local tries="$1" i
  for i in $(seq 1 "$tries"); do
    llm_up && return 0
    sleep 2
  done
  return 1
}

start_containers() {
  compose up -d
  echo "Waiting for $LLM_ENGINE on $BASE_URL ..."
  # Generous: a first run downloads ~24GB of weights before the server listens.
  if wait_ready 900; then
    echo "$LLM_ENGINE ready at $BASE_URL"
  else
    echo "ERROR: $LLM_ENGINE did not become ready — check: $0 logs" >&2
    exit 1
  fi
}

# MLX has no container image (Apple Silicon/Metal only), so it runs as a host
# process. Moved here from dev.sh so ALL model lifecycle lives in ai-server/.
start_mlx() {
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "ERROR: LLM_ENGINE=mlx requires Apple Silicon — MLX is Metal-only and has no" >&2
    echo "       Linux container image. On this host use LLM_ENGINE=ollama or llamacpp." >&2
    exit 1
  fi
  if llm_up; then echo "MLX already running on :$MLX_PORT."; return; fi
  if [ ! -x "$MLX_VENV/bin/mlx_lm.server" ]; then
    echo "ERROR: $MLX_VENV/bin/mlx_lm.server not found. Install with:" >&2
    echo "       python3 -m venv ~/mlx-venv && ~/mlx-venv/bin/pip install mlx-lm" >&2
    exit 1
  fi
  echo "Starting MLX server ($LLM_MODEL) on :$MLX_PORT ..."
  mkdir -p "$(dirname "$MLX_LOG")"
  nohup "$MLX_VENV/bin/mlx_lm.server" --model "$LLM_MODEL" --port "$MLX_PORT" >"$MLX_LOG" 2>&1 &
  echo $! >"$MLX_PID_FILE"
  if wait_ready 60; then
    echo "MLX up (log: $MLX_LOG)"
  else
    echo "ERROR: MLX did not come up in time — see $MLX_LOG" >&2
    exit 1
  fi
}

stop_mlx() {
  if [ -f "$MLX_PID_FILE" ]; then
    kill "$(cat "$MLX_PID_FILE")" 2>/dev/null || true
    rm -f "$MLX_PID_FILE"
  fi
  pkill -f "mlx_lm.server .*--port $MLX_PORT" 2>/dev/null || true
  echo "MLX stopped."
}

pull_model() {
  case "$LLM_ENGINE" in
    ollama)
      echo "Pulling $LLM_MODEL ..."
      compose exec -T ollama ollama pull "$LLM_MODEL"
      ;;
    llamacpp|mlx)
      echo "$LLM_ENGINE downloads its model on start (LLM_HF_REPO / LLM_MODEL)."
      echo "Run '$0 down && $0 up' after changing it."
      ;;
    external)
      echo "external: the model is managed on the remote host — nothing to pull."
      ;;
  esac
}

case "${1:-}" in
  up)
    case "$LLM_ENGINE" in
      ollama)
        start_containers
        # Auto-pull so swapping models really is a one-line .env change.
        if ! compose exec -T ollama ollama list 2>/dev/null | grep -qF "$LLM_MODEL"; then
          pull_model
        fi
        warn_if_ambiguous
        warn_if_ctx_mismatch
        ;;
      llamacpp) start_containers ;;
      mlx)      start_mlx ;;
      external)
        [ -n "$BASE_URL" ] || { echo "ERROR: EXTERNAL_BASE_URL is unset in $ENV_FILE" >&2; exit 1; }
        if llm_up; then
          echo "External LLM at $BASE_URL: up."
        else
          echo "ERROR: external LLM at $BASE_URL is unreachable." >&2
          echo "       Start it on that host and check connectivity (Tailscale up on both ends?)." >&2
          exit 1
        fi
        ;;
    esac
    ;;
  down)
    case "$LLM_ENGINE" in
      ollama|llamacpp) compose down ;;
      mlx)             stop_mlx ;;
      external)        echo "External LLM at $BASE_URL — nothing to stop locally." ;;
    esac
    ;;
  status)
    echo "LLM_ENGINE=$LLM_ENGINE"
    echo "LLM_MODEL=$LLM_MODEL"
    echo -n "endpoint $BASE_URL: "
    llm_up && echo "up" || echo "DOWN"
    case "$LLM_ENGINE" in
      ollama|llamacpp) compose ps ;;
    esac
    warn_if_ambiguous
    warn_if_ctx_mismatch
    ;;
  logs)
    case "$LLM_ENGINE" in
      ollama|llamacpp) compose logs -f "$LLM_ENGINE" ;;
      mlx)             tail -f "$MLX_LOG" ;;
      external)        echo "External LLM — logs live on that host." ;;
    esac
    ;;
  pull) pull_model ;;
  *)
    echo "usage: $0 {up|down|status|logs|pull}" >&2
    exit 1
    ;;
esac
