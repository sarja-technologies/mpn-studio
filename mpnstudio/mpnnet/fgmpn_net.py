"""Studio's own reader for FG-MPN stores: the `mpn-network/1` routes over the public FG-MPN runtime.

The FG-MPN runtime (https://github.com/sarja-technologies/fgmpn, wheels bundled in the packaged app) reads, verifies,
searches and traverses a store, and offers the MPN-14B v1 read API over it (`fgmpn.v1api`). It has no network server.
This module is the server: it puts Studio's vendored `Federation` and `NetworkReader` on that v1 API, so search, the
node inspector, relationships and the canvas answer exactly as they do for a pack, and adds the small `fgmpn-network/1`
route set the interface uses (storage, address lookup, what is stored about a node). Everything is read-only.

    python -m mpnstudio.mpnnet.fgmpn_serve ROOT --host 127.0.0.1 --port 8858 [--name NAME]
    python -m mpnstudio.mpnnet.fgmpn_serve stats ROOT           # write ROOT/stats/gen-N.json (counts by kind, domain, dataset)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import schema as S
from .netapi import NetworkReader
from .pack import Federation

STATS_FORMAT = "fgmpn-stats/1"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
BROWSE_WINDOW = 120_000                 # records one browse call may look at before it hands back a cursor


# ------------------------------------------------------------------------------------------------ statistics
def stats_path(root: Path, generation: int) -> Path:
    return root / "stats" / f"gen-{int(generation):06d}.json"


def compute_stats(store) -> dict:
    """One pass over the stored records: validated and quarantined nodes by kind, domain and dataset, and the relationships.
    The count tables a pack keeps do not exist in a store, so a store ships this file (or Studio writes it once)."""
    from fgmpn import v1compat as V
    t0 = time.time()
    by_kind: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    by_ds: dict[str, int] = {}
    ok = quar = other = edges = 0
    multi = len(store.segments) > 1
    for s in store.segments:
        for e in range(s.N):
            c = s.cols(e)
            if multi and not store._newest_is(s, e, s.nid(e)):
                continue
            if c[4] == 1:
                ok += 1
                k, d = V.KINDS[c[0]], V.DOMAINS[c[1]]
                by_kind[k] = by_kind.get(k, 0) + 1
                by_domain[d] = by_domain.get(d, 0) + 1
                by_ds[str(c[2])] = by_ds.get(str(c[2]), 0) + 1
                edges += sum(1 for _ in s.edges(e, 0, None, None))
            elif c[4] == 2:
                quar += 1
            else:
                other += 1
    return {"format": STATS_FORMAT, "generation": store.generation, "edges_built": edges,
            "edges_note": "relationships stored in the segments, counted by Studio", "seconds": round(time.time() - t0, 1),
            "validated": ok, "quarantined": quar, "other_status": other, "by_kind": by_kind, "by_domain": by_domain, "by_dataset": by_ds}


def load_or_compute_stats(root: Path, store, write: bool = False) -> dict | None:
    p = stats_path(root, store.generation)
    if p.is_file():
        try:
            st = json.loads(p.read_text())
            if st.get("format") == STATS_FORMAT:
                return st
        except (OSError, ValueError):
            pass
    nodes = sum(s.N for s in store.segments)
    if nodes > (2_000_000 if write else 300_000):      # a bigger store ships its own statistics file
        return None
    st = compute_stats(store)
    if write:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1))
        os.replace(tmp, p)
    return st


def default_name(root: Path) -> str:
    """The name of a store Studio installed (its INSTALL.json, a folder or two up), else the folder's name."""
    for d in (root.parent, root.parent.parent):
        try:
            n = json.loads((d / "INSTALL.json").read_text()).get("name")
            if n:
                return str(n)
        except (OSError, ValueError, AttributeError):
            pass
    return root.name


# ------------------------------------------------------------------------------------------------ the reader
class _Fed(Federation):
    def __init__(self, v1):
        self.packs = [({"pack_id": "fgmpn", "version": None, "live": True, "engine": "fgmpn"}, v1)]


