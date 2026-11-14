"""One open MPN network per MPN Studio window: remote (an MPN dashboard's /api/network/v1 over HTTP) or local (a pack
folder on this computer, read with the vendored reader). Every query is bounded and paged by the reader; nothing here
loads the network into memory or copies it."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

API = "mpn-network/1"
OPS = {"overview", "search", "browse"}
PARAMS = {"q", "kind", "domain", "dataset", "page", "limit", "cursor", "boundary", "direction", "rel", "offset"}


class NetError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _parts_dir(root: Path) -> Path:
    if (root / "parts" / "p00.sqlite").exists():
        return root / "parts"
    if (root / "p00.sqlite").exists():
        return root
    if (root / "parts" / "p00.sqlite.zst").exists():
        raise NetError("This folder holds a downloaded pack that is still compressed. Install it first (Install pack), then open the installed folder.")
    raise NetError("No MPN network here: the folder needs parts/p00.sqlite … (an installed pack) or p00.sqlite … (a store).")


def _raise_fd_limit(want: int = 65536) -> None:
    """Each worker thread keeps one SQLite handle per partition (plus WAL and shared-memory files when present): the
    usual default of 1024 descriptors runs out after a few dozen threads on a 64-partition pack."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        cap = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if soft < cap:
            resource.setrlimit(resource.RLIMIT_NOFILE, (cap, hard))
    except (ImportError, ValueError, OSError):
        pass


