#!/usr/bin/env bash
# Fill runtime/ with the llama.cpp builds MPN Studio runs models with, for one machine architecture:
#
#   runtime/llama/cpu/      llama-server, portable CPU build (every x86-64 / ARMv8.2+ CPU variant)
#   runtime/llama/vulkan/   x86-64 only: any Vulkan GPU
#   runtime/llama/cuda/     NVIDIA: CUDA 12.8 (x86-64) or CUDA 13.5 (ARM64, includes the DGX Spark's GB10)
#   runtime/convert/        llama.cpp's Hugging Face to GGUF converter (Python source)
#
# Everything comes from the official llama.cpp release b12318 (commit 7021f7e09) and is checked against the SHA-256
# values below before use. Only llama-server and its libraries are kept. Run from the repository root:
#
#   scripts/fetch-runtime.sh [x86_64|aarch64] [--no-cuda]
#
# MPN_DL_CACHE: folder for the downloaded archives (default ~/.cache/mpn-studio-build/llama-b12318). Archives already
# there are reused after their checksum is verified, so the script also works offline once they are present.
set -euo pipefail
cd "$(dirname "$0")/.."

TAG=b12318
BASE=https://github.com/ggml-org/llama.cpp/releases/download/$TAG
ARCH=${1:-$(uname -m)}
CUDA=1
[ "${2:-}" = "--no-cuda" ] && CUDA=0
DL=${MPN_DL_CACHE:-$HOME/.cache/mpn-studio-build/llama-$TAG}
OUT=runtime
mkdir -p "$DL"

declare -A SHA=(
  [b12318.tar.gz]=d7f89e3661163f0c99f9e3f9e329a44bd2dcfbee5e20c1d9d3578400fab46bb9
  [llama-b12318-bin-ubuntu-x64.tar.gz]=092ee3cb21574ff3a451557f7dd45a815a5c8fd1bc62923b48c916d04c5e4443
  [llama-b12318-bin-ubuntu-vulkan-x64.tar.gz]=a13b60a44d40036e29bfb467ecb9743bae8e0400b4f16c0a50329fb645787b74
  [llama-b12318-bin-ubuntu-cuda-12.8-x64.tar.gz]=e5c7ec6d5924059d6222cfad3b266d88fafdb3772700e23f8b0d23f7d68ce797
  [cudart-llama-b12318-bin-ubuntu-cuda-12.8-x64.tar.gz]=caec3617f1fd440b85b80f0c2db031ef483f383a1939c684f0476d1d3086caea
  [llama-b12318-bin-ubuntu-arm64.tar.gz]=38b74378619839dcced1982363a3e74f33978eaaed54f0a4a68fd9043cc85a68
  [llama-b12318-bin-ubuntu-cuda-13.5-arm64.tar.gz]=a90a5f5b7e09316f6624bbbbcd8866347f67953f7ff88960e75270c5662f4e88
  [cudart-llama-b12318-bin-ubuntu-cuda-13.5-arm64.tar.gz]=5028ff9b11a11f85d3b3724bfd63d48ced25166f950d50615910203a615bb9ad
)

fetch() {    # fetch NAME [URL]: download once, verify every time
  local f=$1 url=${2:-$BASE/$1}
  if [ ! -f "$DL/$f" ] || ! echo "${SHA[$f]}  $DL/$f" | sha256sum -c --status; then
    echo "downloading $f"
    curl -fL --retry 3 -o "$DL/$f.part" "$url"
    mv "$DL/$f.part" "$DL/$f"
  fi
  echo "${SHA[$f]}  $DL/$f" | sha256sum -c --quiet || { echo "checksum mismatch: $f" >&2; exit 1; }
}

