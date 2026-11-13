#!/usr/bin/env bash
# Build llama.cpp b12318's llama-server with CUDA from source into runtime/llama/cuda/, replacing the variant that
# scripts/fetch-runtime.sh unpacked. Used for ARM64: the official CUDA 13.5 ARM64 archive runs on the DGX Spark's GB10,
# but at about an eighth of a native build's speed (measured: 43 vs 347 tokens/s with Qwen2.5-0.5B Q4_K_M).
#
#   scripts/build-llama-cuda.sh [CUDA_ARCHITECTURES]     default: 121a-real;90-real (GB10 DGX Spark; GH200)
#
# Needs the CUDA toolkit (nvcc, /usr/local/cuda), cmake, ninja or make, and gcc. The CPU code does not depend on the
# build machine's CPU: on ARM64 it is one ARMv8.2 (dotprod, fp16) backend, since the GPU does the work in this variant
# and GCC 13 cannot build llama.cpp's SME variant; on x86-64 it is every CPU variant, loaded at run time.
# The source is the same checksummed archive fetch-runtime.sh uses. Run from the repository root after fetch-runtime.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

TAG=b12318
ARCHS=${1:-121a-real;90-real}
DL=${MPN_DL_CACHE:-$HOME/.cache/mpn-studio-build/llama-$TAG}
SHA=d7f89e3661163f0c99f9e3f9e329a44bd2dcfbee5e20c1d9d3578400fab46bb9
echo "$SHA  $DL/$TAG.tar.gz" | sha256sum -c --quiet || { echo "run scripts/fetch-runtime.sh first (source archive missing or wrong)" >&2; exit 1; }
[ -d runtime/llama/cuda ] || { echo "runtime/llama/cuda is missing: run scripts/fetch-runtime.sh first (it supplies the CUDA runtime libraries)" >&2; exit 1; }

W=${MPN_LLAMA_BUILD:-$PWD/build/llama-cuda}
if [ "${MPN_REBUILD:-0}" != 1 ] && [ -x "$W/out/bin/llama-server" ] && [ "$(cat "$W/ARCHS" 2>/dev/null)" = "$TAG $ARCHS" ]; then
  echo "reusing the finished build in $W (MPN_REBUILD=1 to build again)"
else
  rm -rf "$W"
  mkdir -p "$W"
  tar -xzf "$DL/$TAG.tar.gz" -C "$W"
  export PATH=/usr/local/cuda/bin:$PATH
  CPU=(-DGGML_CPU_ALL_VARIANTS=ON)
  [ "$(uname -m)" = aarch64 ] && CPU=(-DGGML_CPU_ALL_VARIANTS=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16)
  G=Ninja
  command -v ninja >/dev/null || G="Unix Makefiles"
  nice -n 15 cmake -S "$W/llama.cpp-$TAG" -B "$W/out" -G "$G" -DCMAKE_BUILD_TYPE=Release \
    -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$ARCHS" \
    -DGGML_NATIVE=OFF -DGGML_BACKEND_DL=ON "${CPU[@]}" \
    -DLLAMA_BUILD_NUMBER=12318 -DLLAMA_BUILD_COMMIT=7021f7e09 \
    -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DGGML_CCACHE=OFF \
    -DCMAKE_INSTALL_RPATH='$ORIGIN' -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON > "$W/cmake.log" 2>&1
  # a full build: the dynamically loaded CPU variants are separate targets
  nice -n 15 cmake --build "$W/out" -j "${JOBS:-8}" > "$W/build.log" 2>&1 || { tail -30 "$W/build.log" >&2; exit 1; }
  echo "$TAG $ARCHS" > "$W/ARCHS"
fi

D=runtime/llama/cuda
B=$W/out/bin
# keep the CUDA runtime libraries and notices from fetch-runtime.sh, replace everything llama.cpp built
find "$D" -maxdepth 1 \( -name 'libggml*' -o -name 'libllama*' -o -name 'libmtmd*' -o -name llama-server \) -delete
cp -a "$B"/llama-server "$D/"
for f in "$B"/lib*.so*; do
  b=$(basename "$f")
  case "$b" in
    libllama-server-impl.so|libllama-fit-params-impl.so) ;;
    libllama-*-impl.so) continue ;;
  esac
  cp -a "$f" "$D/"
done
# same layout rule as fetch-runtime.sh: one real file per library, under its SONAME
for l in "$D"/*.so; do [ -L "$l" ] && rm "$l"; done
for l in "$D"/*.so.[0-9]*; do [ -L "$l" ] && cp -L --remove-destination "$l" "$l.real" && rm "$l" && mv "$l.real" "$l"; done
for l in "$D"/*.so.[0-9]*.[0-9]*; do [ -f "$l" ] && [ -f "${l%.*.*}" ] && rm "$l"; done
echo "built from source $TAG with CUDA architectures $ARCHS" > "$D/BUILD.txt"
python3 - "$ARCHS" <<'EOF'
import json, sys
p = "runtime/RUNTIME.json"
d = json.load(open(p))
d["cuda_build"] = {"source": "llama.cpp b12318 (7021f7e09)", "architectures": sys.argv[1]}
json.dump(d, open(p, "w"))
EOF
"$D/llama-server" --version 2>&1 | tail -2
du -sh "$D"
