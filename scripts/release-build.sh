#!/usr/bin/env bash
# Reproducible release build for the machine it runs on (x86-64 or ARM64):
# tests -> web UI -> frozen local backend -> llama.cpp runtime -> Tauri bundles (.deb, .AppImage) -> dist-desktop/.
#
# Needs (build machine only, not on user machines): rust >= 1.80 with tauri-cli 2 (pnpm's, or `cargo install
# tauri-cli`), python >= 3.12 with venv, the WebKitGTK/GTK development packages (libwebkit2gtk-4.1-dev, libgtk-3-dev,
# librsvg2-dev, libssl-dev) and patchelf; node >= 22 with pnpm unless MPN_PREBUILT_WEB=1. On ARM64 with the CUDA
# toolkit, the CUDA runtime is built from source (scripts/build-llama-cuda.sh). See docs/desktop/BUILD.md.
#
#   MPN_PREBUILT_WEB=1   use web/dist as it is (built on another machine: the UI is the same on every architecture)
#   MPN_DL_CACHE=DIR     where the pinned llama.cpp archives are kept (see scripts/fetch-runtime.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
ARCH=$(uname -m)

echo "== 1/6 python build environment"
[ -x .venv-build/bin/python ] || python3 -m venv .venv-build
.venv-build/bin/pip install -q -U pip
.venv-build/bin/pip install -q "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" "pydantic>=2.7" "orjson>=3.10" "numpy>=2.0" "psutil>=6.0" pytest pyinstaller

echo "== 2/6 tests"
.venv-build/bin/python -m pytest tests -q

echo "== 3/6 web UI"
if [ "${MPN_PREBUILT_WEB:-0}" = 1 ]; then
  [ -f web/dist/index.html ] || { echo "MPN_PREBUILT_WEB=1 but web/dist is empty" >&2; exit 1; }
  echo "using the prebuilt web/dist"
else
  (cd web && pnpm install --frozen-lockfile && pnpm build)
  pnpm install --frozen-lockfile
fi

echo "== 4/6 local backend (PyInstaller)"
./scripts/build-backend.sh

echo "== 5/6 llama.cpp runtime (pinned, checksummed)"
./scripts/fetch-runtime.sh "$ARCH"
if [ "$ARCH" = aarch64 ] && { command -v nvcc >/dev/null || [ -x /usr/local/cuda/bin/nvcc ]; }; then
  ./scripts/build-llama-cuda.sh
fi

echo "== 6/6 desktop bundles"
# linuxdeploy (AppImage) copies every library an ELF file in the app needs into usr/lib, which the AppImage puts first
# on the library path. For the llama.cpp runtime that is wrong: the NVIDIA driver (libcuda) and the Vulkan loader
# must be the user's own, and the runtime folders already carry their libraries next to each llama-server.
export LINUXDEPLOY_EXCLUDED_LIBRARIES="libcuda.so*;libnvidia*;libvulkan.so*;libggml*;libllama*;libmtmd*;libcublas*;libcudart*"
if [ -x node_modules/.bin/tauri ]; then
  NO_STRIP=true node_modules/.bin/tauri build --bundles deb,appimage
else
  (cd src-tauri && NO_STRIP=true cargo tauri build --bundles deb,appimage)
fi

mkdir -p dist-desktop
B=src-tauri/target/release/bundle
V=$(sed -n 's/^version = "\(.*\)"/\1/p' src-tauri/Cargo.toml | head -1)
case "$ARCH" in
  x86_64) DEB=amd64 AI=amd64 ;;
  aarch64) DEB=arm64 AI=aarch64 ;;
esac
cp "$B/deb/MPN Studio_${V}_${DEB}.deb" "dist-desktop/mpn-studio_${V}_${DEB}.deb"
cp "$B/appimage/MPN Studio_${V}_${AI}.AppImage" "dist-desktop/MPN-Studio_${V}_${ARCH}.AppImage"
(cd dist-desktop && sha256sum "mpn-studio_${V}_${DEB}.deb" "MPN-Studio_${V}_${ARCH}.AppImage" > "SHA256SUMS-${ARCH}" && cat "SHA256SUMS-${ARCH}")
