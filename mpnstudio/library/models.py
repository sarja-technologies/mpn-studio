"""MPN models: a cognitive core + a node network + configuration, built into something that runs.

<data>/models/<id>/
    model.json       the model project (format mpn-studio-model/1)
    build/           the last successful build: a runtime root the MPN runtime can start
        MANIFEST.json    absolute paths of the core, projector, encoder, router; identity; settings
        network.sqlite   a snapshot of the network at build time, with its retrieval index
        data/            the runtime's logs and conversations
        build.json       what the build did and the result of its test inference

Building never changes the core or the network the user is editing. The snapshot is what the model answers from and
what an export contains; rebuilding picks up later edits.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import struct
import time
from pathlib import Path
from typing import Callable

from . import cores, networks
from ..desktop import paths, projects
from ..store import netops

FORMAT = "mpn-studio-model/1"
Log = Callable[[str], None]

DEFAULT_RETRIEVAL = {"routing": "auto", "text_nodes": 4, "retrieval_candidates": 48, "max_active_mpns": 3, "tool_rounds": 2, "selected_nodes": 8}
DEFAULT_CONTEXT = {"tokens": 8192, "parallel": 1, "output_tokens": 2048}


class ModelError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def models_dir() -> Path:
    d = paths.data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_dir(mid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", mid or ""):
        raise ModelError("Invalid model id.")
    return models_dir() / mid


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def default_identity(name: str, core: dict | None, network: dict | None) -> str:
    core_name = (core or {}).get("name") or "an open-weight language model"
    nodes = (network or {}).get("nodes")
    net = f" and a persistent knowledge network of {nodes:,} nodes" if nodes else " and a persistent knowledge network"
    return (f"You are {name}, an AI assistant built with MPN Studio. You combine a cognitive core ({core_name}, used unchanged){net} "
            "(a Modular Persistent Network, MPN) that you search for facts before answering.")


def _read(mid: str) -> dict:
    p = model_dir(mid) / "model.json"
    if not p.exists():
        raise ModelError("No such model.", 404)
    return json.loads(p.read_text())


def _write(mid: str, m: dict) -> None:
    m["modified"] = now()
    p = model_dir(mid) / "model.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


def get(mid: str) -> dict:
    m = _read(mid)
    m["status"] = status(m)
    return m


def status(m: dict) -> dict:
    """ready | needs build | incomplete, with the reasons."""
    problems = []
    core = None
    if not m.get("core"):
        problems.append("Choose a cognitive core.")
    else:
        try:
            core = cores.get(m["core"]["id"])
            if not core["present"]:
                problems.append(f"The core file is missing: {core['path']}")
        except cores.CoreError:
            problems.append("The chosen core is no longer in the library.")
    if not (m.get("network") or {}).get("id"):
        problems.append("Choose a node network.")
    elif not projects.project_dir(m["network"]["id"]).exists():
        problems.append("The chosen network no longer exists.")
    b = m.get("build") or {}
    if problems:
        return {"state": "incomplete", "problems": problems}
    if not b.get("ok"):
        return {"state": "needs build", "problems": [b["error"]] if b.get("error") else []}
    stale = []
    if b.get("network_rev") != _net_head(m["network"]["id"]):
        stale.append("The network changed since the last build.")
    if b.get("config_hash") != config_hash(m):
        stale.append("The settings changed since the last build.")
    return {"state": "ready" if not stale else "needs build", "problems": stale, "built": b.get("at")}


def _net_head(slug: str) -> int | None:
    try:
        c = sqlite3.connect(f"file:{projects.store_path(slug)}?mode=ro", uri=True)
        try:
            return c.execute("SELECT MAX(rev) FROM revisions").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error:
        return None


def config_hash(m: dict) -> str:
    import hashlib
    keys = {k: m.get(k) for k in ("core", "encoder", "network", "retrieval", "context", "identity", "name", "model_id")}
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:16]


def list_models() -> list[dict]:
    out = []
    for p in sorted(models_dir().glob("*/model.json")):
        try:
            out.append(get(p.parent.name))
        except (ModelError, ValueError):
            continue
    return sorted(out, key=lambda m: m.get("modified", ""), reverse=True)


def _core_ref(cid: str | None, kind: str) -> dict | None:
    if not cid:
        return None
    c = cores.get(cid)
    if c["kind"] != kind:
        raise ModelError(f"{c['name']} is a {c['kind']}, not a {'cognitive core' if kind == 'core' else 'retrieval encoder'}.")
    i = c["info"]
    return {"id": c["id"], "sha256": c["sha256"], "name": c["name"], "architecture": i["architecture"], "parameters": i["parameters"],
            "quantization": i["quantization"], "bytes": i["bytes"], "license": i.get("license")}


def create(name: str, description: str = "", core_id: str | None = None, network_id: str | None = None, encoder_id: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ModelError("Give the model a name.")
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "model"
    mid, n = base, 1
    while model_dir(mid).exists():
        n += 1
        mid = f"{base}-{n}"
    if encoder_id is None:      # default: the first encoder in the library (the Yaob encoder, if imported)
        enc = next((c for c in cores.list_cores() if c["kind"] == "encoder" and c["present"]), None)
        encoder_id = enc["id"] if enc else None
    m = {"format": FORMAT, "id": mid, "name": name, "description": description, "version": "1.0.0", "created": now(),
         "model_id": mid, "core": _core_ref(core_id, "core"), "encoder": _core_ref(encoder_id, "encoder"),
         "network": {"id": network_id} if network_id else None, "retrieval": dict(DEFAULT_RETRIEVAL), "context": dict(DEFAULT_CONTEXT),
         "identity": None, "licence": {"model": None, "notes": ""}, "build": None}
    model_dir(mid).mkdir(parents=True)
    _write(mid, m)
    return get(mid)


EDITABLE = {"name", "description", "version", "model_id", "identity", "retrieval", "context", "licence"}


def update(mid: str, patch: dict) -> dict:
    m = _read(mid)
    for k, v in patch.items():
        if k == "core_id":
            m["core"] = _core_ref(v, "core")
        elif k == "encoder_id":
            m["encoder"] = _core_ref(v, "encoder") if v else None
        elif k == "network_id":
            if v and not projects.project_dir(v).exists():
                raise ModelError("No such network.")
            m["network"] = {"id": v} if v else None
        elif k in EDITABLE:
            if k == "model_id" and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", str(v or "")):
                raise ModelError("The API id may use lower-case letters, digits, '.', '_' and '-'.")
            if k in ("retrieval", "context") and isinstance(v, dict):
                m[k] = {**(m.get(k) or {}), **v}
            else:
                m[k] = v
        else:
            raise ModelError(f"cannot set {k}")
    _write(mid, m)
    return get(mid)


def remove(mid: str) -> dict:
    t = paths.trash_dir() / f"model-{mid}-{int(time.time())}"
    t.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(model_dir(mid)), t)
    return {"removed": mid, "moved_to": str(t)}


# ------------------------------------------------------------------------------------------------ embedding cache
class EmbeddingCache:
    """Node embeddings by (encoder, node content hash), shared by every build on this computer, so a duplicated or
    edited network only embeds what changed."""

    def __init__(self, encoder_sha: str):
        d = paths.cache_dir() / "embeddings"
        d.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(d / f"{encoder_sha[:16]}.sqlite", isolation_level=None)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("CREATE TABLE IF NOT EXISTS emb(content_hash TEXT PRIMARY KEY, dim INTEGER NOT NULL, v BLOB NOT NULL)")

    def fill_overlay(self, store_path: Path) -> int:
        """Copy cached embeddings into the snapshot's overlay for nodes that have none. Returns how many."""
        s = sqlite3.connect(store_path, isolation_level=None)
        try:
            s.execute("ATTACH DATABASE ? AS cache", (self.c.execute("PRAGMA database_list").fetchone()[2],))
            s.execute("BEGIN IMMEDIATE")
            n = s.execute("INSERT OR REPLACE INTO overlay(node_id, content_hash, slots, dim, node_emb, stmt_emb, updated) "
                          "SELECT n.node_id, n.content_hash, 0, e.dim, e.v, x'', datetime('now') FROM node_v n JOIN cache.emb e ON e.content_hash=n.content_hash "
                          "LEFT JOIN overlay o ON o.node_id=n.node_id AND o.content_hash=n.content_hash "
                          "WHERE n.rev_to IS NULL AND n.status<>'tombstone' AND o.node_id IS NULL").rowcount
            s.execute("INSERT INTO derived_state(node_id, embed_hash) SELECT o.node_id, o.content_hash FROM overlay o WHERE true "
                      "ON CONFLICT(node_id) DO UPDATE SET embed_hash=excluded.embed_hash")
            s.execute("COMMIT")
            s.execute("DETACH DATABASE cache")
            return n
        finally:
            s.close()

    def store_overlay(self, store_path: Path) -> int:
        s = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
        try:
            rows = s.execute("SELECT content_hash, dim, node_emb FROM overlay").fetchall()
        finally:
            s.close()
        self.c.execute("BEGIN")
        self.c.executemany("INSERT OR IGNORE INTO emb VALUES(?,?,?)", rows)
        self.c.execute("COMMIT")
        return len(rows)

    def close(self):
        self.c.close()


