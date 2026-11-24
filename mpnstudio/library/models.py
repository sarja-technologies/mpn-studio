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

from . import coreconf, cores, networks
from .. import names
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
    elif m["core"].get("endpoint_id"):
        try:
            coreconf.get(m["core"]["endpoint_id"])
        except coreconf.CoreServerError:
            problems.append("The chosen core server is no longer configured.")
    else:
        try:
            core = cores.get(m["core"]["id"])
            if not core["present"]:
                problems.append(f"The core file is missing: {core['path']}")
        except cores.CoreError:
            problems.append("The chosen core is no longer in the library.")
    net = m.get("network") or {}
    if is_fg(m):
        problems += fg_problems(m)
    elif not net.get("id"):
        problems.append("Choose a node network.")
    elif not projects.project_dir(net["id"]).exists():
        problems.append("The chosen network no longer exists.")
    b = m.get("build") or {}
    if problems:
        return {"state": "incomplete", "problems": problems}
    if not b.get("ok"):
        return {"state": "needs build", "problems": [b["error"]] if b.get("error") else []}
    stale = []
    if b.get("network_rev") != head_of(m):
        stale.append("The network changed since the last build.")
    if b.get("config_hash") != config_hash(m):
        if b.get("net_hash") == net_hash(m) and b.get("network_rev") == head_of(m):
            stale.append("The core changed since the last test. Apply it: the network is kept.")
        else:
            stale.append("The settings changed since the last build.")
    return {"state": "ready" if not stale else "needs build", "problems": stale, "built": b.get("at")}


def is_fg(m: dict) -> bool:
    """The pairing reads a native FG-MPN network where it is stored (a registered or installed release), not a Studio network."""
    net = m.get("network") or {}
    return bool(net.get("release")) and not net.get("id")


def fg_record(m: dict) -> dict | None:
    from ..catalogue import install
    r = (m.get("network") or {}).get("release") or {}
    return install.get_installed("mpn", r.get("id"), r.get("version"), "fgmpn") if r.get("id") else None


def fg_store(m: dict) -> Path | None:
    from ..catalogue import install
    rec = fg_record(m)
    return install.store_dir(rec) if rec else None


def fg_problems(m: dict) -> list[str]:
    """Why a pairing on an FG-MPN network cannot run now. Cheap: the runtime import and the manifest's sizes, no segment is read."""
    from ..mpnnet import fgmpn_local
    from ..engine import fgreason
    rec = fg_record(m)
    if rec is None:
        return ["The FG-MPN network of this pairing is not installed or registered on this computer."]
    try:
        info = fgmpn_local.inspect_store(fg_store(m))
    except fgmpn_local.FgError as e:
        return [str(e)]
    if not info["ok"]:
        return ["The FG-MPN store is missing parts: " + "; ".join((info["missing"] + info["wrong_size"])[:3]) + ". Verify it, or register it again."]
    why = fgreason.runtime_ok()
    return [why] if why else []


def fg_head(m: dict) -> str | None:
    """What identifies the store's content: generation and the SHA-256 of its live manifest (which lists every segment's SHA-256)."""
    from ..engine import fgreason
    st = fg_store(m)
    try:
        i = fgreason.store_identity(st)
        return f"gen{i['generation']}:{i['manifest_sha256'][:16]}"
    except (OSError, ValueError, TypeError):
        return None


def head_of(m: dict):
    return fg_head(m) if is_fg(m) else _net_head(m["network"]["id"])


def _net_head(slug: str) -> int | None:
    try:
        c = sqlite3.connect(f"file:{projects.store_path(slug)}?mode=ro", uri=True)
        try:
            return c.execute("SELECT MAX(rev) FROM revisions").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error:
        return None


def core_fp(m: dict) -> dict:
    """What identifies the core a pairing answers with: the weights' hash, or the server's current configuration."""
    c = m.get("core") or {}
    if c.get("endpoint_id"):
        try:
            e = coreconf.get(c["endpoint_id"])
        except coreconf.CoreServerError:
            return {"endpoint": c["endpoint_id"], "missing": True}
        return {"endpoint": e["id"], **{k: e.get(k) for k in ("base_url", "model_id", "context_tokens", "max_output_tokens", "timeout_s")}}
    return {"core": c.get("id"), "sha256": c.get("sha256")}


def net_hash(m: dict) -> str:
    """Everything a build's snapshot and index depend on. The core is not part of it."""
    import hashlib
    keys = {k: m.get(k) for k in ("encoder", "network", "retrieval", "context", "identity", "name", "model_id")}
    if is_fg(m):
        keys["encoder"] = None                     # an FG-MPN network is read without embeddings: the encoder plays no part
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:16]


