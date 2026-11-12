# Building and installing

## Install a built package

    sudo apt install ./mpn-studio_0.1.0_amd64.deb          # menu entry "MPN Studio", command: mpn-studio
    # or, without root:
    chmod +x MPN-Studio_0.1.0_x86_64.AppImage && ./MPN-Studio_0.1.0_x86_64.AppImage

Runtime needs only `libwebkit2gtk-4.1-0` and `libgtk-3-0` (the deb declares them). No Node.js, no Python.
Remove with `sudo apt remove mpn-studio`; your projects in `~/.local/share/mpn-studio` are not touched.

## Build from source (build machine only)

Needs Node >= 22 with pnpm, Rust >= 1.80, Python >= 3.12 (+ venv), and
`libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libssl-dev patchelf rsync build-essential`. Internet is needed to fetch
dependencies once; the product itself never needs it.

    scripts/release-build.sh        # tests -> web -> frozen backend -> .deb + .AppImage -> dist-desktop/ + SHA256SUMS

Individual steps: `cd web && pnpm build`, `scripts/build-backend.sh`, `pnpm tauri build --bundles deb,appimage`.
Development build without packaging: `pnpm tauri build --debug --no-bundle`, then run
`MPN_DEV_PYTHON=.venv-build/bin/python MPN_DEV_ROOT=$PWD src-tauri/target/debug/mpn-studio` (uses the source backend).

Tests: `.venv-build/bin/python -m pytest tests -q` (71 pass; 29 need torch and are skipped),
`scripts/offline-acceptance.sh` (needs the package installed, `bwrap`, `strace`, `Xvfb`, `xdotool`, ImageMagick).

## Notes

* No absolute developer paths are shipped; resources are found through the app's resource directory.
* `WEBKIT_DISABLE_DMABUF_RENDERER=1` is set by default (blank-window/crash workaround on NVIDIA+Wayland); export the
  variable yourself to override.
* x86_64 only for now. The frozen backend is ~100 MB mostly numpy/OpenBLAS; slimming it is possible but not done.
