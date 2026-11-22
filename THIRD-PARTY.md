# Third-party components

MPN Studio is licensed under the Apache License 2.0 (`LICENSE`). The components below keep their own licences. This
file lists what is **shipped** inside the `.deb` and `.AppImage` and what is **needed to build** them. Licences are the
ones the packages declare in their own metadata, read from the build environment on 13 November 2026; check each
project for the binding text. The Rust crates are in [`docs/THIRD-PARTY-RUST.md`](docs/THIRD-PARTY-RUST.md).

## Desktop shell

| Component | Licence | Notes |
|---|---|---|
| Tauri 2 and its plugins (dialog, window-state, single-instance) | MIT OR Apache-2.0 | Compiled into the `mpn-studio` executable, with 300-odd Rust crates |
| WebKitGTK 4.1 and GTK 3 | LGPL-2.1 and others | `.deb`: **not bundled**, it depends on `libwebkit2gtk-4.1-0` and `libgtk-3-0` from your system. `.AppImage`: bundled, see below |

### What the AppImage bundles

The AppImage carries the shared libraries the shell needs, copied unmodified from the build machine's Ubuntu packages
(Ubuntu 26.04 for x86-64, Ubuntu 24.04 for ARM64) by linuxdeploy: WebKitGTK 4.1, JavaScriptCore, GTK 3, GDK-Pixbuf,
GLib/GIO, Pango, Cairo, HarfBuzz, FreeType, Fontconfig, ICU, libsoup 3, GnuTLS, GStreamer core and base libraries,
ATK/AT-SPI, libepoxy, libxml2/libxslt and their dependencies. Most are LGPL-2.1-or-later (WebKitGTK, GTK, GLib,
GStreamer, libsoup, GnuTLS); the others are under MIT, BSD, ICU and similar licences. Their source is in the Ubuntu
archive under the package names above. They are ordinary shared libraries in the AppImage's `usr/lib` and can be
replaced: run the AppImage with `--appimage-extract`, swap the library, and start `squashfs-root/AppRun`.

The NVIDIA driver (`libcuda`), the Vulkan loader and GPU drivers are never bundled: the app uses the ones installed on
your computer.

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

## Model runtime (llama.cpp)

