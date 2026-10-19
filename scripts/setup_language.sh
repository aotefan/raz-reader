#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -d .venv-translation || python3.11 -m venv .venv-translation
.venv-translation/bin/python -m pip install -r requirements-translation.txt
python3 scripts/install_translation.py
python3 scripts/install_dictionary.py
echo 'Optional offline translation and dictionary installed. Restart the reader.'
