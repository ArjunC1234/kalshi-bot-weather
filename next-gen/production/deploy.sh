#!/usr/bin/env bash
set -euo pipefail

HOST_NAME="${1:-root@134.122.9.101}"
REMOTE_PATH="${2:-/opt/kalshi-weather-next-gen}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYABLE_PATH="$SCRIPT_DIR/deployable"

ssh "$HOST_NAME" "mkdir -p '$REMOTE_PATH'"
scp -r "$DEPLOYABLE_PATH"/. "$HOST_NAME:$REMOTE_PATH/"
