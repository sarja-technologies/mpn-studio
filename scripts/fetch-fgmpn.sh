#!/usr/bin/env bash
# Install the FG-MPN runtime into the build environment, from the public release, and collect its licence notices:
#
#   https://github.com/sarja-technologies/fgmpn/releases/tag/v0.2.2   (Fylume Source-Available Terms 1.0)
#
#   scripts/fetch-fgmpn.sh [x86_64|aarch64]
#
# The wheel for the machine (native reader on Linux x86-64 and ARM64) is downloaded once, checked against the SHA-256 values
# below every time, and installed into .venv-build. PyInstaller then freezes it into the backend (packaging/backend/mpn-backend.spec).
# Its LICENSE, NOTICE, THIRD-PARTY.md and the Rust crate / zig / Rust standard library notices go to build/fgmpn-licenses/,
# which the desktop bundle ships as licenses/fgmpn/. MPN_DL_CACHE keeps the download.
set -euo pipefail
cd "$(dirname "$0")/.."
VER=0.2.2
ARCH=${1:-$(uname -m)}
DL=${MPN_DL_CACHE:-$HOME/.cache/mpn-studio-build}/fgmpn-$VER
PY=${PY:-.venv-build/bin/python}
case "$ARCH" in
  x86_64)  W=fgmpn-$VER-cp312-abi3-manylinux_2_17_x86_64.whl; H=2bd8b6dc821b0eebfc915cc4999ac2adf3b822ccf61a736c882b7af0b2863284 ;;
  aarch64) W=fgmpn-$VER-cp312-abi3-manylinux_2_34_aarch64.whl; H=5823b11281bb33813babe423c7ad44558832c712c9d2fb37092272e1958b7bbd ;;
  *) echo "no FG-MPN wheel for $ARCH" >&2; exit 1 ;;
esac
mkdir -p "$DL"
if [ ! -f "$DL/$W" ] || ! echo "$H  $DL/$W" | sha256sum -c --status; then
  echo "downloading $W"
  curl -fL --retry 3 -o "$DL/$W.part" "https://github.com/sarja-technologies/fgmpn/releases/download/v$VER/$W"
  mv "$DL/$W.part" "$DL/$W"
fi
echo "$H  $DL/$W" | sha256sum -c --quiet || { echo "checksum mismatch: $W" >&2; exit 1; }
"$PY" -m pip install -q --no-index --force-reinstall --no-deps "$DL/$W"
rm -rf build/fgmpn-licenses && mkdir -p build/fgmpn-licenses
"$PY" - "$DL/$W" build/fgmpn-licenses <<'PYEOF'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
for n in z.namelist():
    if "/licenses/" in n and not n.endswith("/"):
        out = sys.argv[2] + "/" + n.split("/licenses/", 1)[1]
        import os
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(z.read(n))
PYEOF
echo "fgmpn $VER installed from $W; notices in build/fgmpn-licenses/"