class FgReader(NetworkReader):
    def __init__(self, root: Path, name: str | None = None):
        from fgmpn import v1compat as V
        from fgmpn.adapter import NetworkAdapter
        from fgmpn.store import FGStore
        from fgmpn.v1api import FGV1Store
        self.root = Path(root)
        self.store = FGStore(self.root)
        self.V = V
        self.adapter = NetworkAdapter(self.store, "local")
        self.stats = load_or_compute_stats(self.root, self.store)
        ds = {}
        for i in (self.stats or {}).get("by_dataset", {}):
            slug = V.DATASET_SLUG.get(int(i), str(i))
            ds[str(i)] = {"slug": slug, "name": slug, "licence": None, "attribution": None}
        for i, d in ds.items():                           # a dataset's licence is read from its first record
            for s in self.store.segments:
                for e in range(min(s.N, 4000)):
                    c = s.cols(e)
                    if str(c[2]) == i:
                        d["licence"] = V.LIC_NAME.get(c[3])
                        break
                if d["licence"]:
                    break
        info = {"name": name or default_name(self.root), "version": f"generation {self.store.generation}", "location": f"local ({self.root})",
                "datasets": ds, "last_validation": None, "construction": None, "target_nodes": None}
        super().__init__(_Fed(FGV1Store(self.store, "local")), info)

    # ---- overview: counts from the store's statistics file
    def overview(self, max_age_s: float = 60) -> dict:
        st = self.stats or {}
        size = sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
        stored = sum(s.N for s in self.store.segments)
        datasets = [{"id": int(k), "slug": self.ds.get(int(k), {}).get("slug", k), "name": self.ds.get(int(k), {}).get("name", k),
                     "licence": self.ds.get(int(k), {}).get("licence"), "nodes": n}
                    for k, n in sorted((st.get("by_dataset") or {}).items(), key=lambda kv: -kv[1])]
        return {"api": "mpn-network/1", "engine": "fgmpn", "name": self.info["name"], "version": self.info["version"],
                "location": self.info["location"], "read_only": True,
                "validated_nodes": st.get("validated", stored), "quarantined_nodes": st.get("quarantined", 0),
                "relationships": st.get("edges_built", 0),
                "domains": sorted(({"domain": k, "nodes": v} for k, v in (st.get("by_domain") or {}).items()), key=lambda x: -x["nodes"]),
                "kinds": sorted(({"kind": k, "nodes": v} for k, v in (st.get("by_kind") or {}).items()), key=lambda x: -x["nodes"]),
                "datasets": datasets,
                "packs": [{"pack_id": "fgmpn", "version": f"generation {self.store.generation}", "partitions": len(self.store.segments),
                           "bytes": size, "validated": st.get("validated", stored), "live": False}],
                "storage_bytes": size,
                "indexes": {"full_text": "built (FG-MPN postings over titles and aliases)", "identifiers": "built (alias index)",
                            "vectors": "none (retrieval is lexical)"},
                "fgmpn": {"generation": self.store.generation, "segments": len(self.store.segments), "stored_records": stored,
                          "engine": self.store.engine, "counts_from": "store statistics" if self.stats else "not available"},
                "last_validation": None, "construction": None, "target_nodes": None, "ts": time.time()}

    # ---- browse: storage order, cursor "segment:record"
    def browse(self, domain=None, kind=None, dataset=None, cursor=None, limit=25, boundary=False) -> dict:
        t0 = time.time()
        limit = max(1, min(int(limit), 100))
        ds_id = self._filters(kind, domain, dataset)
        V = self.V
        want_kind = V.KIND_ID[kind] if kind else None
        want_dom = V.DOMAINS.index(domain) if domain else None
        si, e0 = 0, 0
        if cursor:
            a, b = (cursor.split(":") + ["0"])[:2]
            si, e0 = int(a), int(b)
        segs = self.store.segments
        out, looked = [], 0
        multi = len(segs) > 1
        while si < len(segs) and len(out) < limit and looked < BROWSE_WINDOW:
            s = segs[si]
            e = e0
            while e < s.N and len(out) < limit and looked < BROWSE_WINDOW:
                looked += 1
                c = s.cols(e)
                e += 1
                if c[4] != 1 or (want_kind is not None and c[0] != want_kind) or (want_dom is not None and c[1] != want_dom):
                    continue
                if want_kind is None and want_dom is None and c[0] == V.CLAIM:
                    continue                                         # claims are reached from their subject
                if ds_id is not None and int(c[2]) != int(ds_id):
                    continue
                if multi and not self.store._newest_is(s, e - 1, s.nid(e - 1)):
                    continue
                d = s.row(e - 1)
                d["pack"] = "fgmpn"
                if boundary and not self._crosses(d):
                    continue
                out.append(self._card(self.fed.render(d)))
            if e >= s.N:
                si, e0 = si + 1, 0
            else:
                e0 = e
        nxt = f"{si}:{e0}" if si < len(segs) else None
        return {"results": out, "cursor": nxt, "ms": round((time.time() - t0) * 1000, 1),
                "filters": {"domain": domain, "kind": kind, "dataset": dataset, "boundary": boundary}}

    # ---- the fgmpn-network/1 routes
    def fg_about(self) -> dict:
        from fgmpn import __version__ as fv
        return {"format": "fgmpn-network/1", "runtime": fv, "engine": self.store.engine, "generation": self.store.generation,
                "read_only": True, "derived_statements": "not listed; look one up by its address"}

    def fg_storage(self) -> dict:
        acc = self.store.accounting()
        by = {}
        for a in acc["segments"]:
            for k, v in (a.get("sections") or {}).items():
                by[k] = by.get(k, 0) + v
        return {"fgmpn": {"deployed_bytes": acc["deployed_bytes"], "segments": len(acc["segments"]), "by_section": by}, "v1": None,
                "ratio_v1_over_fgmpn": None}

    def fg_resolve(self, address: str) -> dict:
        return self.adapter.resolve(address)

    def fg_node(self, nid: str) -> dict:
        g = self.store.get(bytes.fromhex(nid), "local")
        out = {k: g[k] for k in ("status", "id", "reason", "segment", "generation") if k in g}
        out["provenance"] = [{"dataset": self.V.DATASET_SLUG.get(int(d), str(d)), "record": rec} for d, rec in g.get("provenance") or []]
        out["derived"] = []
        out["derived_listed"] = False                 # the explainer is not part of the public runtime; addresses are looked up with /resolve
        out["compare"] = False                        # there is no original (v1) network here to compare with
        return out


