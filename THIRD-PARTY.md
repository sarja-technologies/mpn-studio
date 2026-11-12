# Third-party components

MPN Studio is licensed under the Apache License 2.0 (`LICENSE`). The components below keep their own licences. This
file lists what is **shipped** inside the `.deb` and `.AppImage` and what is **needed to build** them. Licences are the
ones the packages declare in their own metadata, read from the build environment on 13 November 2026; check each
project for the binding text. The Rust crates are in [`docs/THIRD-PARTY-RUST.md`](docs/THIRD-PARTY-RUST.md).

## Desktop shell

| Component | Licence | Notes |
|---|---|---|
| Tauri 2 and its plugins (dialog, window-state, single-instance) | MIT OR Apache-2.0 | Compiled into the `mpn-studio` executable, with 300-odd Rust crates |
| WebKitGTK 4.1 and GTK 3 | LGPL-2.1 and others | **Not bundled.** The `.deb` depends on `libwebkit2gtk-4.1-0` and `libgtk-3-0`, which your system provides |

## Web interface (compiled into the shell's assets)

| Component | Version | Licence |
|---|---|---|
| svelte (runtime) | 5.57.3 | MIT |
| vite (module preload helper) | 8.3.7 | MIT |

Build and test tools that are not shipped: `@sveltejs/vite-plugin-svelte` (MIT), `typescript` (Apache-2.0),
`@playwright/test` (Apache-2.0), `svelte-check` (MIT), `@tauri-apps/cli` (MIT OR Apache-2.0).

### Fonts

Unmodified from the Fylume-Yaob brand kit, in `web/public/brand/fonts/` with their licence files.

| Font | Licence |
|---|---|
| Space Grotesk, copyright 2020 The Space Grotesk Project Authors | SIL Open Font License 1.1 |
| Manrope, copyright 2018 The Manrope Project Authors | SIL Open Font License 1.1 |
| IBM Plex Mono, copyright 2017 IBM Corp., Reserved Font Name "Plex" | SIL Open Font License 1.1 |

## Local backend (frozen with PyInstaller)

The desktop app starts a bundled Python backend. It contains CPython 3.14 (PSF-2.0) and these packages:

| Package | Version | Licence |
|---|---|---|
| fastapi | 0.162.5 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| httpx / httpcore / h11 | 0.28.1 / 1.0.9 / 0.16.0 | BSD-3-Clause / BSD-3-Clause / MIT |
| anyio | 4.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| certifi | 2026.10.23 | MPL-2.0 |
| click | 8.5.1 | BSD-3-Clause |
| pydantic / pydantic-core | 2.14.3 / 2.50.4 | MIT |
| annotated-types, annotated-doc, typing-inspection | 0.8.0, 0.0.6, 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| orjson | 3.13.0 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| psutil | 7.2.2 | BSD-3-Clause |
| httptools, uvloop, python-dotenv | 0.9.0, 0.23.0, 1.2.4 | MIT, MIT, BSD-3-Clause |
| websockets | 17.4 | BSD-3-Clause |
| watchfiles | 1.3.0 | MIT |
| pyyaml | 6.0.3 | MIT |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| setuptools | 84.2.1 | MIT |

MPL-2.0 packages (certifi, orjson) are included unmodified; their source is at <https://github.com/certifi/python-certifi>
and <https://github.com/ijl/orjson>.

PyInstaller's bootloader (GPL-2.0-or-later with the PyInstaller exception, which permits use in any program) is part of
the frozen `mpn-backend` executable. PyInstaller itself is a build tool and is not shipped.

Native libraries that PyInstaller copies from the build machine (Ubuntu 24.04) into the bundle, each under its own
licence: OpenSSL 3, SQLite, libffi, zlib, bzip2, expat, xz-utils (liblzma), zstd, util-linux (libuuid), ncurses (libtinfo),
the GCC runtime libraries (libgcc, libstdc++, libgfortran, under the GCC Runtime Library Exception; libquadmath, LGPL-2.1+)
and OpenBLAS (BSD-3-Clause, in `numpy.libs`). GNU Readline is excluded from the build.

## Needed to run the optional local runtime (not shipped)

You install these yourself into the Python environment you choose in Settings, Compute: `torch`, `transformers`,
`accelerate`, `safetensors`, `peft`, `huggingface_hub`, `nvidia-ml-py`, under their own licences (mostly Apache-2.0 and
BSD). Model weights such as Qwen3 are separate works under their publishers' licences.
