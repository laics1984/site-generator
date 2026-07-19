#!/usr/bin/env bash
#
# Run the backend test suite inside the running dev container.
#
# The runtime image ships only app/ (no tests, no pytest), and the compose file
# bind-mounts just ./backend/app — so we copy the suite + dev deps into the
# running container and invoke pytest there. This is the supported path because
# the project has no local Python toolchain (see the memory / README).
#
# Usage (from anywhere):
#   backend/scripts/run-tests.sh                 # whole suite
#   backend/scripts/run-tests.sh -q tests/test_media.py
#   backend/scripts/run-tests.sh -k overlay -x
#
# Requires: `docker compose up -d backend` already running.
set -euo pipefail

# Stop Git Bash / MSYS on Windows from rewriting the container-side paths
# (e.g. ":/app/tests"). No-op on macOS/Linux.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

CONTAINER="${SITEGEN_BACKEND_CONTAINER:-webtree-sitegen-backend}"
BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "error: container '$CONTAINER' is not running. Start it with: docker compose up -d backend" >&2
  exit 1
fi

# Use relative paths from the backend dir so Windows Git Bash doesn't rewrite an
# absolute POSIX source path (/d/...) into something docker can't read.
cd "$BACKEND_DIR"

echo ">> syncing tests + config into $CONTAINER"
# Remove any prior copy first: `docker cp dir container:/app/tests` NESTS into an
# existing dir (→ /app/tests/tests), and a stale copy could keep deleted files.
docker exec "$CONTAINER" rm -rf /app/tests
docker cp tests                "$CONTAINER:/app/tests"
docker cp pytest.ini           "$CONTAINER:/app/pytest.ini"
docker cp requirements-dev.txt "$CONTAINER:/app/requirements-dev.txt"

echo ">> installing dev deps (pytest, pytest-asyncio)"
docker exec "$CONTAINER" pip install -q -r /app/requirements-dev.txt

echo ">> running pytest"
docker exec -w /app "$CONTAINER" python -m pytest "$@"
