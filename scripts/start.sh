#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -f data/manifest.json || { echo 'Run ./scripts/bootstrap.sh first.'; exit 1; }
args=(--port "${RAZ_PORT:-8765}")
if [[ -n "${RAZ_SYNC_ORIGIN:-}" ]]; then
  args+=(--sync-origin "$RAZ_SYNC_ORIGIN")
fi
exec python3 -m engine.server "${args[@]}"
