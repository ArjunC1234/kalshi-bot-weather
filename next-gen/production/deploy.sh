#!/usr/bin/env bash
set -euo pipefail

HOST_NAME="${1:-root@134.122.9.101}"
REMOTE_PATH="${2:-/opt/kalshi-weather-next-gen}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYABLE_PATH="$SCRIPT_DIR/deployable"

ssh "$HOST_NAME" "mkdir -p '$REMOTE_PATH'"
if command -v rsync >/dev/null 2>&1; then
  rsync -avz --delete \
    --exclude ".env" \
    --exclude ".venv/" \
    --exclude "__pycache__/" \
    --exclude "*.pyc" \
    --exclude "*.pem" \
    --exclude "*.key" \
    --exclude "collector_spool*/" \
    --exclude "logs/" \
    --exclude "exports/" \
    "$DEPLOYABLE_PATH"/ "$HOST_NAME:$REMOTE_PATH/"
else
  ARCHIVE="$(mktemp -t kalshi-weather-next-gen-deploy.XXXXXX.tar.gz)"
  trap 'rm -f "$ARCHIVE"' EXIT
  tar \
    --exclude ".env" \
    --exclude ".venv" \
    --exclude "__pycache__" \
    --exclude "*.pyc" \
    --exclude "*.pem" \
    --exclude "*.key" \
    --exclude "collector_spool*" \
    --exclude "logs" \
    --exclude "exports" \
    -czf "$ARCHIVE" -C "$DEPLOYABLE_PATH" .
  scp "$ARCHIVE" "$HOST_NAME:/tmp/kalshi-weather-next-gen-deploy.tar.gz"
  ssh "$HOST_NAME" "cd '$REMOTE_PATH' && tar -xzf /tmp/kalshi-weather-next-gen-deploy.tar.gz && rm -f /tmp/kalshi-weather-next-gen-deploy.tar.gz"
fi
