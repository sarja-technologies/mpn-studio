#!/usr/bin/env bash
# Build the self-contained local backend (no system Python needed to run it).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv-build/bin/python}
rm -rf build/backend-work build/backend
"$PY" -m PyInstaller --noconfirm --clean --distpath build/backend-dist --workpath build/backend-work packaging/backend/mpn-backend.spec
mkdir -p build/backend
rm -rf build/backend/*; cp -a build/backend-dist/mpn-backend/. build/backend/
# Plain sources for an external Python (Settings, Compute) to run the runtime and worker from; no model stack is shipped.
mkdir -p build/backend/pysrc
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' mpnstudio build/backend/pysrc/
du -sh build/backend