class Local:
    mode = "local"

    def __init__(self, path: str):
        _raise_fd_limit()
        from .netapi import NetworkReader
        from .pack import Federation
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise NetError(f"{root} is not a folder.")
        parts = _parts_dir(root)
        n = len(list(parts.glob("p[0-9][0-9].sqlite")))
        if not (parts / f"p{n - 1:02d}.sqlite").exists():
            raise NetError(f"{parts} is missing partitions: found {n} files but not a continuous p00 … p{n - 1:02d} series.")
        m = {}
        if (root / "manifest.json").exists():
            try:
                m = json.loads((root / "manifest.json").read_text())
            except ValueError:
                raise NetError("manifest.json in this folder is not valid JSON.")
            if m.get("partitions") and m["partitions"] != n:
                raise NetError(f"The manifest lists {m['partitions']} partitions but {n} are installed. Finish installing the pack first.")
        ds = {str(d["id"]): {"slug": d["slug"], "name": d["name"], "licence": d.get("licence"), "attribution": d.get("attribution")}
              for d in m.get("datasets") or [] if "id" in d}
        info = {"name": m.get("name") or root.name, "version": m.get("version"), "location": f"local ({root})", "datasets": ds,
                "target_nodes": None, "construction": None,
                "last_validation": {"verified_at": m.get("created"), "ok": True, "note": "pack manifest"} if m else None}
        fed = Federation([{"pack_id": m.get("pack_id") or root.name, "version": m.get("version"), "parts": str(parts), "partitions": n}],
                         cache_kib=1024)
        if not fed.packs:
            raise NetError("The partitions could not be opened read-only.", 500)
        # search the partitions in parallel with a small fixed pool: each worker keeps one read-only handle per partition
        # with a 1 MiB page cache (at most 6 x 64 MiB), so memory stays bounded however large the network is
        import os as _os
        from concurrent.futures import ThreadPoolExecutor
        self.pool = ThreadPoolExecutor(max_workers=max(2, min(6, (_os.cpu_count() or 4) // 2)), thread_name_prefix="mpn-search")
        for _p, st in fed.packs:
            st.pool = self.pool
        self.reader = NetworkReader(fed, info)
        self.target, self.name = str(root), info["name"]
        self.lock = threading.Lock()

    def call(self, op: str, nid: str | None, params: dict):
        r = self.reader
        p = {k: v for k, v in params.items() if v not in (None, "")}
        num = lambda k, d: int(p.get(k, d))
        try:
            if op == "overview":
                return r.overview()
            if op == "search":
                return r.search(str(p.get("q", ""))[:300], p.get("kind"), p.get("domain"), p.get("dataset"), num("page", 0), num("limit", 25))
            if op == "browse":
                return r.browse(p.get("domain"), p.get("kind"), p.get("dataset"), p.get("cursor"), num("limit", 25),
                                str(p.get("boundary", "")).lower() in ("1", "true"))
            if op == "node":
                d = r.node(nid)
                if not d:
                    raise NetError("No such node.", 404)
                return d
            if op == "relationships":
                return r.relationships(nid, p.get("direction", "out"), p.get("rel"), num("offset", 0), num("limit", 50))
            if op == "graph":
                return r.graph(nid, num("limit", 40))
        except ValueError as e:
            raise NetError(str(e))
        raise NetError("Unknown query.", 404)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        for _p, st in self.reader.fed.packs:
            st.close()


class Remote:
    mode = "remote"

    def __init__(self, url: str):
        u = urlparse(url.strip())
        if u.scheme not in ("http", "https") or not u.hostname:
            raise NetError("Give the dashboard address, for example http://mpn-host.local:8844")
        self.base = f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"
        if self.base.endswith("/api/network/v1"):
            self.base = self.base[: -len("/api/network/v1")]
        self.client = httpx.Client(timeout=httpx.Timeout(60.0, connect=4.0), follow_redirects=False)
        try:
            ov = self.call("overview", None, {})
        except NetError:
            self.client.close()
            raise
        if ov.get("api") != API:
            self.client.close()
            raise NetError(f"{self.base} answered, but not with the MPN network API ({API}).")
        self.target, self.name = self.base, ov.get("name") or self.base

    def call(self, op: str, nid: str | None, params: dict):
        path = f"/api/network/v1/{op}" if op in OPS else f"/api/network/v1/node/{nid}" + ("" if op == "node" else f"/{op}")
        q = {k: v for k, v in params.items() if k in PARAMS and v not in (None, "")}
        try:
            r = self.client.get(self.base + path, params=q)
        except httpx.HTTPError as e:
            raise NetError(f"The network at {self.base} did not answer ({type(e).__name__}). Check that the computer is on and reachable.", 502)
        if r.status_code == 404:
            raise NetError("No such node." if op not in OPS else f"{self.base} has no MPN network API.", 404)
        if r.status_code != 200:
            raise NetError(f"{self.base} answered HTTP {r.status_code}: {r.text[:200]}", 502)
        try:
            return r.json()
        except ValueError:
            raise NetError(f"{self.base} did not answer with JSON.", 502)

    def close(self):
        self.client.close()


class Service:
    def __init__(self):
        self.conn: Local | Remote | None = None
        self.opened = 0.0
        self._lock = threading.Lock()

    def open(self, mode: str, target: str):
        new = Remote(target) if mode == "remote" else Local(target) if mode == "local" else None
        if new is None:
            raise NetError("mode must be remote or local")
        with self._lock:
            old, self.conn, self.opened = self.conn, new, time.time()
        if old:
            old.close()
        return self.state()

    def close(self):
        with self._lock:
            old, self.conn = self.conn, None
        if old:
            old.close()

    def state(self):
        c = self.conn
        return {"open": bool(c), "mode": c.mode if c else None, "target": c.target if c else None, "name": c.name if c else None,
                "opened": self.opened if c else None}

    def call(self, op: str, nid: str | None = None, params: dict | None = None):
        c = self.conn
        if c is None:
            raise NetError("No MPN network is open.", 409)
        if nid is not None and (len(nid) > 80 or "/" in nid):
            raise NetError("Not a node id.")
        t = time.perf_counter()
        out = c.call(op, nid, params or {})
        if isinstance(out, dict):
            out = {**out, "client_ms": round((time.perf_counter() - t) * 1000, 1)}
        return out
