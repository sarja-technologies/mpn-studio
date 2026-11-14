"""A linked network or a fork of one, opened as a project.

Linked: a large MPN network that stays where it is (a pack folder on this computer, or another computer's network
API), opened read-only. The project file names the network and every place it can be read from ("sources"), and
MPN Studio uses the first one that answers. Nothing is copied.

Fork: a linked network plus a change file (overlay.py). Every read goes to the base and then through the fork's
changes; every edit goes only to the change file.

Every query is bounded and paged by the reader underneath: search uses each partition's full-text index, browsing
walks keys with a cursor, relationship lists are cut at the page size. Opening a network reads its overview only.
"""
from __future__ import annotations

import copy
import re
import threading
import time
from pathlib import Path

from ..mpnnet import schema as S
from ..mpnnet.service import Local, NetError, Remote
from .overlay import Overlay, OverlayError, new_node_id

HEX = re.compile(r"^[0-9a-f]{32}$")

# Networks with an official name. The pack and store identifiers are the ones written by the network's builder.
KNOWN = {
    "yaob-origin-n119m": {
        "name": "Yaob Origin N119M",
        "pack_ids": ["fylume-mpn", "mpn14b-live"],
        "description": "The 119-million-node Yaob Origin knowledge network: Wikidata, Wikidata lexemes and OpenAlex "
                       "records with their relationships, built by Fylume.",
    },
}


def known_for(pack_ids: list[str]) -> tuple[str, dict] | None:
    for key, k in KNOWN.items():
        if set(pack_ids) & set(k["pack_ids"]):
            return key, k
    return None


def describe_source(src: dict) -> str:
    if src.get("label"):
        return src["label"]
    return "Network address " + src["target"] if src["mode"] == "remote" else "Folder " + src["target"]


def probe_source(src: dict, timeout: float = 4.0) -> dict:
    """Is this source usable right now? Cheap: a folder check, or one overview request."""
    t = time.perf_counter()
    try:
        if src["mode"] == "local":
            p = Path(src["target"])
            ok = (p / "parts" / "p00.sqlite").exists() or (p / "p00.sqlite").exists()
            return {"available": ok, "ms": round((time.perf_counter() - t) * 1000, 1),
                    "reason": None if ok else ("The folder is missing or not mounted." if not p.exists() else "The folder has no installed network.")}
        import httpx
        r = httpx.get(src["target"].rstrip("/") + "/api/network/v1/overview", timeout=httpx.Timeout(timeout, connect=min(timeout, 3.0)))
        ok = r.status_code == 200 and r.json().get("api") == "mpn-network/1"
        return {"available": ok, "ms": round((time.perf_counter() - t) * 1000, 1), "reason": None if ok else f"Answered HTTP {r.status_code}."}
    except Exception as e:  # noqa: BLE001 - any failure means "not available", with the reason shown
        return {"available": False, "ms": round((time.perf_counter() - t) * 1000, 1), "reason": f"Not reachable ({type(e).__name__})."}


def identify(conn) -> dict:
    """The identity facts of an open source, from its overview."""
    ov = conn.call("overview", None, {})
    packs = [p.get("pack_id") for p in ov.get("packs") or [] if p.get("pack_id")]
    out = {"name": ov.get("name"), "version": ov.get("version"), "pack_ids": packs, "nodes": ov.get("validated_nodes"),
           "relationships": ov.get("relationships"), "storage_bytes": ov.get("storage_bytes"),
           "partitions": sum(p.get("partitions") or 0 for p in ov.get("packs") or []) or None}
    k = known_for(packs)
    if k:
        out["known"] = k[0]
    return out