`runtime/llama/<variant>/llama-server` and its libraries run the cognitive cores. They come from llama.cpp release
b12318 (commit 7021f7e09, <https://github.com/ggml-org/llama.cpp>), checked against SHA-256 values in
`scripts/fetch-runtime.sh`:

| Variant | Where it comes from |
|---|---|
| x86-64 `cpu`, `vulkan`, `cuda` | the official release archives, unmodified |
| ARM64 `cpu` | the official release archive, unmodified |
| ARM64 `cuda` | built from the official b12318 source archive by `scripts/build-llama-cuda.sh` (no source changes) |

llama.cpp and ggml are under the MIT licence. llama-server also compiles in nlohmann/json (MIT), cpp-httplib (MIT),
stb_image (MIT or public domain), miniaudio (public domain or MIT-0), subprocess.h (Unlicense) and hash functions
(sha256: public domain; xxHash: BSD-2-Clause; rotate-bits: MIT). The full texts are in `runtime/LICENSES-llama.cpp.txt`
inside the installed app.

`runtime/convert/` is llama.cpp's Hugging Face to GGUF converter (`convert_hf_to_gguf.py`, `conversion/`, `gguf-py/`),
Python source under the MIT licence. It runs only in a Python environment you choose (it needs `torch`, `transformers`,
`numpy`, `sentencepiece` or `tokenizers`, which are not shipped).

### NVIDIA CUDA runtime libraries

The `cuda` variant includes `libcudart`, `libcublas` and `libcublasLt` (CUDA 12.8 on x86-64, CUDA 13.5 on ARM64),
unmodified, from the official llama.cpp `cudart-llama-b12318-bin-ubuntu-cuda-*.tar.gz` archives. They are NVIDIA
software, redistributed under the NVIDIA CUDA Toolkit End User License Agreement, Attachment A (redistributable
components): <https://docs.nvidia.com/cuda/eula/index.html>. They are not covered by the Apache License. Each `cuda`
folder carries `NVIDIA-NOTICE.txt`. The NVIDIA driver itself is not included.

## FG-MPN runtime (bundled in the local backend)

The backend carries the **FG-MPN runtime 0.2.2**, the `fgmpn` package, so that FG-MPN networks open without any
other software. It is made by Fylume and published at <https://github.com/sarja-technologies/fgmpn> under the
**Fylume Source-Available Terms 1.0** (the text of the Apache License 2.0 plus the Fylume No-Resale Condition 1.0). That
licence is not the Apache License and is not an open-source licence as the OSI defines it; it allows use, modification
and redistribution but not resale, and requires its NOTICE to be kept. MPN Studio's own code stays under Apache-2.0.

| Machine | Wheel bundled (unmodified, SHA-256 checked by `scripts/fetch-fgmpn.sh`) |
|---|---|
| x86-64 | `fgmpn-0.2.2-cp312-abi3-manylinux_2_17_x86_64.whl` (Python package and native reader), SHA-256 `2bd8b6dc821b0eebfc915cc4999ac2adf3b822ccf61a736c882b7af0b2863284` |
| ARM64 | `fgmpn-0.2.2-cp312-abi3-manylinux_2_34_aarch64.whl` (Python package and native reader), SHA-256 `5823b11281bb33813babe423c7ad44558832c712c9d2fb37092272e1958b7bbd` |

The runtime's `LICENSE`, `NOTICE`, `THIRD-PARTY.md` and the notices of what its native reader contains (28 Rust crates under
MIT, Apache-2.0, BSD-3-Clause, Unlicense or Zlib terms; the Rust standard library, MIT OR Apache-2.0; on x86-64 also zlib and the zig compiler
runtime, MIT with LLVM-derived parts under Apache-2.0 with the LLVM exception) are installed with the app in
`licenses/fgmpn/` next to the other notices (`.deb`: `/usr/lib/MPN Studio/licenses/fgmpn/`; AppImage: `usr/lib/MPN Studio/licenses/fgmpn/` inside the image). The
runtime has no required third-party Python dependency. `orjson`, which the runtime can use when present, is already part of the
backend (below).

The runtime has no network server: Studio's own reader (`mpnstudio/mpnnet/fgmpn_net.py`, Apache-2.0) serves a store on a
loopback port with it. The data of an FG-MPN network is not part of the app and has its own licence (the Yaob N10K store: CC BY-SA 4.0).

## Local backend (frozen with PyInstaller)

The desktop app starts a bundled Python backend. It contains CPython (PSF-2.0; 3.14 in the x86-64 build, 3.12 in the
ARM64 build) and these packages, the same versions on both:

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

Native libraries that PyInstaller copies from the build machine (Ubuntu 26.04 x86-64, Ubuntu 24.04 ARM64) into the bundle, each under its own
licence: OpenSSL 3, SQLite, libffi, zlib, bzip2, expat, xz-utils (liblzma), zstd, util-linux (libuuid), ncurses (libtinfo),
the GCC runtime libraries (libgcc, libstdc++, libgfortran, under the GCC Runtime Library Exception; libquadmath, LGPL-2.1+)
and OpenBLAS (BSD-3-Clause, in `numpy.libs`). GNU Readline is excluded from the build.

## Not shipped

- **Model weights.** Cognitive cores and encoders (GGUF files such as Qwen3.5-4B) are separate works under their
  publishers' licences. MPN Studio imports the ones you give it; a model's `.mpn` file records the core's licence and
  carries the notices you exported with it.
- **FG-MPN network data, and Studio's networks.** Downloaded by you from the Releases view; each carries its own licence.
- **Python packages for the optional PyTorch runtime and for converting Hugging Face models.** You install these
  yourself into the Python environment you choose in Settings, Compute: `torch`, `transformers`, `accelerate`,
  `safetensors`, `peft`, `huggingface_hub`, `nvidia-ml-py`, `sentencepiece`, under their own licences (mostly
  Apache-2.0 and BSD).
