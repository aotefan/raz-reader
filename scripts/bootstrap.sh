#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in python3 pdfinfo qpdf ffmpeg ffprobe; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. See README prerequisites."; exit 1; }
done
test -d .venv || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/prepare_demo.py
echo 'Demo ready. Run ./scripts/start.sh, then open http://127.0.0.1:8765'
