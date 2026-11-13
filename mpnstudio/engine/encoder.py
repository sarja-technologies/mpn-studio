"""The frozen text encoder (Qwen3-Embedding-0.6B) served by llama.cpp.

It reproduces the encoder the N10K router was trained with (`mpnstudio.model.encoder.TextEncoder`): queries are wrapped
in the same instruction, inputs are cut to 256 tokens including the final end-of-text token, the last token's hidden
state is the embedding, and vectors are L2-normalised. Measured against the stored training embeddings with the F16
GGUF: node texts cosine 0.99982 mean (400 nodes), queries 0.99984 (docs/measurements/encoder-parity-001).
"""
from __future__ import annotations

import numpy as np

from .llama import LlamaServer

QUERY_INSTRUCTION = "Given a question, retrieve the knowledge node that answers it"


class Encoder:
    def __init__(self, srv: LlamaServer, max_len: int = 256, dim: int = 1024):
        self.srv, self.max_len, self.dim = srv, max_len, dim
        self.eos = srv.tokenize("<|endoftext|>", special=False)
        assert len(self.eos) == 1, f"unexpected end-of-text tokenisation {self.eos}"
        self.eos = self.eos[0]

    def _ids(self, text: str) -> list[int]:
        ids = self.srv.tokenize(text, special=False)
        if ids and ids[-1] == self.eos:
            ids = ids[:-1]
        return ids[: self.max_len - 1] + [self.eos]

    def encode(self, texts: list[str], query: bool = False, batch: int = 16) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        idx = [i for i, t in enumerate(texts) if t and t.strip()]
        for b in range(0, len(idx), batch):
            chunk = idx[b:b + batch]
            toks = [self._ids(f"Instruct: {QUERY_INSTRUCTION}\nQuery: {texts[i]}" if query else texts[i]) for i in chunk]
            r = self.srv.post("/v1/embeddings", {"input": toks}, timeout=300)
            vecs = [d["embedding"] for d in sorted(r["data"], key=lambda d: d["index"])]
            v = np.asarray(vecs, dtype=np.float32)
            v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            out[chunk] = v
        return out
