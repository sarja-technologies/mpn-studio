"""Where the platform-specific runtime files are: llama.cpp builds and the Hugging Face converter.

    <runtime>/llama/cpu/llama-server       always
    <runtime>/llama/cuda/llama-server      NVIDIA (x86-64: CUDA 12; ARM64: the DGX Spark build)
    <runtime>/llama/vulkan/llama-server    other GPUs (x86-64)
    <runtime>/convert/convert_hf_to_gguf.py + gguf-py/   optional

Looked up in this order: $MPN_RUNTIME_DIR; next to the frozen backend (installed app); <repo>/runtime (source checkout,
filled by scripts/fetch-runtime.sh). Model files (.mpn) are the same on every platform; these binaries are not, and come
with the installer for the platform.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def runtime_dir() -> Path:
    env = os.environ.get("MPN_RUNTIME_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        for c in (Path(sys.executable).parent / "runtime", Path(sys.executable).parent.parent / "runtime"):
            if c.exists():
                return c
    return Path(__file__).resolve().parents[2] / "runtime"


def llama_dir() -> Path:
    return runtime_dir() / "llama"


def available() -> dict:
    d = llama_dir()
    variants = [v for v in ("cuda", "vulkan", "cpu") if (d / v / "llama-server").exists()]
    return {"dir": str(d), "variants": variants, "machine": platform.machine(), "ok": "cpu" in variants or bool(variants),
            "reason": None if variants else f"No llama.cpp runtime found under {d}. Reinstall MPN Studio, or set MPN_RUNTIME_DIR."}
