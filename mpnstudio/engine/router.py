"""The trained N10K router, evaluated with numpy (no torch at inference time).

Reads the checkpoint written by `mpnstudio.model.bank.package_router`, unchanged:
  shards/shared.safetensors   enc_to_key, query_to_key (Linear 1024 -> 256), mpn_proto [M, 256], logit_scale, gate_scale, gate_center
  shards/mpn_<id>.safetensors per MPN: node_ids, keys (final normalised node keys, f16), gate_bias, member_* for shared nodes
  nodes.json                  node id -> {row, hash, key_trained, adapter_trained}
  node_emb.f16.npy            frozen-encoder embeddings of node text (the retrieval baseline and the overlay use these)

The arithmetic is the same as `mpnstudio.model.router.Router`:
  query key  = normalize(query_to_key(e_q))
  MPN prob   = sigmoid(min(exp(logit_scale), 100) * qk . normalize(mpn_proto))
  node score = key . qk;  gate = sigmoid(gate_scale * (score - gate_center) + gate_bias)
Only the shards of MPNs the router selects are read from disk for a request (LRU cache with a byte budget).
"""
from __future__ import annotations

import json
import struct
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

_DT = {"F16": np.float16, "F32": np.float32, "BF16": None, "I64": np.int64, "I32": np.int32, "F64": np.float64, "U8": np.uint8, "BOOL": np.bool_}


def load_safetensors(path: Path) -> tuple[dict[str, np.ndarray], dict]:
    """Minimal safetensors reader (format: u64 header length, JSON header, raw little-endian tensors)."""
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
        data = f.read()
    meta = {k: json.loads(v) for k, v in (header.pop("__metadata__", None) or {}).items()}
    out = {}
    for name, info in header.items():
        a, b = info["data_offsets"]
        dt = info["dtype"]
        buf = data[a:b]
        if dt == "BF16":
            u = np.frombuffer(buf, dtype=np.uint16).astype(np.uint32) << 16
            arr = u.view(np.float32)
        else:
            arr = np.frombuffer(buf, dtype=_DT[dt])
        out[name] = arr.reshape(info["shape"]).copy()
    return out, meta


def normalize(x: np.ndarray, axis: int = -1) -> np.ndarray:
    n = np.linalg.norm(x, axis=axis, keepdims=True)
    return x / np.maximum(n, 1e-12)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


class SharedRouter:
    def __init__(self, ck_dir: Path):
        t, meta = load_safetensors(Path(ck_dir) / "shards" / "shared.safetensors")
        self.spec = meta["spec"]
        self.mpn_index = {int(k): v for k, v in meta["mpn_index"].items()}
        f = lambda k: t[k].astype(np.float32)
        self.q_w, self.q_b = f("query_to_key.weight"), f("query_to_key.bias")
        self.e_w, self.e_b = f("enc_to_key.weight"), f("enc_to_key.bias")
        self.proto = normalize(f("mpn_proto"))
        self.logit_scale = float(min(np.exp(float(f("logit_scale").reshape(-1)[0])), 100.0))
        self.gate_scale = float(f("gate_scale").reshape(-1)[0])
        self.gate_center = float(f("gate_center").reshape(-1)[0])
        self.bytes = sum(a.nbytes for a in t.values())
        self.params = sum(a.size for a in t.values())

    def query_key(self, e_q: np.ndarray) -> np.ndarray:
        return normalize(e_q.astype(np.float32) @ self.q_w.T + self.q_b)

    def node_key_from_text_emb(self, e_n: np.ndarray) -> np.ndarray:
        """Key of a node with no trained residual (new or edited nodes): normalize(enc_to_key(e))."""
        return normalize(e_n.astype(np.float32) @ self.e_w.T + self.e_b)

    def mpn_probs(self, qk: np.ndarray) -> dict[int, float]:
        p = sigmoid(self.logit_scale * (self.proto @ qk))
        return {mid: float(p[i]) for mid, i in self.mpn_index.items()}

    def gate(self, sim: np.ndarray, bias) -> np.ndarray:
        return sigmoid(self.gate_scale * (sim - self.gate_center) + bias)


