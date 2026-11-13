"""Read the header of a GGUF file (llama.cpp's model format): metadata and tensor shapes, without loading weights.

https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
"""
from __future__ import annotations

import struct
from pathlib import Path

GGUF_MAGIC = b"GGUF"
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}

# llama_ftype (general.file_type) -> name
FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M",
              13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS",
              21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
              29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4_MOE"}


class GGUFError(ValueError):
    pass


class _R:
    def __init__(self, f):
        self.f = f

    def take(self, n: int) -> bytes:
        b = self.f.read(n)
        if len(b) != n:
            raise GGUFError("the file ends inside its header (truncated or not a GGUF file)")
        return b

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def string(self) -> str:
        n = self.u64()
        if n > 1 << 24:
            raise GGUFError("implausible string length in the GGUF header")
        return self.take(n).decode("utf-8", "replace")

    def value(self, t: int, keep_array: bool):
        if t in _SCALARS:
            fmt = _SCALARS[t]
            return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]
        if t == 8:
            return self.string()
        if t == 9:
            et, n = self.u32(), self.u64()
            if et in _SCALARS:
                size = struct.calcsize(_SCALARS[et])
                raw = self.take(size * n)
                if not keep_array or n > 4096:
                    return {"array": True, "type": et, "length": n}
                return list(struct.unpack("<" + _SCALARS[et][1] * n, raw))
            items = [self.value(et, keep_array) for _ in range(n)]
            return items if keep_array and n <= 4096 else {"array": True, "type": et, "length": n}
        raise GGUFError(f"unknown GGUF value type {t}")


def read_header(path: str | Path) -> dict:
    """{version, metadata (large arrays summarised), tensors: [(name, shape, type)], tensor_count, data_offset}"""
    p = Path(path)
    with p.open("rb") as f:
        r = _R(f)
        if r.take(4) != GGUF_MAGIC:
            raise GGUFError(f"{p.name} is not a GGUF file")
        version = r.u32()
        if version not in (2, 3):
            raise GGUFError(f"GGUF version {version} is not supported (2 and 3 are)")
        n_tensors, n_kv = r.u64(), r.u64()
        meta = {}
        for _ in range(n_kv):
            k = r.string()
            t = r.u32()
            meta[k] = r.value(t, keep_array=not k.startswith("tokenizer.ggml."))
        tensors = []
        for _ in range(n_tensors):
            name = r.string()
            nd = r.u32()
            shape = [r.u64() for _ in range(nd)]
            tt = r.u32()
            r.u64()     # offset
            tensors.append((name, shape, tt))
        align = int(meta.get("general.alignment", 32))
        pos = f.tell()
        data_offset = (pos + align - 1) // align * align
    return {"version": version, "metadata": meta, "tensors": tensors, "tensor_count": n_tensors, "data_offset": data_offset}


def describe(path: str | Path) -> dict:
    """What MPN Studio shows about a GGUF file."""
    p = Path(path)
    h = read_header(p)
    m = h["metadata"]
    arch = m.get("general.architecture", "unknown")
    params = 0
    for _, shape, _ in h["tensors"]:
        n = 1
        for d in shape:
            n *= d
        params += n
    g = lambda k: m.get(f"{arch}.{k}")
    ft = m.get("general.file_type")
    kind = "core"
    if arch == "clip" or m.get("clip.has_vision_encoder") or m.get("clip.projector_type"):
        kind = "projector"
    elif g("pooling_type") is not None or arch in ("bert", "nomic-bert", "jina-bert-v2", "modern-bert", "t5encoder"):
        kind = "encoder"
    ctx = g("context_length")
    return {
        "file": p.name, "bytes": p.stat().st_size, "gguf_version": h["version"],
        "architecture": arch, "kind": kind,
        "name": m.get("general.name") or p.stem,
        "basename": m.get("general.basename"), "size_label": m.get("general.size_label"),
        "license": m.get("general.license"), "license_link": m.get("general.license.link"),
        "organization": m.get("general.organization"), "repo_url": m.get("general.repo_url") or m.get("general.source.url"),
        "base_models": [{k2: m.get(f"general.base_model.{i}.{k2}") for k2 in ("name", "organization", "repo_url") if m.get(f"general.base_model.{i}.{k2}")}
                        for i in range(int(m.get("general.base_model.count", 0) or 0))],
        "parameters": params, "tensors": h["tensor_count"],
        "quantization": FILE_TYPES.get(ft, f"type {ft}") if ft is not None else None,
        "context_length": int(ctx) if ctx else None,
        "embedding_length": g("embedding_length"), "layers": g("block_count"), "heads": g("attention.head_count"),
        "vocab_size": (m.get("tokenizer.ggml.tokens") or {}).get("length") if isinstance(m.get("tokenizer.ggml.tokens"), dict) else None,
        "chat_template": bool(m.get("tokenizer.chat_template")),
        "pooling": g("pooling_type"),
    }