# ------------------------------------------------------------------------------------------------ the server
class Handler(BaseHTTPRequestHandler):
    reader: FgReader
    server_version = "mpn-fgmpn/1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body) -> None:
        b = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items() if v and v[0] != ""}
        parts = [unquote(p) for p in u.path.strip("/").split("/")]
        r = self.reader
        num = lambda k, d: int(q.get(k, d))
        try:
            if parts[:3] == ["api", "network", "v1"]:
                rest = parts[3:]
                if rest == ["overview"]:
                    return self._send(200, r.overview())
                if rest == ["search"]:
                    return self._send(200, r.search(str(q.get("q", ""))[:300], q.get("kind"), q.get("domain"), q.get("dataset"), num("page", 0), num("limit", 25)))
                if rest == ["browse"]:
                    return self._send(200, r.browse(q.get("domain"), q.get("kind"), q.get("dataset"), q.get("cursor"), num("limit", 25),
                                                   str(q.get("boundary", "")).lower() in ("1", "true")))
                if len(rest) >= 2 and rest[0] == "node" and len(rest) <= 3:
                    nid = rest[1]
                    if len(rest) == 2:
                        d = r.node(nid)
                        return self._send(200, d) if d else self._send(404, {"error": "No such node."})
                    if rest[2] == "relationships":
                        return self._send(200, r.relationships(nid, q.get("direction", "out"), q.get("rel"), num("offset", 0), num("limit", 50)))
                    if rest[2] == "graph":
                        return self._send(200, r.graph(nid, num("limit", 40)))
            elif parts[:3] == ["api", "fgmpn", "v1"]:
                rest = parts[3:]
                if rest == ["about"]:
                    return self._send(200, r.fg_about())
                if rest == ["storage"]:
                    return self._send(200, r.fg_storage())
                if rest == ["resolve"]:
                    return self._send(200, r.fg_resolve(q.get("address", "")))
                if len(rest) == 2 and rest[0] == "node" and HEX32.match(rest[1]):
                    return self._send(200, r.fg_node(rest[1]))
            return self._send(404, {"error": "Unknown route."})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:                            # a bad store must not take the server down
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def _exit_with_parent() -> None:
    """The service that started this reader stops it when a network is closed and when it quits. If it is killed instead, this process
    would be left behind (it is in its own session): leave when the parent is gone."""
    ppid = os.getppid()

    def watch():
        while True:
            time.sleep(2)
            if os.getppid() != ppid:
                os._exit(0)
    threading.Thread(target=watch, daemon=True, name="parent-watch").start()


def serve(root: str | Path, host: str = "127.0.0.1", port: int = 8858, name: str | None = None) -> None:
    _exit_with_parent()
    reader = FgReader(Path(root), name)
    handler = type("H", (Handler,), {"reader": reader})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
