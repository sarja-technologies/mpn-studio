# Building and installing

## Install a built package

| Machine | Files |
|---|---|
| x86-64 (Intel, AMD) | `mpn-studio_0.5.0_amd64.deb` or `MPN-Studio_0.5.0_x86_64.AppImage` |
| ARM64 (NVIDIA DGX Spark and other ARMv8.2+ Linux machines) | `mpn-studio_0.5.0_arm64.deb` or `MPN-Studio_0.5.0_aarch64.AppImage` |

    sudo apt install ./mpn-studio_0.5.0_amd64.deb      # menu entry "MPN Studio"; commands: mpn-studio, mpn
    # or, without root:
    chmod +x MPN-Studio_0.5.0_x86_64.AppImage && ./MPN-Studio_0.5.0_x86_64.AppImage
    ./MPN-Studio_0.5.0_x86_64.AppImage mpn run Model.mpn --chat      # the mpn command from the AppImage

The `.deb` needs `libwebkit2gtk-4.1-0` and `libgtk-3-0` (it declares them). No Node.js and no Python are needed.
GPU inference uses the NVIDIA driver (CUDA) or a Vulkan driver (x86-64) that is already installed; without one, models
run on the CPU. Remove with `sudo apt remove mpn-studio`; your library in `~/.local/share/mpn-studio` is not touched.

## Build from source

The release script builds for the machine it runs on:

    scripts/release-build.sh      # FG-MPN runtime -> tests -> web -> frozen backend -> llama.cpp runtime -> .deb + .AppImage -> dist-desktop/

Build machine needs: Rust >= 1.80 with the Tauri 2 CLI (from `pnpm install`, or `cargo install tauri-cli --version 2.12.4
--locked`), Python >= 3.12 with venv, `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libssl-dev patchelf rsync
build-essential`, and Node >= 22 with pnpm for the web interface. Internet is needed once to fetch dependencies and the
pinned llama.cpp archives (`MPN_DL_CACHE` keeps them; the script verifies their SHA-256 every time). The product itself
never needs the internet.

Individual steps:

    cd web && pnpm build                         # the interface (the same files on every architecture)
    scripts/fetch-fgmpn.sh [x86_64|aarch64]      # the public FG-MPN wheel for this machine into .venv-build (sha256-pinned), notices in build/fgmpn-licenses/
    scripts/build-backend.sh                     # frozen backend in build/backend/ (carries the FG-MPN runtime)
    scripts/fetch-runtime.sh [x86_64|aarch64]    # llama.cpp b12318 in runtime/ (cpu, vulkan, cuda on x86-64; cpu, cuda on ARM64)
    scripts/build-llama-cuda.sh                  # ARM64 only: replace the CUDA variant with a source build (below)
    pnpm tauri build --bundles deb,appimage

Development without packaging: `pnpm tauri build --debug --no-bundle`, then
`MPN_DEV_PYTHON=.venv-build/bin/python MPN_DEV_ROOT=$PWD src-tauri/target/debug/mpn-studio` (runs the source backend;
`runtime/` must be filled by `scripts/fetch-runtime.sh`).

Tests: `.venv-build/bin/python -m pytest tests -q` (245 pass; 30 need torch and are skipped; with the FG-MPN wheel installed by `scripts/fetch-fgmpn.sh` the FG-MPN reader tests run).
`scripts/offline-acceptance.sh` runs the installed app with no network (needs `bwrap`, `strace`, `Xvfb`, `xdotool`,
ImageMagick).

## ARM64 and the DGX Spark

The ARM64 packages are built natively on ARM64; nothing x86-64 is used or emulated. Two differences from x86-64:

* **CUDA runtime from source.** llama.cpp's official CUDA 13.5 ARM64 build runs on the DGX Spark's GB10, but measured
  at 43 tokens/s against 347 tokens/s for a native build (Qwen2.5-0.5B Q4_K_M, 256 tokens). `release-build.sh` therefore
  runs `scripts/build-llama-cuda.sh` when the CUDA toolkit is present: llama.cpp b12318 from the same checksummed source
  archive, CUDA architectures `121a-real;90-real` (GB10; GH200), one generic ARMv8.2 CPU backend (GCC 13 cannot build
  llama.cpp's SME variant). It keeps the CUDA runtime libraries from the official archive. A finished build is reused;
  `MPN_REBUILD=1` builds again.
* **Web interface built elsewhere if there is no Node.** The interface is architecture independent. Build `web/dist` on
  any machine, copy it over, and run `MPN_PREBUILT_WEB=1 scripts/release-build.sh`.

### Building without root

The DGX Spark the ARM64 packages were built on has no administrator access for the build user, no Node and no Rust.
This is how it was done, entirely inside the user's home:

1. Rust: `rustup-init.sh -y --profile minimal --no-modify-path`, then `cargo install tauri-cli --version 2.12.4 --locked`.
2. Development headers: list the missing `-dev` packages of `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libssl-dev`
   with `apt-cache depends --recurse`, fetch them with `apt-get download` (no root needed), unpack each with
   `dpkg-deb -x` into a folder (the sysroot), point the `.so` development links at the system's libraries, and rewrite
   `prefix`, `libdir` and `includedir` in the sysroot's `.pc` files to the sysroot. Then
   `PKG_CONFIG_PATH=<sysroot>/usr/lib/aarch64-linux-gnu/pkgconfig:<sysroot>/usr/share/pkgconfig` and
   `LIBRARY_PATH=<sysroot>/usr/lib/aarch64-linux-gnu`.
3. AppImage: linuxdeploy's GTK plugin asks `pkg-config`/`pkgconf --variable=…` where the GTK, GIO and GdkPixbuf modules
   are. Put small `pkg-config` and `pkgconf` wrappers first on `PATH` that strip the sysroot prefix from `--variable`
   answers, so the plugin finds the system's modules. Set `APPIMAGE_EXTRACT_AND_RUN=1` when FUSE is not available.

## Notes

* No absolute developer paths are shipped; resources are found through the app's resource directory.
* `WEBKIT_DISABLE_DMABUF_RENDERER=1` is set by default (blank-window and crash workaround on NVIDIA with Wayland); set
  the variable yourself to override it.
* The AppImage never contains the NVIDIA driver library or the Vulkan loader (`LINUXDEPLOY_EXCLUDED_LIBRARIES` in
  `release-build.sh`): they must match the computer's own driver.
* Package sizes are dominated by the CUDA libraries (cuBLAS alone is about 870 MB unpacked on x86-64).
