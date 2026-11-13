"""The Yaob request path: cognitive core -> MPN routing -> persistent node retrieval -> evidence and procedures -> core -> checked answer.

One shared cognitive core (a llama.cpp server) is used for every module; there is no model per node. The 10,000-node
network stays on disk in SQLite; for each request only the routing shards of the MPNs the learned router selects are
read, and only the selected nodes' text is read and given to the core as evidence.

Modes (also evaluation arms):
  core        the unmodified core; no node is read
  retrieval   ordinary retrieval baseline: frozen-encoder cosine + BM25 over all enabled nodes, given as text
  yaob        learned routing (MPN prototypes, then per-node keys and gates within the selected MPNs), typed referrals,
              procedure nodes offered as callable tools, node text as evidence, and MPN search tools the core may call
              to reformulate or follow relations (bounded rounds), then citation filtering and deterministic checks

What is learned, retrieved, computed and pretrained per request:
  learned     the router (query projection, MPN prototypes, per-node keys and gate biases), trained on N10K; unchanged
  retrieval   node text placed in the prompt; text-search candidates; graph referrals
  computed    procedure results, validation checks
  pretrained  the core's generation and the frozen encoder's embeddings
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mpnstudio.runtime import budget as tokbudget
from mpnstudio.runtime import conversation as conv
from mpnstudio.runtime import procedures
from mpnstudio.store.db import Store, now, sid

from .encoder import Encoder
from .llama import LlamaError, LlamaServer, choose_variant, gpu_snapshot
from .prompts import MPN_TOOLS, PROMPTS, QUESTION_PREFIX, apply_identity, render_node
from . import toolmarkup
from .router import Checkpoint, normalize

conv_stop = {"what", "when", "who", "whom", "which", "where", "why", "how", "is", "was", "the", "a", "an", "in", "on", "of", "and", "or", "i", "please",
             "tell", "give", "name", "list", "explain", "describe", "compare", "according", "is", "are", "did", "does", "do", "can", "could", "would"}

DEFAULT_BUDGET = {
    "context_tokens": 32768,       # per request; the core server is started with this context
    "output_tokens": 8192,         # answer cap per generation call, thinking included
    "max_active_mpns": 3,
    "retrieval_candidates": 48,
    "selected_nodes": 8,
    "text_nodes": 4,
    "traversal_depth": 1,
    "referral_mode": "relation",
    "fact_focus": True,
    "title_priority": True,        # MPNs of nodes whose title the question names are activated before the router's other choices
    "entity_rescue": True,         # a name in the question that no selected node mentions is looked up by text in all enabled MPNs
    "prompt": "next",              # instructions: next (default), v01 (v0.1.0 verbatim), long
    "tool_rounds": 2,              # MPN search / follow / procedure calls the core may make before answering
    "concurrent_requests": 2,
    "think_default": False,        # Qwen3.5-4B scores higher without reasoning (docs/next/core-selection.md)
}
SAMPLING = {"think": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0},
            "nothink": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0},
            "greedy": {"temperature": 0.0, "top_k": 1}}
IMAGE_TOKENS = 1024                # reserved per image (the projector is started with --image-max-tokens 1024)


# Wikidata property labels (and close paraphrases) for the relation types of the network whose names differ from them
RELATION_ALIASES = {
    "place_of_death": "died_in", "death_place": "died_in", "died": "died_in", "place_of_birth": "born_in", "birth_place": "born_in",
    "birthplace": "born_in", "born": "born_in", "country_of_citizenship": "citizen_of", "citizenship": "citizen_of", "nationality": "citizen_of",
    "located_in_the_administrative_territorial_entity": "located_in", "headquarters_location": "headquartered_in", "headquarters": "headquartered_in",
    "shares_border_with": "borders", "border": "borders", "located_in_or_next_to_body_of_water": "next_to_water", "religion_or_worldview": "religion",
    "developer": "developed_by", "discoverer_or_inventor": "discovered_by", "inventor": "discovered_by", "discoverer": "discovered_by",
    "creator": "created_by", "founder": "founded_by", "basic_form_of_government": "form_of_government", "government": "form_of_government",
    "located_on_terrain_feature": "on_terrain", "medical_condition_treated": "treats", "drug_or_therapy_used_for_treatment": "treated_by",
    "has_parts": "has_part", "has_part(s)": "has_part", "language": "official_language", "director_/_manager": "director", "manager": "director",
    "capital_city": "capital", "named_for": "named_after", "namesake": "named_after", "author_of": "notable_work", "occupation_field": "field_of_work",
}

NO_MORE_TOOLS = ("No further searches are available for this answer. Answer now from the knowledge nodes and tool results already "
                 "supplied, or say that you do not have the information.")
EMPTY_ANSWER = "I could not produce an answer to this from the information I have."


class Cancelled(Exception):
    pass


@dataclass
class Request:
    id: str
    mode: str
    rev: int
    think: bool
    budget: dict
    cancel: threading.Event = field(default_factory=threading.Event)
    t0: float = field(default_factory=time.perf_counter)


def node_text(title: str, statements: list[str]) -> str:
    return title + ". " + " ".join(statements[:4])


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _images_of(content) -> list[dict]:
    if isinstance(content, list):
        return [p for p in content if isinstance(p, dict) and p.get("type") in ("image_url", "input_image")]
    return []


class CoreAdapter:
    """Tokenizer and chat template of the loaded core, for the shared budget packer."""

    def __init__(self, srv: LlamaServer, think: bool):
        self.srv, self.think = srv, think

    def chat_text(self, msgs: list[dict]) -> str:
        return self.srv.apply_template(msgs, chat_template_kwargs={"enable_thinking": self.think})

    def ids(self, text: str) -> list[int]:
        return self.srv.tokenize(text, special=True) if text else []


class Engine:
    def __init__(self, root: Path, data: Path, device: str = "auto", context: int | None = None, threads: int | None = None):
        self.root, self.data = Path(root), Path(data)
        man = json.loads((self.root / "MANIFEST.json").read_text()) if (self.root / "MANIFEST.json").exists() else {}
        self.manifest = man
        # a model built by MPN Studio names itself; the Yaob release manifest has no identity and keeps its prompts verbatim
        self.identity: str | None = man.get("identity")
        self.model_id: str = man.get("model_id") or "yaob-c4b-n10k"
        self.model_name: str = man.get("name") or self.model_id
        self.device_pref = device
        self.threads = threads
        self.budget = dict(DEFAULT_BUDGET)
        saved = self.data / "budget.json"
        if saved.exists():
            self.budget.update(json.loads(saved.read_text()))
        if context:
            self.budget["context_tokens"] = int(context)
        self.core: LlamaServer | None = None
        self.enc_srv: LlamaServer | None = None
        self.enc: Encoder | None = None
        self.store: Store | None = None
        self.ck: Checkpoint | None = None
        self.variant = None
        self.devices: list[dict] = []
        self.core_info: dict = {}
        self.loading = False
        self.lock = threading.Lock()
        self.sem = threading.BoundedSemaphore(int(self.budget["concurrent_requests"]))
        self.requests: dict[str, Request] = {}
        self.counters = {"requests": 0, "cancelled": 0, "refused": 0, "errors": 0}
        self.last_error: str | None = None
        self._flat = None
        self.load_info: dict = {}
        self.loaded_ctx: int | None = None

    # ------------------------------------------------------------------ paths
    def p(self, key: str) -> Path:
        m = self.manifest.get("paths", {})
        defaults = {"core": "models/core/core.gguf", "mmproj": "models/core/mmproj.gguf", "encoder": "models/encoder/encoder.gguf",
                    "network": "network/yaob-n10k.sqlite", "checkpoint": "network/checkpoint", "runtime": "runtime/llama"}
        if key == "runtime":
            # llama.cpp belongs to the installed app, not to the model: a path recorded when a model was built or a .mpn
            # file was opened goes stale when the app moves (every AppImage launch has a new mount point; upgrades
            # replace the install). Use the running app's runtime; fall back to the recorded or packaged one.
            from .runtime import llama_dir
            live = llama_dir()
            if any((live / v / "llama-server").exists() for v in ("cuda", "vulkan", "cpu")):
                return live
        return self.root / m.get(key, defaults[key])

    def working_store(self) -> Path:
        """The live network. The packaged store is copied to the data directory once, so edits never modify the release files."""
        if self.manifest.get("network_writable"):      # a model materialised from a .mpn file: already a private working copy
            return self.p("network")
        dst = self.data / "network" / "yaob-n10k.sqlite"
        if not dst.exists():
            import sqlite3
            dst.parent.mkdir(parents=True, exist_ok=True)
            # immutable: the release file never changes, so SQLite needs no -shm/-wal beside it (read-only install folders)
            src = sqlite3.connect(f"file:{self.p('network')}?mode=ro&immutable=1", uri=True)
            tmp = dst.with_suffix(".tmp")
            d = sqlite3.connect(tmp)
            src.backup(d)
            d.close()
            src.close()
            tmp.replace(dst)
        return dst

    # ------------------------------------------------------------------ loading
    def load(self, log=print) -> dict:
        with self.lock:
            if self.core and self.core.alive():
                return self.load_info
            self.loading = True
            try:
                return self._load(log)
            finally:
                self.loading = False

    def _load(self, log) -> dict:
        t0 = time.perf_counter()
        self.store = Store(self.working_store())
        ckp = self.p("checkpoint")
        # a network without a trained router (any network built in MPN Studio) is routed by retrieval instead
        self.ck = Checkpoint(ckp) if (ckp / "shards" / "shared.safetensors").exists() else None
        has_enc = self.p("encoder").exists()
        variant, bindir, devs = choose_variant(self.p("runtime"), self.device_pref)
        self.variant, self.devices = variant, devs
        logs = self.data / "logs"
        thr = ["-t", str(self.threads)] if self.threads else []
        ctx = int(self.budget["context_tokens"])
        n_par = int(self.budget["concurrent_requests"])
        self.loaded_ctx = ctx          # per request; the core holds ctx * n_par tokens of KV cache shared by its slots
        why = " (CPU inference, as asked)" if self.device_pref == "cpu" else " (no usable GPU found; CPU inference)"
        log(f"Device: {variant}" + (f" ({devs[0]['name']}, {devs[0]['free_mib']} MiB free)" if devs else why))
        def enc_args(gpu: bool) -> list[str]:
            return ["-m", str(self.p("encoder")), "--embedding", "--pooling", "last", "-c", "2048", "-b", "2048", "-ub", "2048", "-np", "2",
                    "-ngl", "99" if gpu else "0", *thr]

        def core_args(gpu: bool) -> list[str]:
            a = ["-m", str(self.p("core")), "-c", str(ctx * n_par), "-np", str(n_par), "--kv-unified", "--jinja", "-fa", "auto",
                 "--reasoning-format", "deepseek", "--cache-reuse", "256", *thr]
            if self.p("mmproj").exists():
                a += ["--mmproj", str(self.p("mmproj")), "--image-max-tokens", str(IMAGE_TOKENS)]
            # GPU: the context is fixed; --fit chooses how many layers fit in free device memory (the rest run on the CPU)
            return a + (["--fit", "on"] if gpu else ["--fit", "off", "-ngl", "0", "--no-mmproj-offload"])

        order = [(variant, bindir)] + ([("cpu", self.p("runtime") / "cpu")] if variant != "cpu" else [])
        last, retried = None, set()
        while order:
            v, b = order.pop(0)
            gpu = v != "cpu"
            pending = []
            try:
                enc_srv = None
                if has_enc:
                    log("Loading the encoder ...")
                    enc_srv = LlamaServer("encoder", b, enc_args(gpu), logs / "encoder.log", v)
                    pending = [enc_srv]
                    enc_srv.start()
                log("Loading the cognitive core ...")
                core = LlamaServer("core", b, core_args(gpu), logs / "core.log", v)
                pending.append(core)
                core.start()
                # published only once both are ready, so /health never reports a core that is still loading
                self.enc_srv, self.enc, self.core = enc_srv, (Encoder(enc_srv) if enc_srv else None), core
                self.variant = v
                break
            except RuntimeError as e:
                last = e
                log(f"{v} start failed: {e}")
                for srv in pending:
                    srv.stop()
                self.core = self.enc_srv = self.enc = None
                if "exited during start (code -" in str(e) and v not in retried:
                    # killed by a signal while loading (one SIGSEGV in llama.cpp's vocabulary loader was seen in ~20 CPU starts): retry once
                    retried.add(v)
                    order.insert(0, (v, b))
                    log(f"Retrying {v} once.")
                elif v != "cpu":
                    log("Falling back to CPU inference.")
        if self.core is None:
            raise RuntimeError(f"the core could not be started: {last}")
        props = self.core.props()
        self.core_info = {"name": (self.manifest.get("core") or {}).get("name") or Path(self.p("core")).stem, "repo": (self.manifest.get("core") or {}).get("repo"),
                          "revision": (self.manifest.get("core") or {}).get("revision"), "quantization": (self.manifest.get("core") or {}).get("quantization"),
                          "n_ctx": (props.get("default_generation_settings") or {}).get("n_ctx"), "modalities": props.get("modalities"),
                          "build": props.get("build_info")}
        self.load_info = {"load_s": round(time.perf_counter() - t0, 1), "core_load_s": self.core.load_s, "encoder_load_s": self.enc_srv.load_s if self.enc_srv else None,
                          "variant": self.variant, "devices": devs, "loaded_at": now()}
        return self.load_info

    def unload(self) -> dict:
        with self.lock:
            for s in (self.core, self.enc_srv):
                if s:
                    s.stop()
            self.core = self.enc_srv = self.enc = None
            if self.store is not None:
                self.store.close()
                self.store = None
            self._flat = None
        return {"unloaded": True}

    def set_budget(self, b: dict) -> dict:
        allowed = set(DEFAULT_BUDGET)
        bad = [k for k in b if k not in allowed]
        if bad:
            raise ValueError(f"unknown budget keys: {', '.join(bad)}")
        for k, v in b.items():
            d = DEFAULT_BUDGET[k]
            if isinstance(d, bool):
                ok = isinstance(v, bool)
            elif isinstance(d, int):
                ok = isinstance(v, int) and not isinstance(v, bool) and v >= 0
            else:
                ok = isinstance(v, str)
            if not ok:
                raise ValueError(f"budget key {k} must be {'true or false' if isinstance(d, bool) else type(d).__name__ + ' (not negative)' if isinstance(d, int) else 'a string'}")
        if "prompt" in b and b["prompt"] not in PROMPTS:
            raise ValueError(f"prompt must be one of: {', '.join(PROMPTS)}")
        if "referral_mode" in b and b["referral_mode"] not in ("relation", "plain"):
            raise ValueError("referral_mode must be relation or plain")
        nb = dict(self.budget)
        nb.update(b)
        why = tokbudget.check_budget_tokens(nb["context_tokens"], nb["output_tokens"])
        if why:
            raise ValueError(why)
        self.budget = nb
        (self.data / "budget.json").write_text(json.dumps({k: v for k, v in nb.items() if DEFAULT_BUDGET.get(k) != v}, indent=1))
        return self.budget

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        st = {"available": True, "loading": self.loading, "core": {"loaded": bool(self.core and self.core.alive())}, "encoder": {"loaded": bool(self.enc)},
              "device": {"variant": self.variant, "devices": self.devices, "gpu": gpu_snapshot()}, "budget": self.budget,
              "requests": {"active": len(self.requests), **self.counters}, "last_error": self.last_error, "load": self.load_info}
        if self.core and self.core.alive():
            st["core"].update(self.core_info)
            try:
                import resource
                st["memory"] = {"backend_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
            except Exception:
                pass
        if self.enc:
            st["encoder"].update({"name": (self.manifest.get("encoder") or {}).get("name") or "Qwen3-Embedding-0.6B", "dim": self.enc.dim, "trained_here": False})
        st["routing"] = "learned router" if self.ck else ("retrieval: encoder and text search" if self.enc else "retrieval: text search only")
        st["model"] = {"id": self.model_id, "name": self.model_name}
        if self.ck:
            m = self.ck.manifest
            rp = ((m.get("learned_params") or {}).get("router") or {})
            st["checkpoint"] = {"id": self.ck.id, "nodes": m.get("nodes"), "trained_nodes": m.get("key_trained_nodes"), "router_parameters": rp.get("total"),
                                "per_node_parameters": rp.get("per_node_total"), "specialist_adapters": "not used (trained for the previous core)"}
            st["residency"] = {"shards": self.ck.shards.residency(), "shard_bytes": self.ck.shards.bytes(), "stats": self.ck.shards.stats,
                               "shared_router_bytes": self.ck.router.bytes}
        if self.store:
            st["network"] = self.store.counts()
            st["network"]["path"] = str(self.store.path) if hasattr(self.store, "path") else None
        return st

    # ------------------------------------------------------------------ indexing for new and edited nodes
    def reindex(self, limit: int = 4000) -> dict:
        """Embed live nodes whose text changed since the router was trained (or that are new) into the overlay."""
        if self.enc is None:
            return {"indexed": 0, "reason": "no encoder: retrieval uses text search only"}
        rows = self.store.q("SELECT n.node_id, n.title, n.cells, n.content_hash FROM node_v n LEFT JOIN derived_state d ON d.node_id=n.node_id "
                            "WHERE n.rev_to IS NULL AND n.status<>'tombstone' AND (d.embed_hash IS NULL OR d.embed_hash<>n.content_hash) LIMIT ?", (limit,))
        if self.ck is not None:      # the router's trained keys cover nodes whose content is unchanged since training
            covered = [r for r in rows if (self.ck.nodes.get(r["node_id"]) or {}).get("hash") == r["content_hash"]]
            if covered:
                c = self.store.conn()
                with self.store._wlock:
                    c.execute("BEGIN IMMEDIATE")
                    c.executemany("INSERT INTO derived_state(node_id, embed_hash) VALUES(?,?) ON CONFLICT(node_id) DO UPDATE SET embed_hash=excluded.embed_hash",
                                  [(r["node_id"], r["content_hash"]) for r in covered])
                    c.execute("COMMIT")
            rows = [r for r in rows if (self.ck.nodes.get(r["node_id"]) or {}).get("hash") != r["content_hash"]]
            if not rows and covered:
                return {"indexed": 0, "covered_by_router": len(covered), "more": True}
        if not rows:
            return {"indexed": 0}
        t0 = time.perf_counter()
        texts = [node_text(r["title"], [s.get("text", "") for s in json.loads(r["cells"]).get("knowledge", {}).get("statements", [])]) for r in rows]
        emb = self.enc.encode(texts)
        c = self.store.conn()
        with self.store._wlock:
            c.execute("BEGIN IMMEDIATE")
            try:
                for i, r in enumerate(rows):
                    c.execute("INSERT OR REPLACE INTO overlay(node_id, content_hash, slots, dim, node_emb, stmt_emb, updated) VALUES(?,?,?,?,?,?,?)",
                              (r["node_id"], r["content_hash"], 0, emb.shape[1], emb[i].astype(np.float16).tobytes(), b"", now()))
                    c.execute("INSERT INTO derived_state(node_id, embed_hash) VALUES(?,?) ON CONFLICT(node_id) DO UPDATE SET embed_hash=excluded.embed_hash",
                              (r["node_id"], r["content_hash"]))
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        self._flat = None
        return {"indexed": len(rows), "ms": round((time.perf_counter() - t0) * 1000, 1)}

    # ------------------------------------------------------------------ retrieval
    def _enabled(self, rev: int) -> dict[int, dict]:
        return {int(m["id"]): m for m in self.store.mpns(rev) if m["installed"] and m["enabled"]}

    def route(self, rev: int, question: str, b: dict, exclude: set | None = None) -> dict:
        """Learned hierarchical routing. Reads only the shards of the MPNs it selects."""
        if self.ck is None:
            return self._route_retrieval(rev, question, b, exclude)
        t0 = time.perf_counter()
        ev: dict = {"mpns": [], "skipped": [], "shards": [], "reductions": []}
        R = self.ck.router
        qe = self.enc.encode([question], query=True)[0]
        qk = R.query_key(qe)
        all_mpns = {int(m["id"]): m for m in self.store.mpns(rev)}
        enabled = {k: m for k, m in all_mpns.items() if m["installed"] and m["enabled"]}
        probs = R.mpn_probs(qk)
        scored = sorted(((p, mid) for mid, p in probs.items() if mid in all_mpns), reverse=True)
        lex = self.store.search_lexical(question, limit=12, any_terms=True)
        lex_ids = [int(r["id"]) for r in lex]
        title_hit: dict[int, str] = {}
        ql = question.lower()
        for r in lex[:6]:
            if len(r["title"]) >= 4 and r["title"].lower() in ql:
                for m in self.store.memberships(int(r["id"]), rev):
                    title_hit.setdefault(int(m["mpn_id"]), r["title"])
        active: list[int] = []
        max_active = int(b["max_active_mpns"])
        best_enabled = max((p for p, i in scored if i in enabled), default=0)
        # MPNs holding a node whose title the question names take a slot first, then the router's order
        for p, mid in sorted(scored, key=lambda t: (t[1] not in title_hit, -t[0])) if b.get("title_priority", True) else scored:
            m = all_mpns[mid]
            wanted = p >= 0.5 or (mid in enabled and p == best_enabled) or mid in title_hit
            if not wanted:
                continue
            if mid not in enabled:
                ev["skipped"].append({"id": sid(mid), "name": m["name"], "score": round(p, 3), "reason": "disabled" if m["installed"] else "not installed"})
                continue
            if len(active) >= max_active:
                ev["skipped"].append({"id": sid(mid), "name": m["name"], "score": round(p, 3), "reason": "over the active-MPN budget"})
                continue
            active.append(mid)
        for p, mid in scored:
            ev["mpns"].append({"id": sid(mid), "name": all_mpns[mid]["name"], "score": round(p, 3), "active": mid in active,
                               **({"reason": f"title match: {title_hit[mid]}"} if mid in title_hit and p < 0.5 else {})})
        ev["mpns"] = [m for m in ev["mpns"] if m["active"]] + [m for m in ev["mpns"] if not m["active"]][:4]
        cands: dict[int, dict] = {}
        k_c = int(b["retrieval_candidates"])
        for mid in active:
            shard, e = self.ck.shards.get(mid)
            e["name"] = all_mpns[mid]["name"]
            ev["shards"].append(e)
            if shard is None:
                continue
            if len(shard.node_ids):
                sim = shard.keys @ qk
                top = np.argsort(-sim)[:k_c]
                gate = R.gate(sim[top], shard.gate_bias[top])
                for i, s, g in zip(top, sim[top], gate):
                    nid = int(shard.node_ids[i])
                    if nid not in cands or s > cands[nid]["score"]:
                        cands[nid] = {"id": nid, "score": float(s), "gate": float(g), "via_mpn": mid, "home": mid, "hash": shard.hashes[i], "via": "router"}
            if len(shard.member_ids):
                sim = shard.member_keys @ qk
                top = np.argsort(-sim)[:max(4, k_c // 4)]
                gate = R.gate(sim[top], shard.member_gate_bias[top])
                for i, s, g in zip(top, sim[top], gate):
                    nid = int(shard.member_ids[i])
                    if nid not in cands or s > cands[nid]["score"]:
                        cands[nid] = {"id": nid, "score": float(s), "gate": float(g), "via_mpn": mid, "home": int(shard.member_home[i]),
                                      "hash": shard.member_hashes[i], "via": "router"}
        if active:      # overlay: new and edited nodes in the active MPNs (text-derived keys, no trained residual)
            ph = ",".join("?" * len(active))
            wm, am = self.store.at(rev, "m")
            orows = self.store.q(f"SELECT DISTINCT o.node_id, o.content_hash, o.dim, o.node_emb, m.mpn_id FROM overlay o JOIN member_v m ON m.node_id=o.node_id AND {wm} "
                                 f"WHERE m.mpn_id IN ({ph}) LIMIT 4000", (*am, *active))
            if orows:
                oe = np.stack([np.frombuffer(r["node_emb"], dtype=np.float16).astype(np.float32) for r in orows])
                ok = R.node_key_from_text_emb(oe)
                sim = ok @ qk
                gate = R.gate(sim, 0.0)
                for r, s, g in zip(orows, sim, gate):
                    cands[int(r["node_id"])] = {"id": int(r["node_id"]), "score": float(s), "gate": float(g), "via_mpn": int(r["mpn_id"]), "home": None,
                                                "hash": r["content_hash"], "via": "router (new or edited node)", "overlay": True}
        if lex_ids and active:      # text matches inside the active MPNs (ordinary retrieval, labelled as such)
            ph, pa = ",".join("?" * len(lex_ids)), ",".join("?" * len(active))
            wm, am = self.store.at(rev, "m")
            inside = {r[0]: r[1] for r in self.store.q(f"SELECT m.node_id, m.mpn_id FROM member_v m WHERE {wm} AND m.node_id IN ({ph}) AND m.mpn_id IN ({pa})",
                                                       (*am, *lex_ids, *active))}
            for rank, nid in enumerate(lex_ids):
                if nid in inside and nid not in cands:
                    cands[nid] = {"id": nid, "score": 0.0, "gate": max(0.2, 0.75 - 0.08 * rank), "via_mpn": inside[nid], "home": None, "hash": None,
                                  "via": "text match", "lex_rank": rank}
                elif nid in cands:
                    cands[nid]["lex_rank"] = rank
        if exclude:
            cands = {k: v for k, v in cands.items() if k not in exclude}
        ranked = sorted(cands.values(), key=lambda c: -(c["gate"] + (0.25 / (1 + c["lex_rank"]) if "lex_rank" in c else 0.0)))
        ev["route_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        ev["candidates"] = len(ranked)
        return {"evidence": ev, "ranked": ranked, "active": active, "question": question}

    def _route_retrieval(self, rev: int, question: str, b: dict, exclude: set | None = None) -> dict:
        """Routing for a network without a trained router: frozen-encoder cosine and text search over the enabled MPNs,
        fused by reciprocal rank. The MPNs of the best candidates become active, so the rest of the request path
        (selection, typed referrals, procedures, MPN tools, citations) is the same as with the learned router."""
        t0 = time.perf_counter()
        ev: dict = {"mpns": [], "skipped": [], "shards": [], "reductions": [], "routing": "retrieval"}
        enabled = self._enabled(rev)
        score: dict[int, float] = {}
        cos: dict[int, float] = {}
        flat = self._flat_index()
        k_c = int(b["retrieval_candidates"])
        if flat["ids"] and self.enc is not None:
            qv = self.enc.encode([question], query=True)[0]
            sims = flat["mat"] @ qv
            for rank, i in enumerate(np.argsort(-sims)[:k_c]):
                nid = flat["ids"][i]
                cos[nid] = float(sims[i])
                score[nid] = score.get(nid, 0.0) + 1 / (30 + rank)
        lex = self.store.search_lexical(question, limit=24, any_terms=True)
        lex_rank = {}
        for rank, r in enumerate(lex):
            nid = int(r["id"])
            lex_rank[nid] = rank
            score[nid] = score.get(nid, 0.0) + 1 / (30 + rank)
        if exclude:
            score = {k: v for k, v in score.items() if k not in exclude}
        ids = sorted(score, key=lambda i: -score[i])[: k_c]
        mem: dict[int, list[int]] = {}
        if ids:
            ph = ",".join("?" * len(ids))
            wm, am = self.store.at(rev, "m")
            for r in self.store.q(f"SELECT m.node_id, m.mpn_id FROM member_v m WHERE {wm} AND m.node_id IN ({ph}) ORDER BY m.role<>'primary'", (*am, *ids)):
                mem.setdefault(int(r[0]), []).append(int(r[1]))
        ql = question.lower()
        ranked, votes = [], {}
        for rank, nid in enumerate(ids):
            via = next((m for m in mem.get(nid, []) if m in enabled), None)
            if via is None:
                continue
            c = cos.get(nid)
            # gate: rank-based confidence, raised by a strong cosine or a text hit; a weak, text-only tail stays low
            g = max(0.2, 0.9 - 0.05 * len(ranked))
            if c is not None and c < 0.35 and nid not in lex_rank:
                g = min(g, 0.3)
            cand = {"id": nid, "score": float(c if c is not None else 0.0), "gate": g, "via_mpn": via, "home": via, "hash": None,
                    "via": "retrieval" if c is not None else "text match"}
            if nid in lex_rank:
                cand["lex_rank"] = lex_rank[nid]
            ranked.append(cand)
            votes[via] = votes.get(via, 0.0) + score[nid]
        active = sorted(votes, key=lambda m: -votes[m])[: int(b["max_active_mpns"])]
        tot = sum(votes.values()) or 1.0
        for mid, m in enabled.items():
            if mid in votes:
                ev["mpns"].append({"id": sid(mid), "name": m["name"], "score": round(votes[mid] / tot, 3), "active": mid in active})
        ev["mpns"].sort(key=lambda m: (not m["active"], -m["score"]))
        ev["route_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        ev["candidates"] = len(ranked)
        return {"evidence": ev, "ranked": ranked, "active": active, "question": question}

    def select(self, rev: int, routed: dict, b: dict, want: int | None = None) -> list[dict]:
        """Resolve candidates at the pinned revision; follow typed links of the best nodes (relation referrals)."""
        want = int(want or b["selected_nodes"])
        ids = [c["id"] for c in routed["ranked"][: want * 4]]
        recs = {int(n["id"]): n for n in self.store.get_nodes(ids, rev, full=True)}
        chosen: list[dict] = []
        for c in routed["ranked"]:
            if len(chosen) >= want:
                break
            n = recs.get(c["id"])
            if n is None or n["status"] != "live":
                continue
            if c["gate"] < 0.35 and len(chosen) >= 1:
                break
            c["node"] = n
            c["stale"] = c["hash"] is not None and c["hash"] != n["content_hash"]
            chosen.append(c)
        depth = int(b["traversal_depth"])
        by_relation = b.get("referral_mode", "relation") == "relation"
        named = conv.relations_named(routed.get("question", "")) if by_relation else set()
        enabled = None
        if depth > 0 and chosen:
            seen = {c["id"] for c in chosen}
            frontier = chosen[:2]
            for _ in range(depth):
                nxt = []
                for c in frontier:
                    edges = self.store.edges_of(c["id"], rev, limit=200 if by_relation else 12)
                    if by_relation:
                        edges.sort(key=lambda e: e["type"] not in named)
                    for e in edges:
                        if e["type"] == "mentions" or e["direction"] != "out":
                            continue
                        oid = int(e["other"]["id"])
                        asked = e["type"] in named
                        if oid in seen or (len(chosen) >= want + 2 and not (asked and len(chosen) < want + 4)):
                            continue
                        mems = [int(m["mpn_id"]) for m in self.store.memberships(oid, rev)]
                        via = next((m for m in mems if m in routed["active"]), None)
                        if via is None and asked:
                            enabled = enabled if enabled is not None else set(self._enabled(rev))
                            via = next((m for m in mems if m in enabled), None)
                        if via is None:
                            continue
                        n = self.store.get_node(oid, rev)
                        if n is None or n["status"] != "live":
                            continue
                        seen.add(oid)
                        r = {"id": oid, "score": 0.0, "gate": 0.5, "via_mpn": via, "home": None, "hash": None, "via": "referral", "node": n,
                             "stale": False, "relation": e["type"], "from": c["node"]["title"], "asked": asked}
                        chosen.append(r)
                        nxt.append(r)
                frontier = nxt
        return chosen

    NAME = re.compile(r"(?<![\w'])([A-Z\u00C0-\u024F][\w\u00C0-\u024F'\-]{2,}(?:\s+(?:of|de|la|von|van|the|da|del|di)?\s*[A-Z\u00C0-\u024F][\w\u00C0-\u024F'\-]+)*)")

    def entity_rescue(self, rev: int, question: str, have: list[dict], cap: int = 2) -> list[dict]:
        """Names in the question (capitalised words after the first word) that no selected node mentions are looked up
        by text across all enabled MPNs. A hit is kept only if its title, aliases or text contains the name, so a name that
        is not in the network adds nothing. This is ordinary text retrieval and is labelled as such."""
        words = question.split()
        body = question[len(words[0]):] if words else ""
        names = []
        for m in self.NAME.finditer(body):
            n = m.group(1).strip()
            if n.lower() in conv_stop or n in names:
                continue
            names.append(n)
        if not names:
            return []
        seen = {c["id"] for c in have}
        text_have = " ".join((c["node"]["title"] + " " + " ".join(c["node"].get("aliases") or []) + " " +
                              json.dumps(c["node"]["cells"].get("knowledge", {}), ensure_ascii=False)) for c in have).lower()
        enabled = None
        out = []
        for name in names[:4]:
            if name.lower() in text_have:
                continue
            for r in self.store.search_lexical(name, limit=4):
                nid = int(r["id"])
                if nid in seen:
                    continue
                n = self.store.get_node(nid, rev)
                if n is None or n["status"] != "live" or n["type"] == "procedure":
                    continue
                blob = (n["title"] + " " + " ".join(n.get("aliases") or []) + " " + json.dumps(n["cells"].get("knowledge", {}), ensure_ascii=False)).lower()
                if not re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", blob):
                    continue
                enabled = enabled if enabled is not None else set(self._enabled(rev))
                via = next((int(m["mpn_id"]) for m in n["memberships"] if int(m["mpn_id"]) in enabled), None)
                if via is None:
                    continue
                seen.add(nid)
                out.append({"id": nid, "score": 0.0, "gate": 0.5, "via_mpn": via, "home": None, "hash": None, "via": f"text match on the name '{name}'",
                            "node": n, "stale": False})
                break
            if len(out) >= cap:
                break
        return out

    def _flat_index(self) -> dict:
        if self._flat is None and self.ck is None:
            over = {r["node_id"]: r for r in self.store.q("SELECT o.node_id, o.dim, o.node_emb FROM overlay o JOIN node_v n ON n.node_id=o.node_id "
                                                         "AND n.rev_to IS NULL AND n.status<>'tombstone' AND n.content_hash=o.content_hash")}
            if over:
                mat = normalize(np.stack([np.frombuffer(r["node_emb"], dtype=np.float16).astype(np.float32) for r in over.values()]))
            else:
                mat = np.zeros((0, 1024), np.float32)
            self._flat = {"ids": list(over), "mat": mat}
        if self._flat is None:
            emb = self.ck.node_emb
            cur = {r[0]: r[1] for r in self.store.q("SELECT node_id, content_hash FROM node_v WHERE rev_to IS NULL AND status<>'tombstone'")}
            over = {r["node_id"]: r for r in self.store.q("SELECT node_id, dim, node_emb FROM overlay")}
            ids, rows = [], []
            for nid, info in self.ck.nodes.items():
                if nid in cur and cur[nid] == info["hash"] and nid not in over:
                    ids.append(nid)
                    rows.append(info["row"])
            mats = [np.asarray(emb[rows], dtype=np.float32)] if rows else []
            if over:
                mats.append(np.stack([np.frombuffer(r["node_emb"], dtype=np.float16).astype(np.float32) for r in over.values()]))
                ids += list(over)
            mat = normalize(np.concatenate(mats)) if mats else np.zeros((0, 1024), np.float32)
            self._flat = {"ids": ids, "mat": mat}
        return self._flat

    def baseline_retrieve(self, rev: int, question: str, k: int) -> list[dict]:
        """Ordinary retrieval over all enabled nodes: frozen-encoder cosine and BM25, fused by reciprocal rank."""
        enabled = set(self._enabled(rev))
        score: dict[int, float] = {}
        flat = self._flat_index()
        if flat["ids"] and self.enc is not None:
            qv = self.enc.encode([question], query=True)[0]
            sims = flat["mat"] @ qv
            for rank, i in enumerate(np.argsort(-sims)[:40]):
                score[flat["ids"][i]] = score.get(flat["ids"][i], 0) + 1 / (30 + rank)
        for rank, r in enumerate(self.store.search_lexical(question, limit=40, any_terms=True)):
            score[int(r["id"])] = score.get(int(r["id"]), 0) + 1 / (30 + rank)
        out = []
        for nid in sorted(score, key=lambda i: -score[i]):
            n = self.store.get_node(nid, rev)
            if n is None or n["status"] != "live" or n["type"] == "procedure":
                continue
            ms = [int(m["mpn_id"]) for m in n["memberships"]]
            via = next((m for m in ms if m in enabled), None)
            if via is None:
                continue
            out.append({"id": nid, "score": score[nid], "gate": 1.0, "via_mpn": via, "home": None, "via": "retrieval", "node": n})
            if len(out) >= k:
                break
        return out

    def semantic(self, q: str, limit: int = 20) -> list[dict]:
        flat = self._flat_index()
        if not flat["ids"] or self.enc is None:
            return []
        qv = self.enc.encode([q], query=True)[0]
        sims = flat["mat"] @ qv
        top = np.argsort(-sims)[:limit]
        nodes = {int(n["id"]): n for n in self.store.get_nodes([flat["ids"][i] for i in top])}
        return [{"id": sid(flat["ids"][i]), "title": nodes[flat["ids"][i]]["title"], "type": nodes[flat["ids"][i]]["type"],
                 "summary": nodes[flat["ids"][i]]["summary"][:240], "score": round(float(sims[i]), 4)} for i in top if flat["ids"][i] in nodes]

    # ------------------------------------------------------------------ generation helpers
    def _sampling(self, req: Request) -> dict:
        s = dict(SAMPLING["think" if req.think else "nothink"])
        s.update(req.budget.get("sampling") or {})
        return s

    def _gen_body(self, req: Request, messages: list[dict], max_tokens: int, tools: list | None = None) -> dict:
        body = {"messages": messages, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": req.think}, "cache_prompt": True,
                "stream_options": {"include_usage": True}, "timings_per_token": False, **self._sampling(req)}
        if req.budget.get("seed") is not None:
            body["seed"] = int(req.budget["seed"])
        if tools:
            body["tools"] = tools
        return body

    def _stream_round(self, req: Request, body: dict):
        """Stream one generation round. Yields ('content'|'reasoning', text) and finally ('end', {tool_calls, finish, usage, timings})."""
        calls: dict[int, dict] = {}
        finish, usage, timings = None, None, None
        dbg = open(self.data / "logs" / "debug.jsonl", "a") if os.environ.get("YAOB_DEBUG") else None
        if dbg:
            dbg.write(json.dumps({"request": req.id, "body": {k: v for k, v in body.items() if k != "messages"}, "messages": body["messages"]}, ensure_ascii=False) + "\n")
        for ch in self.core.stream("/v1/chat/completions", body, cancel=req.cancel):
            if dbg:
                dbg.write(json.dumps({"request": req.id, "chunk": ch}, ensure_ascii=False) + "\n")
            if req.cancel.is_set():
                raise Cancelled()
            if ch.get("error"):          # llama-server reports a failure inside the stream as an error object
                e = ch["error"]
                raise LlamaError(int(e.get("code", 500)) if isinstance(e, dict) else 500, json.dumps(e)[:2000])
            if ch.get("usage"):
                usage = ch["usage"]
            if ch.get("timings"):
                timings = ch["timings"]
            for c in ch.get("choices", []):
                d = c.get("delta") or {}
                if d.get("reasoning_content"):
                    yield "reasoning", d["reasoning_content"]
                if d.get("content"):
                    yield "content", d["content"]
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                                                                 "function": {"name": "", "arguments": ""}})
                    f = tc.get("function") or {}
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    slot["function"]["name"] += f.get("name") or ""
                    slot["function"]["arguments"] += f.get("arguments") or ""
                if c.get("finish_reason"):
                    finish = c["finish_reason"]
        if req.cancel.is_set():
            raise Cancelled()
        yield "end", {"tool_calls": [calls[i] for i in sorted(calls)], "finish": finish, "usage": usage, "timings": timings}

    def _quick(self, req: Request, messages: list[dict], max_tokens: int = 160) -> str:
        """A short non-thinking deterministic call (used to extract procedure arguments when tool calling is off)."""
        body = {"messages": messages, "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False}, **SAMPLING["greedy"]}
        r = self.core.post("/v1/chat/completions", body, timeout=600)
        return (r["choices"][0]["message"].get("content") or "").strip()

    # ------------------------------------------------------------------ procedures as tools
    @staticmethod
    def procedure_tool(n: dict) -> dict | None:
        impl = n["cells"].get("thinking", {}).get("impl")
        p = procedures.REGISTRY.get(impl or "")
        if p is None:
            return None
        ty = {"number": {"type": "number"}, "integer": {"type": "integer"}, "string": {"type": "string"}, "boolean": {"type": "boolean"},
              "list[number]": {"type": "array", "items": {"type": "number"}}}
        props = {k: dict(ty.get(v.rstrip("?"), {"type": "string"})) for k, v in p.params.items()}
        req_ = [k for k, v in p.params.items() if not v.endswith("?")]
        desc = f"{p.title}: {p.summary}" + (f" Formula: {p.formula}." if p.formula else "") + (f" Constraints: {'; '.join(p.constraints)}." if p.constraints else "")
        return {"type": "function", "function": {"name": "proc_" + impl, "description": desc[:900],
                                                 "parameters": {"type": "object", "properties": props, "required": req_}}}

    # ------------------------------------------------------------------ the request
    def run(self, messages: list[dict], mode: str = "yaob", rev: int | None = None, think: bool | None = None, budget: dict | None = None,
            client_tools: list | None = None, request_id: str | None = None):
        """Generator of (event, data) for one request: start, route, reasoning, token, tool, validation, done | error."""
        if not (self.core and self.core.alive()):
            yield "error", {"error": "The cognitive core is not loaded.", "status": 503}
            return
        b = dict(self.budget)
        b.update({k: v for k, v in (budget or {}).items() if k in DEFAULT_BUDGET or k in ("sampling", "seed", "requested_output_tokens")})
        think = bool(b.get("think_default", False)) if think is None else bool(think)
        rev = self.store.head() if rev is None else int(rev)
        req = Request(request_id or uuid.uuid4().hex[:12], mode, rev, think, b)
        if not self.sem.acquire(timeout=300):
            self.counters["refused"] += 1
            yield "error", {"error": "The backend stayed busy for five minutes; try again.", "status": 503}
            return
        self.requests[req.id] = req
        self.counters["requests"] += 1
        try:
            yield from self._run(req, messages, client_tools or [])
        except Cancelled:
            self.counters["cancelled"] += 1
            yield "done", {"text": "", "cancelled": True, "usage": {}, "timing": {"total_ms": round((time.perf_counter() - req.t0) * 1000, 1)}}
        except LlamaError as e:
            self.counters["errors"] += 1
            self.last_error = str(e)[:300]
            status = 413 if "exceed" in e.text and "context" in e.text else 500
            yield "error", {"error": f"The core refused the request: {e.text[:400]}", "status": status}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.counters["errors"] += 1
            self.last_error = f"{type(e).__name__}: {e}"[:300]
            yield "error", {"error": f"The request failed: {type(e).__name__}: {e}"[:400], "status": 500}
        finally:
            self.requests.pop(req.id, None)
            self.sem.release()

    def cancel(self, rid: str) -> bool:
        r = self.requests.get(rid)
        if r:
            r.cancel.set()
        return bool(r)

    def _run(self, req: Request, messages: list[dict], client_tools: list):
        b = req.budget
        ui = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=None)
        if ui is None:
            yield "error", {"error": "The request has no user message.", "status": 400}
            return
        last_user = messages[ui]
        question = _text_of(last_user.get("content"))
        images = _images_of(last_user.get("content"))
        # a client tool round-trip: the assistant's tool calls and the client's tool results after the last user turn
        tail = [m for m in messages[ui + 1:] if m.get("role") in ("assistant", "tool")]
        history = [{"role": m["role"], "content": _text_of(m.get("content"))} for m in messages[:ui] if m.get("role") in ("user", "assistant")][-8:]
        history = [h for h in history if h["content"].strip()]
        client_system = "\n\n".join(_text_of(m.get("content")) for m in messages if m.get("role") == "system" and _text_of(m.get("content")).strip())
        try:
            limits = tokbudget.resolve(b, self.loaded_ctx or int(b["context_tokens"]), b.get("requested_output_tokens"))
        except ValueError as e:
            yield "error", {"error": str(e), "status": 400}
            return
        yield "start", {"request_id": req.id, "rev": req.rev, "mode": req.mode, "think": req.think, "core": self.core_info.get("name"),
                        "checkpoint": self.ck.id if (req.mode == "yaob" and self.ck) else None}
        ev: dict = {"mpns": [], "skipped": [], "shards": [], "nodes": [], "procedures": [], "searches": [], "reductions": list(limits["notes"])}
        rq, used_history = conv.routing_query(question, [{"role": m["role"], "content": _text_of(m.get("content"))} for m in messages])
        small_talk = conv.is_small_talk(question) or (not question.strip() and bool(images))
        ev["routing_query"] = {"used_history": used_history, "small_talk": small_talk}
        chosen: list[dict] = []
        t_r = time.perf_counter()
        if req.mode == "yaob" and not small_talk:
            routed = self.route(req.rev, rq, b)
            ev.update(routed["evidence"])
            chosen = self.select(req.rev, routed, b)
            active = routed["active"]
        elif req.mode == "retrieval" and not small_talk:
            chosen = self.baseline_retrieve(req.rev, rq, int(b["text_nodes"]) + 2)
            active = []
        else:
            active = []
        ev["ms"] = round((time.perf_counter() - t_r) * 1000, 2)
        text_nodes = [c for c in chosen if c["node"]["type"] != "procedure"][: int(b["text_nodes"]) if req.mode == "yaob" else max(int(b["text_nodes"]), 1)]
        if req.mode == "yaob" and b.get("referral_mode", "relation") == "relation":
            srcs = {c["node"]["title"] for c in text_nodes}
            text_nodes += [c for c in chosen if c.get("asked") and c.get("from") in srcs and c not in text_nodes][:2]
        if req.mode == "yaob" and not small_talk and b.get("entity_rescue", True):
            for c in self.entity_rescue(req.rev, question, text_nodes + chosen):
                chosen.append(c)
                text_nodes.append(c)
        procs = [c for c in chosen if c["node"]["type"] == "procedure" and (c["gate"] >= 0.5 or c["via"] == "text match")][:2] if req.mode == "yaob" else []
        focus = conv.properties_named(question) if b.get("fact_focus", True) else None

        evidence: list[dict] = list(text_nodes)        # numbered as supplied: [1], [2], ... (tool results continue the numbering)

        def render(tn):
            return "\n\n".join(render_node(c["node"], i, focus=focus) for i, c in enumerate(tn, start=1))

        tools: list[dict] = []
        n_rounds = int(b["tool_rounds"]) if req.mode == "yaob" and not small_talk else 0
        if n_rounds > 0:
            tools += MPN_TOOLS
        proc_tools = {}
        for c in procs:
            t = self.procedure_tool(c["node"])
            if t:
                tools.append(t)
                proc_tools[t["function"]["name"]] = c
        tools += client_tools
        ps = PROMPTS.get(b.get("prompt", "next"), PROMPTS["next"])
        system = apply_identity(ps[("yaob" if n_rounds > 0 else "yaob_no_tools") if req.mode == "yaob" else req.mode], self.identity)
        if proc_tools:
            system += "\n- A procedure tool computes its result exactly; when the question asks for such a computation, call it rather than computing by hand."
        if client_system:
            system += "\n\nInstructions from the application:\n" + client_system
        core = CoreAdapter(self.core, req.think)
        lim = dict(limits)
        if images:
            lim["margin"] = limits["margin"] + IMAGE_TOKENS * len(images)
        packed = tokbudget.pack(core, system, history, text_nodes, "", question, lim, render)
        ev["reductions"] += packed["reductions"]
        if packed.get("error"):
            yield "error", packed["error"]
            return
        text_nodes, history = packed["text_nodes"], packed["history"]
        evidence = list(text_nodes)
        body_text = ("Knowledge nodes:\n" + render(text_nodes) + "\n\n" if text_nodes else "") + QUESTION_PREFIX + question
        user_content = body_text if not images else images + [{"type": "text", "text": body_text}]
        convo = [{"role": "system", "content": system}] + history + [{"role": "user", "content": user_content}]
        for m in tail:     # client tool calls and their results, passed through unchanged
            convo.append({k: m[k] for k in ("role", "content", "tool_calls", "tool_call_id", "name") if k in m and m[k] is not None}
                         | ({"content": _text_of(m.get("content"))} if m.get("role") == "tool" else {}))
        names = {int(m["id"]): m["name"] for m in self.store.mpns(req.rev)}
        ev["nodes"] = [self._node_ev(c, names, text_nodes) for c in chosen]
        ev["budget"] = {"context": limits["context"], "output": packed["output"], "input_tokens": packed["n_prompt"], "breakdown": packed["breakdown"],
                        "tool_rounds": n_rounds, "think": req.think}
        yield "route", ev

        # ---- generation rounds: the core may call MPN tools / procedures, then answers.
        # The same tool list is offered in every round up to `limit`, so the rendered prompt keeps its prefix (prompt cache).
        # Calls after the limit are answered with NO_MORE_TOOLS, and the closing round offers only the client's tools: with
        # MPN tools still on offer, cores keep calling them instead of answering. Markup that a core still writes as text
        # in that round is held back by the guard.
        out_tokens = packed["output"]
        cf = conv.CitationFilter([])
        parts, reasoning = [], []
        usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
        ttft, t_gen = None, time.perf_counter()
        final_calls, finish = [], None
        round_i = 0
        limit = max(n_rounds, 1 if proc_tools else 0)
        offered = tools
        known = {t["function"]["name"] for t in offered}
        closing = False
        markup_calls = 0
        sep = False
        while True:
            cf.node_texts = [c["node"]["title"] + " " + render_node(c["node"], i, focus=focus) for i, c in enumerate(evidence, start=1)]
            body = self._gen_body(req, convo, out_tokens, (client_tools if closing else offered) or None)
            calls, content_round, reason_round = [], [], []
            guard = toolmarkup.MarkupGuard()

            def emit(piece_in):
                nonlocal ttft, sep
                piece = cf.feed(piece_in)
                if piece and sep:       # text resumes after a tool round: keep it apart from what came before
                    if parts and not parts[-1][-1:].isspace() and not piece[0].isspace():
                        piece = "\n\n" + piece
                    sep = False
                if piece:
                    if ttft is None:
                        ttft = time.perf_counter() - t_gen
                    parts.append(piece)
                    return piece
                return ""

            for kind, data in self._stream_round(req, body):
                if kind == "reasoning":
                    reason_round.append(data)
                    yield "reasoning", {"text": data}
                elif kind == "content":
                    content_round.append(data)
                    out = emit(guard.feed(data))
                    if out:
                        yield "token", {"text": out}
                else:
                    calls, finish = data["tool_calls"], data["finish"]
                    u = data.get("usage") or {}
                    tm = data.get("timings") or {}
                    if not u and tm:
                        u = {"prompt_tokens": tm.get("prompt_n", 0) + tm.get("cache_n", 0), "completion_tokens": tm.get("predicted_n", 0)}
                    usage_total["prompt_tokens"] += u.get("prompt_tokens", 0)
                    usage_total["completion_tokens"] += u.get("completion_tokens", 0)
            reasoning += reason_round
            tail_text, held = guard.finish()
            if held and not calls:      # calls the server's parser did not recognise, written as text
                calls = [c for c in toolmarkup.parse_calls(held) if c["function"]["name"] in known or closing]
                markup_calls += len(calls)
                if not calls:           # not a tool call after all: it is part of the answer
                    tail_text += held
            out = emit(tail_text)
            if out:
                yield "token", {"text": out}
            content_text = "".join(content_round)
            if held:
                content_text = content_text.split(held, 1)[0] if held in content_text else content_text
            bad_args = {c["id"] for c in calls if not toolmarkup.normalize_arguments(c)}
            internal = [c for c in calls if c["function"]["name"] in ("mpn_search", "mpn_follow") or c["function"]["name"] in proc_tools]
            external = [c for c in calls if c not in internal and c["function"]["name"] in {t["function"]["name"] for t in client_tools}]
            if external:          # a client-defined tool: return the call to the client (OpenAI semantics)
                final_calls = external
                break
            if not calls or closing:
                break
            convo.append({"role": "assistant", "content": content_text, "tool_calls": calls,
                          **({"reasoning_content": "".join(reason_round)} if reason_round else {})})
            if round_i >= limit or not internal:     # over the limit, or calls of tools that do not exist
                closing = True
                for c in calls:
                    convo.append({"role": "tool", "tool_call_id": c["id"], "content": NO_MORE_TOOLS})
                ev["reductions"].append(f"tool call after the limit of {limit} round(s) answered with: no further searches")
                continue
            round_i += 1
            fruitless = True
            for c in calls:
                if c in internal and c["id"] not in bad_args:
                    result = self._tool(req, c, evidence, ev, proc_tools, b, focus)
                elif c in internal:
                    result = {"content": "The arguments of this call were not valid JSON, so it was not run.", "summary": "invalid arguments"}
                else:
                    result = {"content": f"There is no tool named {c['function']['name']}.", "summary": "unknown tool"}
                fruitless = fruitless and bool(result.get("fruitless"))
                yield "tool", {"name": c["function"]["name"], "arguments": c["function"]["arguments"], "result_summary": result["summary"]}
                convo.append({"role": "tool", "tool_call_id": c["id"], "content": result["content"]})
            if fruitless:      # every search found only what was already supplied: the next round answers, with no MPN tools on offer
                closing = True
            sep = True
        rest = cf.finish()
        if rest:
            parts.append(rest)
            yield "token", {"text": rest}
        text = "".join(parts).strip()
        empty = not text and not final_calls
        if empty:       # the core gave no answer text (for example it kept calling tools): say so instead of returning nothing
            text = EMPTY_ANSWER
            yield "token", {"text": text}
        total_gen = time.perf_counter() - t_gen
        validation = self.validate(text, chosen, evidence, ev["procedures"], question)
        if empty:
            validation["checks"].append({"kind": "empty_answer", "ok": False,
                                         "detail": "The core produced no answer text; Yaob replied that it could not answer."})
        if markup_calls:
            validation["checks"].append({"kind": "tool_markup", "ok": True, "calls": markup_calls,
                                         "detail": f"{markup_calls} tool call(s) written as text were read by Yaob and kept out of the answer."})
        if cf.removed:
            validation["checks"].append({"kind": "citations_removed", "ok": True, "removed": cf.removed,
                                         "detail": f"{len(cf.removed)} citation(s) removed: no supplied node text supported them."})
        if round_i:      # tools ran: send the evidence again with what they added (procedures, searches, nodes read)
            yield "route", ev
        yield "validation", validation
        n_out = usage_total["completion_tokens"]
        yield "done", {"text": text, "reasoning": "".join(reasoning), "cancelled": False, "tool_calls": final_calls, "finish": "tool_calls" if final_calls else finish,
                       "usage": {**usage_total, "total_tokens": usage_total["prompt_tokens"] + n_out, "evidence_nodes": len(evidence),
                                 "tool_rounds": round_i},
                       "memory": self._memory(),
                       "timing": {"route_ms": ev["ms"], "ttft_ms": round((ttft or 0) * 1000, 1), "total_ms": round((time.perf_counter() - req.t0) * 1000, 1),
                                  "tokens_per_s": round(n_out / max(total_gen, 1e-6), 1) if n_out >= 8 else None}}

    def _memory(self) -> dict:
        try:
            import resource
            m = {"backend_peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
        except Exception:
            m = {}
        if self.ck:
            m["shard_bytes_resident"] = self.ck.shards.bytes()
        return m

    def _node_ev(self, c: dict, names: dict, text_nodes: list) -> dict:
        info = self.ck.nodes.get(c["id"], {}) if self.ck else {}
        cur = c["node"]["content_hash"]
        state = "trained" if info.get("key_trained") and info.get("hash") == cur else ("edited since training" if info else "not trained")
        return {"id": sid(c["id"]), "title": c["node"]["title"], "type": c["node"]["type"], "mpn_id": sid(c.get("via_mpn")), "mpn": names.get(c.get("via_mpn"), ""),
                "score": round(float(c["score"]), 3), "gate": round(float(c["gate"]), 3), "read_as": ["text"] if c in text_nodes else [], "via": c["via"],
                "state": state, **({"relation": c["relation"], "from": c["from"]} if c["via"] == "referral" else {})}

    def _tool(self, req: Request, call: dict, evidence: list, ev: dict, proc_tools: dict, b: dict, focus) -> dict:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        if name in proc_tools:
            c = proc_tools[name]
            impl = name[5:]
            p = procedures.REGISTRY[impl]
            r = procedures.run(impl, {k: v for k, v in args.items() if k in p.params})
            rec = {"node_id": sid(c["id"]), "title": c["node"]["title"], "impl": impl, "args": args, "ok": r["ok"], "result": r.get("result"),
                   "unit": r.get("unit", ""), "error": r.get("error")}
            ev["procedures"].append(rec)
            content = json.dumps({"result": r.get("result"), "unit": r.get("unit", "")} if r["ok"] else {"error": r.get("error")})
            return {"content": content, "summary": content[:200]}
        have = {c["id"] for c in evidence}
        new: list[dict] = []
        if name == "mpn_search":
            q = str(args.get("query", "")).strip()[:300]
            if q:
                routed = self.route(req.rev, q, b, exclude=have)
                for s in routed["evidence"]["shards"]:
                    ev["shards"].append({**s, "via": "mpn_search"})
                new = [c for c in self.select(req.rev, routed, {**b, "traversal_depth": 0}, want=3) if c["node"]["type"] != "procedure"][:3]
                if len(new) < 2:        # the router found little: add ordinary retrieval hits (labelled)
                    for c in self.baseline_retrieve(req.rev, q, 4):
                        if c["id"] not in have and all(c["id"] != x["id"] for x in new):
                            c["via"] = "search (text and encoder)"
                            new.append(c)
                        if len(new) >= 3:
                            break
            ev["searches"].append({"tool": "mpn_search", "query": q, "found": [c["node"]["title"] for c in new]})
            if not new and evidence:
                # the matching nodes are the ones already supplied; say so, so the core answers from them
                listed = "; ".join(f"[{i}] {c['node']['title']}" for i, c in enumerate(evidence, start=1))
                return {"content": f"The matching nodes are the ones already supplied: {listed}. Answer the question from their text.",
                        "summary": "no further nodes", "fruitless": True}
        elif name == "mpn_follow":
            try:
                k = int(args.get("node"))
            except (TypeError, ValueError):
                k = 0
            rel = str(args.get("relation", "any")).strip().lower().replace(" ", "_").replace("-", "_")
            rel = RELATION_ALIASES.get(rel, rel)
            already: list[str] = []
            if 1 <= k <= len(evidence):
                src = evidence[k - 1]
                edges = self.store.edges_of(src["id"], req.rev, limit=300)
                enabled = set(self._enabled(req.rev))
                pos = {c["id"]: i for i, c in enumerate(evidence, start=1)}
                for e in edges:
                    if e["type"] == "mentions" or e["direction"] != "out":
                        continue
                    if rel not in ("any", "", "all") and rel not in e["type"] and e["type"] not in rel:
                        continue
                    oid = int(e["other"]["id"])
                    if oid in have:      # the target is already supplied: say so instead of reporting no link
                        already.append(f"{e['type']} -> [{pos[oid]}] {evidence[pos[oid] - 1]['node']['title']}")
                        continue
                    if any(oid == x["id"] for x in new):
                        continue
                    n = self.store.get_node(oid, req.rev)
                    if n is None or n["status"] != "live":
                        continue
                    via = next((int(m["mpn_id"]) for m in n["memberships"] if int(m["mpn_id"]) in enabled), None)
                    if via is None:
                        continue
                    new.append({"id": oid, "score": 0.0, "gate": 0.5, "via_mpn": via, "home": None, "via": "mpn_follow", "node": n,
                                "relation": e["type"], "from": src["node"]["title"]})
                    if len(new) >= 4:
                        break
                links = sorted({e["type"] for e in edges if e["direction"] == "out" and e["type"] != "mentions"})
            else:
                links = []
            ev["searches"].append({"tool": "mpn_follow", "node": k, "relation": rel, "found": [c["node"]["title"] for c in new]})
            if not new and already:
                msg = f"The links of node [{k}] lead to nodes already supplied above: {'; '.join(dict.fromkeys(already))}."
                return {"content": msg, "summary": msg}
            if not new:
                msg = f"No {rel} link from node [{k}]." + (f" Its links: {', '.join(links[:20])}." if links else "")
                return {"content": msg, "summary": msg}
        start = len(evidence) + 1
        evidence.extend(new)
        names = {int(m["id"]): m["name"] for m in self.store.mpns(req.rev)}
        for c in new:
            ev["nodes"].append({**self._node_ev(c, names, new), "read_as": ["text"]})
        if not new:
            return {"content": "No matching nodes in the Modular Persistent Network.", "summary": "no nodes"}
        txt = "\n\n".join(render_node(c["node"], i, focus=focus) for i, c in enumerate(new, start=start))
        return {"content": "Knowledge nodes:\n" + txt, "summary": ", ".join(f"[{i}] {c['node']['title']}" for i, c in enumerate(new, start=start))}

    # ------------------------------------------------------------------ deterministic checks
    def validate(self, answer: str, chosen: list[dict], evidence: list[dict], procs: list[dict], question: str) -> dict:
        citations = []
        for i, c in enumerate(evidence, start=1):
            n = c["node"]
            src = next((s for s in n.get("sources", []) if s["kind"] in ("wikipedia", "user", "authored", "synthetic")), None) or (n.get("sources") or [None])[0]
            citations.append({"n": i, "node_id": sid(c["id"]), "title": n["title"], "cited": f"[{i}]" in answer,
                              "source": {k: src.get(k) for k in ("title", "url", "license", "revision", "kind")} if src else None})
        checks = []
        pool = {c["id"]: c for c in chosen + evidence}.values()
        evidence_text = question + " " + " ".join(
            " ".join(s.get("text", "") for s in c["node"]["cells"].get("knowledge", {}).get("statements", [])) + " " +
            " ".join(str(f.get("value", "")) + " " + str(f.get("as_of", "")) for f in c["node"]["cells"].get("knowledge", {}).get("facts", []))
            for c in pool) + " " + " ".join(json.dumps(p.get("result")) + " " + json.dumps(p.get("args")) for p in procs)
        ev_nums = set(re.findall(r"\d+(?:\.\d+)?", evidence_text.replace(",", "")))
        ans_nums = [x for x in re.findall(r"\d+(?:\.\d+)?", re.sub(r"\[\d+\]", "", answer).replace(",", "")) if len(x.replace(".", "")) >= 3]
        unsupported = sorted({x for x in ans_nums if x not in ev_nums and x.rstrip("0").rstrip(".") not in ev_nums})
        if evidence or procs:
            checks.append({"kind": "numbers_supported", "ok": not unsupported,
                           "detail": "Every number of three or more digits in the answer occurs in a node that was read, a tool result or the question."
                           if not unsupported else "Not found in any node read or tool result: " + ", ".join(unsupported[:8])})
        for p in procs:
            if p.get("ok"):
                vals = re.findall(r"-?\d+(?:\.\d+)?", json.dumps(p["result"]))
                a = answer.replace(",", "")
                hit = any(v in a or v.rstrip("0").rstrip(".") in a for v in vals) if vals else str(p["result"]).lower() in answer.lower()
                checks.append({"kind": "tool_result_used", "title": p["title"], "ok": bool(hit),
                               "detail": "The answer states the computed result." if hit else f"The answer does not state the computed result {json.dumps(p['result'])}."})
        if evidence:
            cited = sum(1 for c in citations if c["cited"])
            checks.append({"kind": "sources_cited", "ok": cited > 0, "detail": f"{cited} of {len(citations)} supplied nodes are cited in the answer."})
        if not evidence and not procs:
            checks.append({"kind": "no_node_support", "ok": True, "detail": "No node was read for this answer; it comes from the core alone."})
        return {"citations": citations, "checks": checks}
