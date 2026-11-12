#!/usr/bin/env bash
# Reproducible release build: web UI -> frozen local backend -> Tauri bundles (.deb, .AppImage) -> dist-desktop/.
#
# Needs (build machine only, not on user machines): node >= 22, pnpm, rust >= 1.80, python >= 3.12 with venv,
# and the WebKitGTK/GTK development packages (libwebkit2gtk-4.1-dev, libgtk-3-dev, librsvg2-dev, libssl-dev,
# patchelf). See docs/desktop/BUILD.md. Run from the repository root.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1/5 python build environment"
[ -x .venv-build/bin/python ] || python3 -m venv .venv-build
.venv-build/bin/pip install -q -U pip
.venv-build/bin/pip install -q "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" "pydantic>=2.7" "orjson>=3.10" "numpy>=2.0" "psutil>=6.0" pytest pyinstaller

echo "== 2/5 tests"
.venv-build/bin/python -m pytest tests -q

echo "== 3/5 web UI"
(cd web && pnpm install --frozen-lockfile && pnpm build)
pnpm install --frozen-lockfile

echo "== 4/5 local backend (PyInstaller)"
./scripts/build-backend.sh

echo "== 5/5 desktop bundles"
NO_STRIP=true pnpm tauri build --bundles deb,appimage

mkdir -p dist-desktop
B=src-tauri/target/release/bundle
V=$(sed -n 's/^version = "\(.*\)"/\1/p' src-tauri/Cargo.toml | head -1)
cp "$B/deb/MPN Studio_${V}_amd64.deb" "dist-desktop/mpn-studio_${V}_amd64.deb"
cp "$B/appimage/MPN Studio_${V}_amd64.AppImage" "dist-desktop/MPN-Studio_${V}_x86_64.AppImage"
(cd dist-desktop && sha256sum *.deb *.AppImage > SHA256SUMS && cat SHA256SUMS)