class Workspace:
    """The open linked or forked network. Thread-safe for reads; edits are serialised by the overlay."""

    def __init__(self, slug: str, manifest: dict, project_dir: Path):
        self.slug, self.m, self.dir = slug, manifest, project_dir
        self.kind = manifest.get("kind")
        self.net = manifest.get("network") or {}
        self.conn: Local | Remote | None = None
        self.source_index: int | None = None
        self.source_status: list[dict] = []
        self.warning: str | None = None
        self._lock = threading.Lock()
        self.overlay: Overlay | None = None
        if self.kind == "fork":
            self.overlay = Overlay(project_dir / (manifest.get("overlay") or {}).get("file", "overlay.sqlite"))
        self.last_ms: float | None = None

    # ------------------------------------------------------------------ sources
    @property
    def sources(self) -> list[dict]:
        return self.net.get("sources") or []

    def connect(self, prefer: int | None = None) -> dict:
        """Open the preferred source, or the first that works. Records why each one was or was not used."""
        order = list(range(len(self.sources)))
        if prefer is not None and 0 <= prefer < len(order):
            order.remove(prefer)
            order.insert(0, prefer)
        elif self.net.get("preferred") is not None and 0 <= self.net["preferred"] < len(order):
            p = self.net["preferred"]
            order.remove(p)
            order.insert(0, p)
        status = [{"index": i, **{k: v for k, v in s.items() if k in ("mode", "target", "label")}, "label": describe_source(s),
                   "available": None, "reason": None} for i, s in enumerate(self.sources)]
        new, used, warn = None, None, None
        for i in order:
            s = self.sources[i]
            t = time.perf_counter()
            try:
                c = Remote(s["target"]) if s["mode"] == "remote" else Local(s["target"])
            except NetError as e:
                status[i].update(available=False, reason=str(e))
                continue
            try:
                ident = identify(c)
            except NetError as e:
                c.close()
                status[i].update(available=False, reason=str(e))
                continue
            want = set(self.net.get("identity", {}).get("pack_ids") or [])
            if want and not (want & set(ident["pack_ids"])):
                c.close()
                status[i].update(available=False, reason=f"This source holds a different network ({ident.get('name')}).")
                continue
            status[i].update(available=True, ms=round((time.perf_counter() - t) * 1000, 1), nodes=ident.get("nodes"))
            exp = self.net.get("identity", {}).get("nodes")
            if exp and ident.get("nodes") and abs(ident["nodes"] - exp) > 0:
                warn = (f"{describe_source(s)} holds {ident['nodes']:,} nodes; the network had {exp:,} when it was linked. "
                        "It may have changed since. Edits in a fork still apply by node id.")
            new, used = c, i
            break
        with self._lock:
            old, self.conn, self.source_index, self.source_status, self.warning = self.conn, new, used, status, warn
        if old is not None and old is not new:
            old.close()
        return self.state()

    def close(self) -> None:
        with self._lock:
            c, self.conn = self.conn, None
        if c:
            c.close()
        if self.overlay:
            self.overlay.close()

    def need(self):
        c = self.conn
        if c is None:
            raise NetError("None of this network's sources can be reached right now. "
                           + " ".join(f"{s['label']}: {(s.get('reason') or 'not tried').rstrip('.')}." for s in self.source_status), 503)
        return c

    def state(self) -> dict:
        src = self.sources[self.source_index] if self.source_index is not None else None
        out = {"kind": self.kind, "identity": self.net.get("identity"), "connected": self.conn is not None,
               "source": {"index": self.source_index, "mode": src["mode"], "label": describe_source(src), "target": src["target"]} if src else None,
               "sources": self.source_status, "warning": self.warning, "read_only": self.kind != "fork"}
        if self.overlay:
            out["changes"] = self.overlay.stats()
        return out

    # ------------------------------------------------------------------ base reads
    def _base(self, op: str, nid: str | None = None, params: dict | None = None):
        t = time.perf_counter()
        try:
            return self.need().call(op, nid, params or {})
        finally:
            self.last_ms = round((time.perf_counter() - t) * 1000, 1)

    def _base_node(self, nid: str) -> dict | None:
        try:
            return self._base("node", nid)
        except NetError as e:
            if e.status == 404:
                return None
            raise

    # ------------------------------------------------------------------ merged reads
    def overview(self) -> dict:
        ov = dict(self._base("overview"))
        known = self.net.get("identity", {}).get("known")
        if known in KNOWN:
            ov["name"] = KNOWN[known]["name"]
            ov["description"] = KNOWN[known]["description"]
        else:
            ov["name"] = self.m.get("name") or ov.get("name")
        if self.overlay:
            st = self.overlay.stats()
            ov["changes"] = st
            ov["base_nodes"] = ov.get("validated_nodes")
            ov["validated_nodes"] = (ov.get("validated_nodes") or 0) + st["nodes_added"] - st["nodes_deleted"]
        ov["last_query_ms"] = self.last_ms
        return ov

    def _apply_card(self, card: dict, states: dict) -> dict | None:
        st = states.get(card["id"])
        if not st:
            return card
        if st[0] == "del":
            return None
        if st[0] == "put" and st[1]:
            return {**card, **_card(st[1]), "edited": True}
        return card

    def search(self, q: str, params: dict) -> dict:
        base = self._base("search", None, {**params, "q": q})
        if not self.overlay:
            return base
        res = base.get("results") or []
        states = self.overlay.node_states([r["id"] for r in res])
        merged = [c for c in (self._apply_card(r, states) for r in res) if c]
        if int(params.get("page") or 0) == 0 and base.get("mode") != "id":
            dom, kind = params.get("domain"), params.get("kind")
            own = [_card(d) | {"edited": True} for d in self.overlay.search(q, 50)
                   if (not dom or d.get("domain") == dom) and (not kind or d.get("kind") == kind)]
            seen = {c["id"] for c in own}
            merged = own + [c for c in merged if c["id"] not in seen]
        return {**base, "results": merged}

    def browse(self, params: dict) -> dict:
        base = self._base("browse", None, params)
        if not self.overlay:
            return base
        res = base.get("results") or []
        states = self.overlay.node_states([r["id"] for r in res])
        merged = [c for c in (self._apply_card(r, states) for r in res) if c]
        if not params.get("cursor"):
            merged = [_card(d) | {"edited": True} for d in self.overlay.added_nodes(params.get("domain"), 50)] + merged
        return {**base, "results": merged}

    def _merge_rels(self, nid: str, out: list[dict], inn: list[dict]) -> tuple[list[dict], list[dict]]:
        ov = self.overlay
        changes = ov.edges_of(nid)
        removed = {(e["src"], e["rel"], e["dst"]) for e in changes if e["state"] == "del"}
        out = [r for r in out if (nid, r.get("rel_id") or r["rel"], r["node"]) not in removed]
        inn = [r for r in inn if (r["node"], r.get("rel_id") or r["rel"], nid) not in removed]
        added = [e for e in changes if e["state"] == "add"]
        others = {e["dst"] if e["src"] == nid else e["src"] for e in added}
        states = ov.node_states(list({r["node"] for r in out + inn} | others))
        info = {}
        for o in others:
            st = states.get(o)
            if st and st[0] == "put" and st[1]:
                info[o] = st[1]
            elif not st:
                b = self._base_node(o)
                info[o] = b["node"] if b else None
        mine = self._node_doc_domain(nid)
        for e in added:
            other = e["dst"] if e["src"] == nid else e["src"]
            d = info.get(other)
            if other in states and states[other][0] == "del":
                continue
            entry = {"rel": e["label"] or e["rel"].replace("_", " "), "rel_id": e["rel"], "node": other, "via": None,
                     "title": d.get("title") if d else None, "domain": d.get("domain") if d else None, "kind": d.get("kind") if d else None,
                     "cross_domain": bool(d and mine and d.get("domain") != mine), "present": d is not None, "added": True}
            (out if e["src"] == nid else inn).insert(0, entry)

        def fix(rows):
            keep = []
            for r in rows:
                st = states.get(r["node"])
                if st and st[0] == "del":
                    continue
                if st and st[0] == "put" and st[1]:
                    r = {**r, "title": st[1].get("title"), "domain": st[1].get("domain"), "kind": st[1].get("kind")}
                keep.append(r)
            return keep
        return fix(out), fix(inn)

    def _node_doc_domain(self, nid: str) -> str | None:
        st, doc = self.overlay.node_state(nid)
        return doc.get("domain") if doc else None

    def resolve(self, nid: str) -> str:
        """A Wikidata id, DOI or other key becomes the node id; a node id stays itself."""
        if HEX.match(nid):
            return nid
        b = self._base_node(nid)
        if not b:
            raise NetError("No such node.", 404)
        return b["node"]["id"]

    def node(self, nid: str) -> dict:
        if not self.overlay:
            d = self._base_node(nid)
            if not d:
                raise NetError("No such node.", 404)
            return d
        nid = self.resolve(nid)
        st, doc = self.overlay.node_state(nid)
        if st == "del":
            raise NetError("This node was deleted in this fork. Undo the deletion to bring it back.", 410)
        base = None if (doc and doc.get("created_in_fork")) else self._base_node(nid)
        if st == "base" and not base:
            raise NetError("No such node.", 404)
        if st == "put":
            node = {**(base["node"] if base else {}), **doc, "edited": True}
        else:
            node = base["node"]
        out_r, in_r = self._merge_rels(nid, (base or {}).get("out", []), (base or {}).get("in", []))
        return {**(base or {"claims": [], "more_claims": False, "more_out": False, "more_in": False}), "node": node, "out": out_r, "in": in_r,
                "cross_domain": sorted({r["domain"] for r in out_r + in_r if r.get("cross_domain") and r.get("domain")})}

    def relationships(self, nid: str, params: dict) -> dict:
        base = self._base("relationships", nid, params) if not (self.overlay and self._created(nid)) else {"results": [], "more": False}
        if not self.overlay:
            return base
        direction = params.get("direction", "out")
        rows = base.get("results") or []
        if int(params.get("offset") or 0) == 0:
            o, i = self._merge_rels(nid, rows if direction == "out" else [], rows if direction == "in" else [])
            rows = o if direction == "out" else i
        return {**base, "results": rows}

    def _created(self, nid: str) -> bool:
        st, doc = self.overlay.node_state(nid)
        return bool(doc and doc.get("created_in_fork"))

    def graph(self, nid: str, limit: int = 40) -> dict:
        if not self.overlay:
            return self._base("graph", nid, {"limit": limit})
        nid = self.resolve(nid)
        st, doc = self.overlay.node_state(nid)
        if st == "del":
            raise NetError("This node was deleted in this fork.", 410)
        g = {"center": nid, "nodes": [], "edges": []} if (doc and doc.get("created_in_fork")) else self._base("graph", nid, {"limit": limit})
        nodes = {n["id"]: n for n in g.get("nodes") or []}
        if doc:
            nodes[nid] = {"id": nid, "title": doc.get("title"), "domain": doc.get("domain"), "kind": doc.get("kind"), "center": True, "edited": True}
        states = self.overlay.node_states(list(nodes))
        for k, (s2, d2) in states.items():
            if s2 == "del" and k != nid:
                nodes.pop(k, None)
            elif s2 == "put" and d2 and k in nodes:
                nodes[k] = {**nodes[k], "title": d2.get("title"), "domain": d2.get("domain"), "kind": d2.get("kind"), "edited": True}
        changes = self.overlay.edges_of(nid)
        removed = {(e["src"], e["rel"], e["dst"]) for e in changes if e["state"] == "del"}
        edges = [e for e in g.get("edges") or [] if e["src"] in nodes and e["dst"] in nodes]
        # base graph edges carry labels, not ids: drop a removed pair by endpoints and label or id
        edges = [e for e in edges if not any(e["src"] == s and e["dst"] == d and (e["rel"] == r or e["rel"] == r.replace("_", " "))
                                             for s, r, d in removed)]
        for e in changes:
            if e["state"] != "add":
                continue
            other = e["dst"] if e["src"] == nid else e["src"]
            if other not in nodes:
                s3, d3 = self.overlay.node_state(other)
                if s3 == "del":
                    continue
                if not d3:
                    b = self._base_node(other)
                    d3 = b["node"] if b else None
                if not d3:
                    continue
                nodes[other] = {"id": other, "title": d3.get("title"), "domain": d3.get("domain"), "kind": d3.get("kind"), "edited": s3 == "put"}
            edges.append({"src": e["src"], "dst": e["dst"], "rel": e["label"] or e["rel"], "cross_domain": nodes[e["src"]].get("domain") != nodes[e["dst"]].get("domain"),
                          "added": True})
        return {"center": nid, "nodes": list(nodes.values()), "edges": edges}

    # ------------------------------------------------------------------ edits (forks only)
    def _need_fork(self) -> Overlay:
        if not self.overlay:
            raise OverlayError("This network is protected: it is opened read-only. Duplicate it to make an editable fork.", 409)
        return self.overlay

    def _current_doc(self, nid: str) -> dict:
        ov = self._need_fork()
        st, doc = ov.node_state(nid)
        if st == "del":
            raise OverlayError("This node was deleted in this fork.", 410)
        if doc:
            return copy.deepcopy(doc)
        b = self._base_node(nid)
        if not b:
            raise OverlayError("No such node.", 404)
        n = copy.deepcopy(b["node"])
        n.pop("edited", None)
        return n

    def create_node(self, fields: dict) -> dict:
        ov = self._need_fork()
        title = (fields.get("title") or "").strip()
        if not title:
            raise OverlayError("Give the node a title.")
        nid = new_node_id()
        doc = {"id": nid, "title": title, "kind": "concept", "domain": "general", "subdomain": None, "summary": None, "aliases": [],
               "dataset": "fork", "licence": None, "status": "edited", "conf": None, "pack": None,
               "cells": {"knowledge": {"statements": []}, "thinking": None, "reasoning": None},
               "provenance": {"dataset": "fork", "note": f"Added in {self.m.get('name')}"}, "created_in_fork": True,
               "created": int(time.time())}
        _apply_fields(doc, fields)
        ov.edit(f"Add node “{title}”", nodes=[(nid, "put", doc, True)])
        return self.node(nid)

    def update_node(self, nid: str, fields: dict) -> dict:
        ov = self._need_fork()
        nid = self.resolve(nid)
        doc = self._current_doc(nid)
        before = copy.deepcopy(doc)
        _apply_fields(doc, fields)
        if doc == before:
            return self.node(nid)
        doc["status"] = "edited"
        ov.edit(f"Edit “{doc.get('title')}”", nodes=[(nid, "put", doc, bool(doc.get("created_in_fork")))])
        return self.node(nid)

    def delete_nodes(self, nids: list[str]) -> dict:
        ov = self._need_fork()
        if not nids:
            raise OverlayError("Choose the nodes to delete.")
        if len(nids) > 10000:
            raise OverlayError("Delete at most 10,000 nodes at a time.")
        ops, titles = [], []
        for nid in nids:
            nid = self.resolve(nid)
            doc = self._current_doc(nid)
            titles.append(doc.get("title"))
            ops.append((nid, "del", None, bool(doc.get("created_in_fork"))))
        msg = f"Delete “{titles[0]}”" if len(ops) == 1 else f"Delete {len(ops):,} nodes"
        rev = ov.edit(msg, nodes=ops)
        return {"deleted": len(ops), "revision": rev}

    def duplicate_node(self, nid: str, title: str | None = None) -> dict:
        ov = self._need_fork()
        nid = self.resolve(nid)
        full = self.node(nid)
        doc = self._current_doc(nid)
        new = new_node_id()
        doc.update({"id": new, "title": (title or "").strip() or f"{doc.get('title')} (copy)", "created_in_fork": True, "status": "edited",
                    "duplicated_from": nid, "key": None, "created": int(time.time())})
        doc.pop("edited", None)
        doc.pop("ids", None)          # external identifiers (Wikidata, VIAF, ...) name the original, not the copy
        edges = [(new, r.get("rel_id") or r["rel"], r["node"], "add", r["rel"]) for r in full.get("out", [])[:100] if r.get("present", True)]
        ov.edit(f"Duplicate “{full['node'].get('title')}”", nodes=[(new, "put", doc, True)], edges=edges)
        return self.node(new)

    def add_edge(self, src: str, dst: str, rel: str) -> dict:
        ov = self._need_fork()
        label = (rel or "").strip() or "related to"
        if len(label) > 80:
            raise OverlayError("Keep the relationship name under 80 characters.")
        src, dst = self.resolve(src), self.resolve(dst)
        if src == dst:
            raise OverlayError("A node cannot be connected to itself.")
        a, b = self._current_doc(src), self._current_doc(dst)
        rid = re.sub(r"\s+", "_", label.lower())
        ov.edit(f"Connect “{a.get('title')}” → “{b.get('title')}”", edges=[(src, rid, dst, "add", label)])
        return self.node(src)

    def remove_edge(self, src: str, rel: str, dst: str) -> dict:
        ov = self._need_fork()
        a = self._current_doc(src)
        ov.edit(f"Disconnect “{a.get('title')}” ({rel.replace('_', ' ')})", edges=[(src, rel, dst, "del", None)])
        return self.node(src)


