#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -f data/manifest.json || { echo 'Run ./scripts/bootstrap.sh first.'; exit 1; }
exec python3 -m engine.server --port "${RAZ_PORT:-8765}"
