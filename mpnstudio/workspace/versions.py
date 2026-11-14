"""Named versions and comparisons for projects with their own store (kind "network").

A version is a name for a store revision (the store's `releases` table, which bundles already use to pin a
revision). Nothing is copied when a version is saved: the store keeps every revision. Comparing two versions reads the
interval-versioned rows directly. "Open as a new project" copies the store and cuts its history at the version, so
the new project starts exactly in that state and the original is never touched.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..store.db import Store, cell_text

VERSIONED = ("mpn_v", "domain_v", "node_v", "member_v", "edge_v", "node_source_v")


class VersionError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def list_versions(store: Store) -> list[dict]:
    out = []
    for r in store.q("SELECT name, rev, created, manifest FROM releases ORDER BY rev DESC, created DESC"):
        m = json.loads(r["manifest"] or "{}")
        out.append({"name": r["name"], "rev": r["rev"], "created": r["created"], "note": m.get("note", ""), "kind": m.get("kind", "version")})
    return out


def save_version(store: Store, name: str, note: str = "") -> dict:
    name = (name or "").strip()[:120]
    if not name:
        raise VersionError("Give the version a name.")
    if store.q1("SELECT 1 FROM releases WHERE name=?", (name,)):
        raise VersionError(f"A version called “{name}” already exists.", 409)
    head = store.head()
    c = store.conn()
    with store._wlock:
        c.execute("INSERT INTO releases(name, rev, created, manifest) VALUES(?,?,?,?)",
                  (name, head, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), json.dumps({"kind": "version", "note": note})))
    return {"name": name, "rev": head}


def version_rev(store: Store, name: str | None) -> int:
    if name in (None, "", "current"):
        return store.head()
    r = store.q1("SELECT rev FROM releases WHERE name=?", (name,))
    if not r:
        raise VersionError(f"No version called “{name}”.", 404)
    return r[0]


def _node_at(store: Store, node_id: int, rev: int) -> dict | None:
    r = store.q1("SELECT title, summary, aliases, status, cells, type FROM node_v WHERE node_id=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?)",
                 (node_id, rev, rev))
    return dict(r) if r else None


def _edge_at(store: Store, edge_id: int, rev: int) -> dict | None:
    r = store.q1("SELECT src, dst, type FROM edge_v WHERE edge_id=? AND rev_from<=? AND (rev_to IS NULL OR rev_to>?)", (edge_id, rev, rev))
    return dict(r) if r else None


def _changed(store: Store, table: str, key: str, lo: int, hi: int, limit: int) -> list[int]:
    rows = store.q(f"SELECT DISTINCT {key} FROM {table} WHERE (rev_from>? AND rev_from<=?) OR (rev_to>? AND rev_to<=?) LIMIT ?",
                   (lo, hi, lo, hi, limit + 1))
    return [r[0] for r in rows]


def compare(store: Store, ra: int, rb: int, limit: int = 2000) -> dict:
    """What differs between revision ra (before) and rb (after): nodes added, deleted or edited (with the fields that
    changed), relationships added or removed, and how many memberships changed."""
    lo, hi = min(ra, rb), max(ra, rb)
    nodes, truncated = [], False
    ids = _changed(store, "node_v", "node_id", lo, hi, limit)
    truncated |= len(ids) > limit
    for nid in ids[:limit]:
        a, b = _node_at(store, nid, ra), _node_at(store, nid, rb)
        live_a = bool(a and a["status"] == "live")
        live_b = bool(b and b["status"] == "live")
        if not live_a and not live_b:
            continue
        if live_a and not live_b:
            ch = "deleted"
        elif live_b and not live_a:
            ch = "added"
        else:
            fields = [f for f in ("title", "summary", "aliases", "type") if a[f] != b[f]]
            if a["cells"] != b["cells"]:
                fields.append("cells")
            if not fields:
                continue
            ch = "edited"
        e = {"id": str(nid), "change": ch, "title": (b or a)["title"]}
        if ch == "edited":
            e["fields"] = fields
            e["before"] = {"title": a["title"], "summary": a["summary"], "text": cell_text(json.loads(a["cells"]))[:600]}
            e["after"] = {"title": b["title"], "summary": b["summary"], "text": cell_text(json.loads(b["cells"]))[:600]}
        nodes.append(e)
    edges = []
    eids = _changed(store, "edge_v", "edge_id", lo, hi, limit)
    truncated |= len(eids) > limit
    titles: dict[int, str] = {}

    def title(n: int) -> str:
        if n not in titles:
            r = store.q1("SELECT title FROM node_v WHERE node_id=? ORDER BY rev_from DESC LIMIT 1", (n,))
            titles[n] = r[0] if r else str(n)
        return titles[n]
    for eid in eids[:limit]:
        a, b = _edge_at(store, eid, ra), _edge_at(store, eid, rb)
        if bool(a) == bool(b):
            continue
        e = b or a
        edges.append({"change": "added" if b else "removed", "src": str(e["src"]), "dst": str(e["dst"]), "type": e["type"],
                      "src_title": title(e["src"]), "dst_title": title(e["dst"])})
    mem = len(_changed(store, "member_v", "vid", lo, hi, 100000))
    return {"from": ra, "to": rb, "nodes": nodes, "relationships": edges, "membership_rows_changed": mem, "truncated": truncated}


def cut_history(db: Path, rev: int) -> dict:
    """In a COPY of a store: make revision `rev` the head (drop everything after it), then rebuild the head-only indexes.
    Never call this on a project's own store."""
    c = sqlite3.connect(db, isolation_level=None)
    try:
        head = c.execute("SELECT MAX(rev) FROM revisions").fetchone()[0] or 0
        if rev > head or rev < 0:
            raise VersionError("That revision does not exist in this project.")
        c.execute("BEGIN IMMEDIATE")
        for t in VERSIONED:
            c.execute(f"DELETE FROM {t} WHERE rev_from>?", (rev,))
            c.execute(f"UPDATE {t} SET rev_to=NULL WHERE rev_to>?", (rev,))
        c.execute("DELETE FROM revisions WHERE rev>?", (rev,))
        c.execute("DELETE FROM releases WHERE rev>?", (rev,))
        c.execute("COMMIT")
    finally:
        c.close()
    st = Store(db)
    try:
        cc = st.conn()
        with st._wlock:
            cc.execute("BEGIN IMMEDIATE")
            try:
                cc.execute("DELETE FROM node_fts")
                for r in cc.execute("SELECT * FROM node_v WHERE rev_to IS NULL").fetchall():
                    srcs = cc.execute("SELECT s.title, s.kind FROM node_source_v ns JOIN sources s ON s.id=ns.source_id "
                                      "WHERE ns.node_id=? AND ns.rev_to IS NULL", (r["node_id"],)).fetchall()
                    cc.execute("INSERT INTO node_fts(rowid, title, aliases, summary, body, sources) VALUES(?,?,?,?,?,?)",
                               (r["node_id"], r["title"], " ; ".join(json.loads(r["aliases"])), r["summary"], cell_text(json.loads(r["cells"])),
                                " ; ".join(f"{s['kind']} {s['title']}" for s in srcs)))
                cc.execute("COMMIT")
            except BaseException:
                cc.execute("ROLLBACK")
                raise
        st.rebuild_summaries()
        from ..store import layout
        exist = {r[0] for r in st.q("SELECT node_id FROM node_v WHERE rev_to IS NULL")}
        live = {r[0] for r in st.q("SELECT node_id FROM node_v WHERE rev_to IS NULL AND status='live'")}
        placed = {r[0] for r in st.q("SELECT node_id FROM layout WHERE board_id=1")}
        for n in placed - exist:          # nodes created after the version
            layout.remove_node(st, 1, n)
        for n in live - placed:           # nodes that were removed from the board after the version
            layout.place_node(st, 1, n)
        layout.ensure_tiles(st, 1)
        st.checkpoint_wal()
        return {"head": st.head(), "nodes": st.counts().get("canonical_live_nodes")}
    finally:
        st.close()