def config_hash(m: dict) -> str:
    import hashlib
    keys = {"net": net_hash(m), "core": core_fp(m)}
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:16]


def list_models() -> list[dict]:
    out = []
    for p in sorted(models_dir().glob("*/model.json")):
        try:
            m = get(p.parent.name)
        except (ModelError, ValueError):
            continue
        m["name"] = names.display_name(m.get("name"))          # stored names are kept; the list shows the official spelling
        out.append(m)
    return sorted(out, key=lambda m: m.get("modified", ""), reverse=True)


def _core_ref(cid: str | None, kind: str) -> dict | None:
    if not cid:
        return None
    if coreconf.is_ref(cid):
        if kind != "core":
            raise ModelError("A core server cannot be used as a retrieval encoder.")
        try:
            e = coreconf.get(coreconf.ref_id(cid))
        except coreconf.CoreServerError as ex:
            raise ModelError(str(ex), ex.status)
        return {"id": cid, "endpoint_id": e["id"], "name": e["name"], "model_id": e["model_id"], "server": True}
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


def _pick_test_question_fg(root: Path) -> dict | None:
    """A question an FG-MPN network can answer, found without scanning it: a few thousand rows from the start of a few segments, the first
    entity with a description and a title that names exactly one entity."""
    from ..engine import fgreason
    net = fgreason.FgNetwork(root)
    try:
        V = net.V
        want = {V.KIND_ID[k] for k in ("person", "concept", "organisation")}
        step = max(1, len(net.store.segments) // 4)
        for s in net.store.segments[::step][:4]:
            for e in range(0, min(s.N, 40000), 1 if s.N <= 5000 else 7):
                c = s.cols(e)
                if c[4] != 1 or c[0] not in want or not (6 <= len(c[7]) <= 50):
                    continue
                r = s.row(e)
                desc = r.get("summary") or ""
                if len(desc) < 12:
                    continue
                if len(net.title_lookup(c[7], 3, fgreason.ENTITY_KINDS)) != 1:
                    continue
                return {"node_id": s.nid(e).hex(), "title": c[7], "question": f"What is {c[7]}? Answer in one or two sentences.", "statement": desc}
    finally:
        for sg in net.store.segments:
            sg.close()
    return None


def _core_parts(m: dict) -> dict:
    """The core's share of a runtime MANIFEST.json: its description, where its weights are (or `core_endpoint`, for a core on a
    server), and the vision projector. Never contains an API key."""
    c = m["core"]
    if c.get("endpoint_id"):
        e = coreconf.get(c["endpoint_id"])
        return {"core": {"name": e["name"], "sha256": None, "quantization": None, "parameters": None, "architecture": None, "server": True, "model_id": e["model_id"]},
                "core_endpoint": {"id": e["id"], "name": e["name"], "base_url": e["base_url"], "model_id": e["model_id"], "context_tokens": e.get("context_tokens"),
                                  "max_output_tokens": e.get("max_output_tokens"), "timeout_s": e.get("timeout_s"), "key_env": coreconf.KEY_ENV},
                "core_path": "none", "mmproj": "none", "key": coreconf.api_key(e["id"])}
    core = cores.get(c["id"])
    if not core["compatibility"]["runnable"]:
        raise ModelError(" ".join(core["compatibility"]["notes"]))
    return {"core": {"name": core["name"], "sha256": core["sha256"], "quantization": core["info"]["quantization"], "parameters": core["info"]["parameters"],
                     "architecture": core["info"]["architecture"]},
            "core_endpoint": None, "core_path": core["path"], "mmproj": (core.get("projector") or {}).get("path") or "none", "key": None}


def _test_inference(eng, tq: dict | None, log: Log) -> dict:
    """Ask the pairing one real question from its network. Raises ModelError unless an answer comes back with node evidence."""
    test: dict = {"question": None}
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
    return test


def build(mid: str, log: Log = print, device: str = "auto", cancelled: Callable[[], bool] | None = None) -> dict:
    """Validate, snapshot and index the network, write the runtime root, start the model and run a real test request
    with retrieval. The build counts as successful only if that request returns an answer with node evidence."""
    from ..engine.engine import Engine
    from ..engine.runtime import available
    m = _read(mid)
    st = status(m)
    if st["state"] == "incomplete":
        raise ModelError(" ".join(st["problems"]))
    fg = is_fg(m)
    enc = cores.get(m["encoder"]["id"]) if (m.get("encoder") and not fg) else None        # an FG-MPN network is read without embeddings
    parts = _core_parts(m)
    rt = available()
    if (not parts["core_endpoint"] or enc) and not rt["ok"]:      # llama.cpp is needed for a local core, and for the encoder
        raise ModelError(rt["reason"], 409)
    t0 = time.perf_counter()
    if enc and not enc["present"]:
        raise ModelError(f"The encoder file is missing: {enc['path']}")
    slug = None if fg else m["network"]["id"]
    net_head = head_of(m)
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
        if fg:
            # the store is referenced where it is: nothing is copied, snapshotted or embedded
            from ..engine import fgreason
            root_fg = fg_store(m)
            ident = fgreason.store_identity(root_fg)
            snap = {"nodes": ident["stored_records"], "edges": None}
            rinfo, use_router, cache = None, False, None
            step("network", format="FG-MPN, read in place", path=str(root_fg), generation=ident["generation"], stored_records=ident["stored_records"],
                 manifest_sha256=ident["manifest_sha256"][:16])
            step("routing", mode="FG-MPN entity linking over the store's indexes (no embeddings)")
        else:
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
                    "core": parts["core"], "core_endpoint": parts["core_endpoint"],
                    "encoder": {"name": enc["name"], "sha256": enc["sha256"]} if enc else None,
                    "paths": {"core": parts["core_path"], "mmproj": parts["mmproj"],
                              "encoder": enc["path"] if enc else "none", "network": str(net_path),
                              "checkpoint": rinfo["path"] if use_router else "none", **({"fgmpn": str(root_fg)} if fg else {})},
                    **({"network_format": {"format": "fgmpn", "generation": ident["generation"], "manifest_sha256": ident["manifest_sha256"],
                                           "release": (m.get("network") or {}).get("release")}} if fg else {})}
        (work / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
        budget = {"text_nodes": int(r.get("text_nodes", 4)), "retrieval_candidates": int(r.get("retrieval_candidates", 48)),
                  "max_active_mpns": int(r.get("max_active_mpns", 3)), "tool_rounds": int(r.get("tool_rounds", 2)),
                  "selected_nodes": int(r.get("selected_nodes", 8)), "concurrent_requests": int(c.get("parallel", 1)),
                  "output_tokens": int(c.get("output_tokens", 2048))}
        (work / "data" / "budget.json").write_text(json.dumps(budget))
        eng = Engine(work, work / "data", device=device, context=int(c.get("tokens", 8192)), core_key=parts["key"])
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
            tq = _pick_test_question_fg(root_fg) if fg else _pick_test_question(net_path)
            test = _test_inference(eng, tq, log)
            step("test inference", question=test.get("question"), retrieved_expected=test.get("expected_node_retrieved"),
                 answer=(test.get("answer") or "")[:200])
            report["test"] = test
            report["device"] = info["variant"]
            report["memory"] = eng._memory()
        finally:
            eng.unload()
            if cache:
                cache.close()
        if not fg:
            # the snapshot is final: fold its WAL so the file is self-contained
            s = sqlite3.connect(net_path)
            s.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            s.execute("PRAGMA journal_mode=DELETE")
            s.close()
        report.update({"ok": True, "finished": now(), "seconds": round(time.perf_counter() - t0, 1), "routing": "fgmpn" if fg else ("learned" if use_router else "retrieval"),
                       "network": ({"format": "fgmpn", "path": str(root_fg), "generation": ident["generation"], "manifest_sha256": ident["manifest_sha256"],
                                    "stored_records": snap["nodes"]} if fg else {"id": slug, "revision": net_head, "nodes": snap["nodes"], "relations": snap["edges"]})})
        (work / "build.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
        old = d / "build"
        if old.exists():
            shutil.rmtree(old)
        os.replace(work, old)
        # paths in the manifest pointed into build.new
        man = json.loads((old / "MANIFEST.json").read_text())
        man["paths"]["network"] = str(old / "network.sqlite")      # (an FG-MPN pairing has no such file; its path stays the store's own)
        (old / "MANIFEST.json").write_text(json.dumps(man, indent=1, ensure_ascii=False))
        m = _read(mid)
        m["build"] = {"ok": True, "at": report["finished"], "network_rev": net_head, "config_hash": config_hash(m), "net_hash": net_hash(m), "routing": report["routing"],
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


def rebind(mid: str, log: Log = print, device: str = "auto") -> dict:
    """Apply a changed core to a built pairing without rebuilding: the network snapshot and its retrieval index are kept, the
    runtime manifest gets the new core, and the same test question is asked of it. Fails, changing nothing, if the network,
    encoder or settings changed too (that needs a build) or if the new core does not answer."""
    from ..engine.engine import Engine
    from ..engine.runtime import available
    m = _read(mid)
    b = m.get("build") or {}
    if not b.get("ok"):
        raise ModelError("This pairing has not been built yet. Build it first.", 409)
    if b.get("net_hash") != net_hash(m) or b.get("network_rev") != head_of(m):
        raise ModelError("The network or the settings changed since the last build, so the snapshot has to be rebuilt.", 409)
    root = model_dir(mid) / "build"
    mpath = root / "MANIFEST.json"
    old_text = mpath.read_text()
    man = json.loads(old_text)
    enc = man.get("encoder")
    parts = _core_parts(m)
    rt = available()
    if (not parts["core_endpoint"] or enc) and not rt["ok"]:
        raise ModelError(rt["reason"], 409)
    t0 = time.perf_counter()
    man.update({"core": parts["core"], "core_endpoint": parts["core_endpoint"]})
    man["paths"].update({"core": parts["core_path"], "mmproj": parts["mmproj"]})
    mpath.write_text(json.dumps(man, indent=1, ensure_ascii=False))
    try:
        prev = json.loads((root / "build.json").read_text())
        q = (prev.get("test") or {})
        tq = {"node_id": (q.get("expected_node") or {}).get("id"), "title": (q.get("expected_node") or {}).get("title"), "question": q.get("question")} if q.get("question") else None
        eng = Engine(root, root / "data", device=device, context=int((m.get("context") or {}).get("tokens", 8192)), core_key=parts["key"])
        try:
            info = eng.load(log=lambda s: log("  " + s))
            log(f"core connected in {info['load_s']} s")
            test = _test_inference(eng, tq, log)
        finally:
            eng.unload()
    except Exception:
        mpath.write_text(old_text)
        raise
    m = _read(mid)
    b = dict(m["build"])
    b.update({"config_hash": config_hash(m), "device": info["variant"], "seconds": round(time.perf_counter() - t0, 1), "at": now(), "rebound": True,
              "test": {k: test.get(k) for k in ("question", "answer", "expected_node_retrieved", "timing")}})
    m["build"] = b
    _write(mid, m)
    prev["test"], prev["rebound"] = test, now()
    (root / "build.json").write_text(json.dumps(prev, indent=1, ensure_ascii=False))
    return get(mid)


def create_from_release(r: dict, mpn_rec: dict, core_rec: dict | None, endpoint_id: str | None = None, enc_rec: dict | None = None) -> dict:
    """A pairing made by installing an official `model` release: it refers to the installed network and core, and keeps the release's defaults."""
    d = r["detail"]
    dfl = d.get("defaults") or {}
    set_ = dfl.get("settings") or {}
    if endpoint_id:
        core_id = coreconf.PREFIX + endpoint_id
    else:
        core_id = (core_rec or {}).get("core_id")
    if mpn_rec.get("format") == "studio-store":
        m = create(r["name"], f"Official pairing {d.get('pairing') or r['id']} {r['version']}", core_id, mpn_rec["project"], (enc_rec or {}).get("core_id"))
    else:
        m = create(r["name"], f"Official pairing {d.get('pairing') or r['id']} {r['version']}", core_id, None, None)
        raw = _read(m["id"])
        raw["network"] = {"release": {"id": mpn_rec["id"], "version": mpn_rec["version"], "format": mpn_rec.get("format")}}
        _write(m["id"], raw)
    raw = _read(m["id"])
    raw["release"] = {"id": r["id"], "version": r["version"], "pairing": d.get("pairing"), "mpn": {"id": mpn_rec["id"], "version": mpn_rec["version"]},
                      "core": ({"id": core_rec["id"], "version": core_rec["version"]} if core_rec else None),
                      "encoder": ({"id": enc_rec["id"], "version": enc_rec["version"]} if enc_rec else None)}
    if dfl.get("model_id") and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", str(dfl["model_id"])):
        raw["model_id"] = dfl["model_id"]
    for k in ("retrieval", "context"):
        if isinstance(set_.get(k), dict):
            raw[k] = {**(raw.get(k) or {}), **{a: b for a, b in set_[k].items() if a in (DEFAULT_RETRIEVAL if k == "retrieval" else DEFAULT_CONTEXT)}}
    if isinstance(set_.get("identity"), str):
        raw["identity"] = set_["identity"]
    _write(m["id"], raw)
    return get(m["id"])


def build_root(mid: str) -> Path:
    d = model_dir(mid) / "build"
    if not (d / "MANIFEST.json").exists():
        raise ModelError("Build the model first.", 409)
    return d
