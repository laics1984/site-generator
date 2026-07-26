#!/usr/bin/env bash
#
# Dev stack control: brings the app up and down together with whatever is
# serving the model.
#
# This script knows NOTHING about models. Which engine runs, which model it
# loads, and whether it is a container here, a host process on a Mac, or a box
# somewhere else are all decisions owned by ai-server/ — see ai-server/ai.sh and
# ai-server/.env. The backend likewise holds only LLM_BASE_URL.
#
# Usage:
#   ./dev.sh up      # start the AI server (if it manages one) + docker compose up -d
#   ./dev.sh down    # docker compose down + stop the AI server
#   ./dev.sh status  # show both
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI="$ROOT/ai-server/ai.sh"

case "${1:-}" in
  up)
    # Start the model first: the backend's first LLM call discovers the model id
    # from /v1/models, and a generation that races an unready server just 502s.
    "$AI" up
    docker compose -f "$ROOT/docker-compose.yml" up -d
    ;;
  down)
    docker compose -f "$ROOT/docker-compose.yml" down
    "$AI" down
    ;;
  status)
    "$AI" status
    echo
    docker compose -f "$ROOT/docker-compose.yml" ps
    ;;
  *)
    echo "usage: $0 {up|down|status}" >&2
    exit 1
    ;;
esac