class Shard:
    def __init__(self, mpn_id: int, path: Path):
        t0 = time.perf_counter()
        t, meta = load_safetensors(path)
        self.mpn_id = mpn_id
        self.node_ids = t["node_ids"].astype(np.int64)
        self.keys = t["keys"].astype(np.float32)
        self.gate_bias = t["gate_bias"].astype(np.float32)
        self.member_ids = t["member_ids"].astype(np.int64)
        self.member_keys = t["member_keys"].astype(np.float32).reshape(-1, self.keys.shape[1] if self.keys.size else 256)
        self.member_gate_bias = t["member_gate_bias"].astype(np.float32)
        self.member_home = t["member_home"].astype(np.int64)
        self.hashes = meta.get("hashes", [])
        self.member_hashes = meta.get("member_hashes", [])
        self.bytes = path.stat().st_size
        self.load_ms = (time.perf_counter() - t0) * 1000


class ShardCache:
    """LRU residency of per-MPN routing shards under a byte budget. A shard is read only when its MPN is selected."""

    def __init__(self, ck_dir: Path, limit_bytes: int = 64 << 20):
        self.dir = Path(ck_dir) / "shards"
        self.limit = int(limit_bytes)
        self.items: "OrderedDict[int, Shard]" = OrderedDict()
        self.lock = threading.Lock()
        self.stats = {"loads": 0, "hits": 0, "evictions": 0}

    def get(self, mpn_id: int) -> tuple[Shard | None, dict]:
        with self.lock:
            s = self.items.get(mpn_id)
            if s is not None:
                self.items.move_to_end(mpn_id)
                self.stats["hits"] += 1
                return s, {"mpn_id": str(mpn_id), "state": "already resident", "bytes": s.bytes, "ms": 0.0}
            p = self.dir / f"mpn_{mpn_id}.safetensors"
            if not p.exists():
                return None, {"mpn_id": str(mpn_id), "state": "no trained state", "bytes": 0, "ms": 0.0}
            s = Shard(mpn_id, p)
            while self.items and sum(x.bytes for x in self.items.values()) + s.bytes > self.limit:
                self.items.popitem(last=False)
                self.stats["evictions"] += 1
            self.items[mpn_id] = s
            self.stats["loads"] += 1
            return s, {"mpn_id": str(mpn_id), "state": "loaded now", "bytes": s.bytes, "ms": round(s.load_ms, 2)}

    def bytes(self) -> int:
        return sum(s.bytes for s in self.items.values())

    def evict(self) -> int:
        with self.lock:
            n = len(self.items)
            self.items.clear()
            self.stats["evictions"] += n
            return n

    def residency(self) -> list[dict]:
        return [{"mpn_id": str(m), "bytes": s.bytes} for m, s in self.items.items()]


class Checkpoint:
    def __init__(self, ck_dir: Path, shard_cache_bytes: int = 64 << 20):
        self.dir = Path(ck_dir)
        self.manifest = json.loads((self.dir / "manifest.json").read_text())
        self.router = SharedRouter(self.dir)
        self.shards = ShardCache(self.dir, shard_cache_bytes)
        self.nodes = {int(k): v for k, v in json.loads((self.dir / "nodes.json").read_text()).items()}
        self._emb = None

    @property
    def node_emb(self) -> np.ndarray:
        """Memory-mapped frozen-encoder node embeddings (row-indexed); only touched by retrieval baselines and search."""
        if self._emb is None:
            self._emb = np.load(self.dir / "node_emb.f16.npy", mmap_mode="r")
        return self._emb

    @property
    def id(self) -> str:
        return self.manifest.get("checkpoint_id") or self.manifest.get("id") or self.dir.name