# ------------------------------------------------------------------------------------------------ build
def _pick_test_question(store_path: Path) -> dict | None:
    """A question the network can answer: a live knowledge node in an enabled MPN, with a stated fact."""
    c = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        rows = c.execute(
            "SELECT n.node_id, n.title, n.cells FROM node_v n JOIN member_v m ON m.node_id=n.node_id AND m.rev_to IS NULL "
            "JOIN mpn_v p ON p.mpn_id=m.mpn_id AND p.rev_to IS NULL AND p.enabled=1 AND p.installed=1 AND p.kind<>'diagnostic' "
            "WHERE n.rev_to IS NULL AND n.status='live' AND n.type='knowledge' AND length(n.title) BETWEEN 4 AND 60 "
            "ORDER BY (abs(n.node_id * 2654435761) % 1000003) LIMIT 50").fetchall()
    finally:
        c.close()
    for nid, title, cells in rows:
        st = (json.loads(cells).get("knowledge") or {}).get("statements") or []
        if st and len(st[0].get("text", "")) > 30:
            return {"node_id": str(nid), "title": title, "question": f"What is {title}? Answer in one or two sentences.", "statement": st[0]["text"]}
    return None


def build(mid: str, log: Log = print, device: str = "auto", cancelled: Callable[[], bool] | None = None) -> dict:
    """Validate, snapshot and index the network, write the runtime root, start the model and run a real test request
    with retrieval. The build counts as successful only if that request returns an answer with node evidence."""
    from ..engine.engine import Engine
    from ..engine.runtime import available
    m = _read(mid)
    st = status(m)
    if st["state"] == "incomplete":
        raise ModelError(" ".join(st["problems"]))
    rt = available()
    if not rt["ok"]:
        raise ModelError(rt["reason"], 409)
    t0 = time.perf_counter()
    core = cores.get(m["core"]["id"])
    if not core["compatibility"]["runnable"]:
        raise ModelError(" ".join(core["compatibility"]["notes"]))
    enc = cores.get(m["encoder"]["id"]) if m.get("encoder") else None
    if enc and not enc["present"]:
        raise ModelError(f"The encoder file is missing: {enc['path']}")
    slug = m["network"]["id"]
    net_head = _net_head(slug)
    d = model_dir(mid)
    work = d / "build.new"
    shutil.rmtree(work, ignore_errors=True)
    (work / "data" / "logs").mkdir(parents=True)
    report: dict = {"started": now(), "steps": []}

    def step(name, **kw):
        report["steps"].append({"step": name, "s": round(time.perf_counter() - t0, 1), **kw})
        log(f"{name}" + (f": {json.dumps(kw, ensure_ascii=False)}" if kw else ""))

    try:
        net_path = work / "network.sqlite"
        snap = netops.extract(projects.store_path(slug), net_path, message=f"snapshot of network {slug} at revision {net_head} for model {mid}")
        step("network snapshot", nodes=snap["nodes"], relations=snap["edges"], revision=net_head)
        if snap["nodes"] == 0:
            raise ModelError("The network has no live nodes.")
        # learned router: only with the encoder it was trained for
        rinfo = networks.router(slug)
        routing = (m.get("retrieval") or {}).get("routing", "auto")
        use_router = False
        if rinfo and routing in ("auto", "learned"):
            want = (rinfo.get("encoder") or {}).get("sha256")
            use_router = bool(enc and want and want == enc["sha256"])
            if not use_router and routing == "learned":
                raise ModelError("This network's learned router needs the encoder it was trained with; choose that encoder, or use retrieval routing.")
        step("routing", mode="learned router" if use_router else ("retrieval: encoder and text search" if enc else "retrieval: text search only"))
        cache = EmbeddingCache(enc["sha256"]) if enc else None
        if cache:
            step("embedding cache", reused=cache.fill_overlay(net_path))
        r, c = m.get("retrieval") or {}, m.get("context") or {}
        identity = m.get("identity") or default_identity(m["name"], m.get("core"), {"nodes": snap["nodes"]})
        manifest = {"name": m["name"], "model_id": m.get("model_id") or mid, "identity": identity, "owned_by": "local",
                    "version": m.get("version"), "network_writable": True, "built": now(),
                    "core": {"name": core["name"], "sha256": core["sha256"], "quantization": core["info"]["quantization"],
                             "parameters": core["info"]["parameters"], "architecture": core["info"]["architecture"]},
                    "encoder": {"name": enc["name"], "sha256": enc["sha256"]} if enc else None,
                    "paths": {"core": core["path"], "mmproj": (core.get("projector") or {}).get("path") or "none",
                              "encoder": enc["path"] if enc else "none", "network": str(net_path),
                              "checkpoint": rinfo["path"] if use_router else "none"}}
        (work / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
        budget = {"text_nodes": int(r.get("text_nodes", 4)), "retrieval_candidates": int(r.get("retrieval_candidates", 48)),
                  "max_active_mpns": int(r.get("max_active_mpns", 3)), "tool_rounds": int(r.get("tool_rounds", 2)),
                  "selected_nodes": int(r.get("selected_nodes", 8)), "concurrent_requests": int(c.get("parallel", 1)),
                  "output_tokens": int(c.get("output_tokens", 2048))}
        (work / "data" / "budget.json").write_text(json.dumps(budget))
        eng = Engine(work, work / "data", device=device, context=int(c.get("tokens", 8192)))
        try:
            info = eng.load(log=lambda s: log("  " + s))
            step("model loaded", device=info["variant"], seconds=info["load_s"])
            if cancelled and cancelled():
                raise ModelError("Cancelled.")
            if eng.enc is not None:
                total = covered = 0
                while True:
                    rr = eng.reindex(limit=2000)
                    total += rr.get("indexed", 0)
                    covered += rr.get("covered_by_router", 0)
                    if rr.get("indexed"):
                        log(f"  indexed {total} nodes")
                    if not rr.get("indexed") and not rr.get("more"):
                        break
                    if cancelled and cancelled():
                        raise ModelError("Cancelled.")
                step("retrieval index", embedded_now=total, covered_by_router=covered)
                if cache:
                    cache.store_overlay(net_path)
            tq = _pick_test_question(net_path)
            test = {"question": None}
            if tq:
                ev = {"answer": "", "nodes": [], "route": None, "done": None, "error": None}
                for kind, data in eng.run([{"role": "user", "content": tq["question"]}], mode="yaob"):
                    if kind == "token":
                        ev["answer"] += data["text"]
                    elif kind == "route":
                        ev["route"] = data
                    elif kind == "done":
                        ev["done"] = data
                    elif kind == "error":
                        ev["error"] = data.get("error")
                nodes = [n for n in ((ev["route"] or {}).get("nodes") or []) if "text" in (n.get("read_as") or [])]
                supplied = [n.get("id") for n in nodes]
                test = {"question": tq["question"], "expected_node": {"id": tq["node_id"], "title": tq["title"]}, "answer": ev["answer"].strip(),
                        "error": ev["error"], "evidence_nodes": [{"id": n.get("id"), "title": n.get("title"), "via": n.get("via")} for n in nodes[:8]],
                        "expected_node_retrieved": tq["node_id"] in supplied,
                        "timing": (ev["done"] or {}).get("timing"), "usage": (ev["done"] or {}).get("usage")}
                if ev["error"] or not test["answer"]:
                    raise ModelError(f"The test request failed: {ev['error'] or 'empty answer'}")
                if not nodes:
                    raise ModelError("The test request ran, but retrieval supplied no node.")
            step("test inference", question=test.get("question"), retrieved_expected=test.get("expected_node_retrieved"),
                 answer=(test.get("answer") or "")[:200])
            report["test"] = test
            report["device"] = info["variant"]
            report["memory"] = eng._memory()
        finally:
            eng.unload()
            if cache:
                cache.close()
        # the snapshot is final: fold its WAL so the file is self-contained
        s = sqlite3.connect(net_path)
        s.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        s.execute("PRAGMA journal_mode=DELETE")
        s.close()
        report.update({"ok": True, "finished": now(), "seconds": round(time.perf_counter() - t0, 1), "routing": "learned" if use_router else "retrieval",
                       "network": {"id": slug, "revision": net_head, "nodes": snap["nodes"], "relations": snap["edges"]}})
        (work / "build.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        old = d / "build"
        if old.exists():
            shutil.rmtree(old)
        os.replace(work, old)
        # paths in the manifest pointed into build.new
        man = json.loads((old / "MANIFEST.json").read_text())
        man["paths"]["network"] = str(old / "network.sqlite")
        (old / "MANIFEST.json").write_text(json.dumps(man, indent=1, ensure_ascii=False))
        m = _read(mid)
        m["build"] = {"ok": True, "at": report["finished"], "network_rev": net_head, "config_hash": config_hash(m), "routing": report["routing"],
                      "device": report["device"], "seconds": report["seconds"], "test": {k: report["test"].get(k) for k in
                                                                                       ("question", "answer", "expected_node_retrieved", "timing")}}
        _write(mid, m)
        return get(mid)
    except Exception as e:
        shutil.rmtree(work, ignore_errors=True)
        m = _read(mid)
        prev = m.get("build") or {}
        m["build"] = {**prev, "ok": prev.get("ok", False), "error": str(e)[:800], "error_at": now()} if prev.get("ok") else {"ok": False, "error": str(e)[:800], "error_at": now()}
        _write(mid, m)
        raise


def build_root(mid: str) -> Path:
    d = model_dir(mid) / "build"
    if not (d / "MANIFEST.json").exists():
        raise ModelError("Build the model first.", 409)
    return d
