#!/usr/bin/env bash
# Run the test suite on the host venv against a local Postgres container.
# The container listens on a unix socket in .pgsock/, because TCP from the
# host to containers is not reachable on every developer machine.
set -euo pipefail
cd "$(dirname "$0")/.."

SOCKET_DIR="$PWD/.pgsock"
CONTAINER=rmo-test-pg
mkdir -p "$SOCKET_DIR"
chmod 777 "$SOCKET_DIR" 2>/dev/null || true

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" \
        -e POSTGRES_DB=orchestrator -e POSTGRES_USER=orchestrator \
        -e POSTGRES_PASSWORD=orchestrator \
        -v "$SOCKET_DIR:/var/run/postgresql" postgres:14 >/dev/null
fi
until docker exec "$CONTAINER" pg_isready -U orchestrator >/dev/null 2>&1; do
    sleep 1
done

DB_HOST="$SOCKET_DIR" DEBUG=1 GITLAB_WEBHOOK_TOKEN="${GITLAB_WEBHOOK_TOKEN:-}" \
    exec .venv/bin/pytest "$@"