def _card(d: dict) -> dict:
    return {"id": d.get("id"), "title": d.get("title"), "kind": d.get("kind"), "domain": d.get("domain"), "subdomain": d.get("subdomain"),
            "summary": (d.get("summary") or "")[:300] or None, "dataset": d.get("dataset"), "licence": d.get("licence"),
            "status": d.get("status"), "conf": d.get("conf"), "pack": d.get("pack")}


def _apply_fields(doc: dict, f: dict) -> None:
    if "title" in f:
        t = (f["title"] or "").strip()
        if not t:
            raise OverlayError("A node needs a title.")
        doc["title"] = t[:500]
    if "summary" in f:
        doc["summary"] = (f["summary"] or "").strip()[:4000] or None
    if "aliases" in f:
        al = f["aliases"]
        if isinstance(al, str):
            al = [a.strip() for a in al.split(",")]
        doc["aliases"] = [a for a in (str(x).strip() for x in al or []) if a][:100]
    if "domain" in f:
        if f["domain"] not in S.DOMAIN_ID:
            raise OverlayError(f"Unknown domain {f['domain']!r}.")
        doc["domain"] = f["domain"]
    if "kind" in f:
        if f["kind"] not in S.KIND_ID or f["kind"] == "claim":
            raise OverlayError(f"Unknown kind {f['kind']!r}.")
        doc["kind"] = f["kind"]
    if "notes" in f:
        doc["notes"] = (f["notes"] or "").strip()[:20000] or None
    if "statements" in f:
        st = [s.strip() for s in (f["statements"] or []) if isinstance(s, str) and s.strip()][:200]
        cells = doc.setdefault("cells", {}) or {}
        doc["cells"] = cells
        k = cells.get("knowledge") or {}
        k["statements"] = [{"text": s} for s in st]
        cells["knowledge"] = k
