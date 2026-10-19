#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null
command -v espeak-ng >/dev/null
test -d .venv-assessment || python3.11 -m venv .venv-assessment
.venv-assessment/bin/python -m pip install -r requirements-assessment.lock
python3 scripts/install_assessment_model.py
echo 'Optional offline assessment installed. Restart the reader.'