# unpack ARCHIVE into runtime/llama/VARIANT, keeping llama-server and its libraries only
unpack() {
  local archive=$1 variant=$2 tmp
  tmp=$(mktemp -d)
  tar -xzf "$DL/$archive" -C "$tmp"
  local src
  src=$(dirname "$(find "$tmp" -name llama-server -type f | head -1)")
  rm -rf "$OUT/llama/$variant"
  mkdir -p "$OUT/llama/$variant"
  cp -a "$src"/llama-server "$OUT/llama/$variant/"
  for f in "$src"/lib*.so*; do
    b=$(basename "$f")
    case "$b" in
      libllama-server-impl.so|libllama-fit-params-impl.so) ;;
      libllama-*-impl.so) continue ;;            # libraries of the other llama.cpp tools
    esac
    cp -a "$f" "$OUT/llama/$variant/"
  done
  cp "$tmp"/*/LICENSE "$OUT/llama/$variant/LICENSE" 2>/dev/null || tar -xzf "$DL/b12318.tar.gz" -O llama.cpp-$TAG/LICENSE > "$OUT/llama/$variant/LICENSE"
  rm -rf "$tmp"
  flatten "$OUT/llama/$variant"
}

# Installers copy symlinks as full files, so keep one real file per library, under the name the loader asks for
# (the SONAME, e.g. libggml-base.so.0), and drop the development link and the fully versioned file.
flatten() {
  local d=$1 l
  for l in "$d"/*.so; do [ -L "$l" ] && rm "$l"; done
  for l in "$d"/*.so.[0-9]*; do
    [ -L "$l" ] || continue
    cp -L --remove-destination "$l" "$l.real" && rm "$l" && mv "$l.real" "$l"
  done
  for l in "$d"/*.so.[0-9]*.[0-9]*; do
    [ -f "$l" ] && [ -f "${l%.*.*}" ] && rm "$l"
  done
  return 0
}

cudart() {    # the NVIDIA CUDA runtime libraries llama.cpp's CUDA build needs (redistributable under the CUDA EULA)
  local archive=$1 tmp
  tmp=$(mktemp -d)
  tar -xzf "$DL/$archive" -C "$tmp"
  find "$tmp" -name 'lib*.so*' -exec cp -a {} "$OUT/llama/cuda/" \;
  cat > "$OUT/llama/cuda/NVIDIA-NOTICE.txt" <<'EOF'
libcudart, libcublas and libcublasLt are NVIDIA CUDA Toolkit runtime components, included unmodified as
redistributable software under the NVIDIA CUDA Toolkit End User License Agreement, Attachment A:
https://docs.nvidia.com/cuda/eula/index.html
They come from the official llama.cpp b12318 release archive (cudart-llama-b12318-bin-ubuntu-*.tar.gz).
They are not covered by MPN Studio's Apache-2.0 licence.
EOF
  rm -rf "$tmp"
}

fetch b12318.tar.gz https://github.com/ggml-org/llama.cpp/archive/refs/tags/$TAG.tar.gz
case "$ARCH" in
  x86_64|amd64)
    fetch llama-b12318-bin-ubuntu-x64.tar.gz
    fetch llama-b12318-bin-ubuntu-vulkan-x64.tar.gz
    unpack llama-b12318-bin-ubuntu-x64.tar.gz cpu
    unpack llama-b12318-bin-ubuntu-vulkan-x64.tar.gz vulkan
    if [ $CUDA = 1 ]; then
      fetch llama-b12318-bin-ubuntu-cuda-12.8-x64.tar.gz
      fetch cudart-llama-b12318-bin-ubuntu-cuda-12.8-x64.tar.gz
      unpack llama-b12318-bin-ubuntu-cuda-12.8-x64.tar.gz cuda
      cudart cudart-llama-b12318-bin-ubuntu-cuda-12.8-x64.tar.gz
    fi ;;
  aarch64|arm64)
    fetch llama-b12318-bin-ubuntu-arm64.tar.gz
    unpack llama-b12318-bin-ubuntu-arm64.tar.gz cpu
    rm -rf "$OUT/llama/vulkan"
    if [ $CUDA = 1 ]; then
      fetch llama-b12318-bin-ubuntu-cuda-13.5-arm64.tar.gz
      fetch cudart-llama-b12318-bin-ubuntu-cuda-13.5-arm64.tar.gz
      unpack llama-b12318-bin-ubuntu-cuda-13.5-arm64.tar.gz cuda
      cudart cudart-llama-b12318-bin-ubuntu-cuda-13.5-arm64.tar.gz
    fi ;;
  *) echo "unsupported architecture: $ARCH" >&2; exit 2 ;;
esac

# the converter: plain Python, the same on every architecture
rm -rf "$OUT/convert"
mkdir -p "$OUT/convert"
tar -xzf "$DL/b12318.tar.gz" -C "$OUT/convert" --strip-components=1 \
  llama.cpp-$TAG/convert_hf_to_gguf.py llama.cpp-$TAG/conversion llama.cpp-$TAG/gguf-py llama.cpp-$TAG/LICENSE
rm -rf "$OUT/convert/gguf-py/tests" "$OUT/convert/gguf-py/examples"
# one licence file for llama.cpp and the code compiled into llama-server, taken from the same source archive
src() { tar -xzf "$DL/b12318.tar.gz" -O "llama.cpp-$TAG/$1"; }
{
  echo "llama-server and the converter in this folder come from llama.cpp $TAG (https://github.com/ggml-org/llama.cpp),"
  echo "unmodified. llama.cpp and the code compiled into it are under the licences below."
  for p in "llama.cpp:LICENSE" "nlohmann/json:licenses/LICENSE-jsonhpp" "cpp-httplib:vendor/cpp-httplib/LICENSE" \
           "sha256 (vendor/hash):vendor/hash/sha256/LICENSE" "xxHash (vendor/hash):vendor/hash/xxhash/LICENSE" \
           "rotate-bits (vendor/hash):vendor/hash/rotate-bits/LICENSE.md" "gguf-py:gguf-py/LICENSE"; do
    printf '\n\n==== %s ====\n\n' "${p%%:*}"; src "${p#*:}"
  done
  printf '\n\n==== stb_image (vendor/stb/stb_image.h) ====\n\n'
  src vendor/stb/stb_image.h | awk '/This software is available under 2 licenses/{p=1} p'
  printf '\n\n==== miniaudio (vendor/miniaudio/miniaudio.h) ====\n\n'
  src vendor/miniaudio/miniaudio.h | awk '/This software is available as a choice of the following licenses/{p=1} p'
  printf '\n\n==== subprocess.h (vendor/sheredom/subprocess.h) ====\n\n'
  src vendor/sheredom/subprocess.h | awk '/free and unencumbered/{p=1} p && !d{print} p && /unlicense.org/{d=1}'
} > "$OUT/LICENSES-llama.cpp.txt"
echo "{\"llama_cpp\": \"$TAG\", \"commit\": \"7021f7e09\", \"arch\": \"$ARCH\", \"variants\": [$(ls "$OUT/llama" | sed 's/.*/"&"/' | paste -sd,)]}" > "$OUT/RUNTIME.json"
cat "$OUT/RUNTIME.json"
du -sh "$OUT"/llama/* "$OUT/convert"
