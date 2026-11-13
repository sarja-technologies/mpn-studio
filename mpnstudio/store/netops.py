"""Operations on whole node networks and on large node selections.

* select_ids      every live node matching a filter (not limited to a page or to what the board shows)
* bulk_delete     delete any number of nodes as ONE revision (one undo restores all of them), with progress and safe
                  cancellation (the transaction is rolled back; nothing is half-deleted). Live relations that touch a
                  deleted node are closed in the same revision, so no dangling edge is left behind.
* extract         a new, independent network holding the current state of selected nodes (with their MPNs, domains,
                  sources, the relations among them, layout and indexes). Node ids, uids and provenance are kept.
* merge_into      copy nodes of another network into this one (new ids here, provenance recorded), skipping or
                  reporting conflicts
* compare         what differs between two networks
* stats           real figures read from the file

A network is one SQLite store file (see store/schema.py).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterable

from .db import Store, content_hash, now, sid


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------------------------- selection
def select_ids(store: Store, filt: dict | None = None, rev: int | None = None) -> list[int]:
    """Node ids (head state) matching `filt`. Keys, all optional and combined with AND:
    mpn (id or list of ids), domain (id), type, validation, origin, status (default: live and disabled, never tombstones),
    q (full-text query over title, aliases, summary, body and sources), title_prefix."""
    f = dict(filt or {})
    where, args = ["n.rev_to IS NULL"], []
    status = f.get("status")
    if status:
        where.append("n.status=?")
        args.append(status)
    else:
        where.append("n.status<>'tombstone'")
    for k in ("type", "validation", "origin"):
        if f.get(k):
            v = f[k] if isinstance(f[k], list) else [f[k]]
            where.append(f"n.{k} IN ({','.join('?' * len(v))})")
            args += v
    if f.get("title_prefix"):
        where.append("n.title LIKE ? ESCAPE '\\'")
        args.append(f["title_prefix"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    mpns = f.get("mpn")
    if mpns:
        mpns = [int(x) for x in (mpns if isinstance(mpns, list) else [mpns])]
        where.append(f"EXISTS(SELECT 1 FROM member_v m WHERE m.node_id=n.node_id AND m.rev_to IS NULL AND m.mpn_id IN ({','.join('?' * len(mpns))}))")
        args += mpns
    if f.get("domain"):
        where.append("EXISTS(SELECT 1 FROM member_v m WHERE m.node_id=n.node_id AND m.rev_to IS NULL AND m.domain_id=?)")
        args.append(int(f["domain"]))
    if f.get("q"):
        fq = Store.fts_query(str(f["q"]))
        if fq:
            where.append("n.node_id IN (SELECT rowid FROM node_fts WHERE node_fts MATCH ?)")
            args.append(fq)
    rows = store.q(f"SELECT n.node_id FROM node_v n WHERE {' AND '.join(where)} ORDER BY n.node_id", args)
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------------------------- bulk delete
def bulk_delete(store: Store, ids: Iterable[int], actor: str = "local", progress: Callable[[int, int], None] | None = None,
                cancelled: Callable[[], bool] | None = None, close_edges: bool = True) -> dict:
    """Tombstone every node in `ids` in one revision. Returns {rev, deleted, already, missing, edges_closed}.
    Raises Cancelled (and rolls back) if `cancelled()` becomes true."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    total = len(ids)
    out = {"requested": total, "deleted": 0, "already": 0, "missing": 0, "edges_closed": 0, "rev": store.head()}
    if not ids:
        return out
    msg = f"delete {total} nodes" if total != 1 else "delete 1 node"
    t0 = time.perf_counter()
    with store.edit(actor=actor, message=msg) as tx:
        c = tx.c
        live = set()
        for i in range(0, total, 900):
            chunk = ids[i:i + 900]
            for r in c.execute(f"SELECT node_id, status FROM node_v WHERE rev_to IS NULL AND node_id IN ({','.join('?' * len(chunk))})", chunk):
                if r[1] == "tombstone":
                    out["already"] += 1
                else:
                    live.add(r[0])
        out["missing"] = total - out["already"] - len(live)
        done = 0
        for nid in ids:
            if nid in live:
                tx.delete_node(nid)
                out["deleted"] += 1
            done += 1
            if done % 250 == 0 or done == total:
                if cancelled and cancelled():
                    raise Cancelled()
                if progress:
                    progress(done, total)
        if close_edges and live:
            lv = list(live)
            edge_ids = set()
            for i in range(0, len(lv), 450):
                chunk = lv[i:i + 450]
                ph = ",".join("?" * len(chunk))
                edge_ids.update(r[0] for r in c.execute(
                    f"SELECT edge_id FROM edge_v WHERE rev_to IS NULL AND (src IN ({ph}) OR dst IN ({ph}))", chunk + chunk))
            for eid in edge_ids:
                tx.remove_edge(eid)
            out["edges_closed"] = len(edge_ids)
        rev = tx.rev
    out["rev"] = rev if out["deleted"] else store.head()
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out


# ---------------------------------------------------------------------------------------------- extract (specialise / copy)
_COPY_HEAD = {
    # table: (columns, where clause selecting rows of the chosen nodes; ? = the node-id temp table)
    "node_v": ["node_id", "uid", "type", "title", "summary", "aliases", "schema_id", "schema_version", "status", "validation", "origin",
               "cells", "content_hash"],
    "member_v": ["node_id", "mpn_id", "domain_id", "role", "evidence"],
    "node_source_v": ["node_id", "source_id", "locator"],
}


def extract(src: str | Path, dst: str | Path, ids: Iterable[int] | None = None, message: str = "") -> dict:
    """Write a new network at `dst` containing the head state of `ids` (all live nodes if None). The new store starts
    with one import revision; it shares nothing with the source file."""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        raise FileExistsError(dst)
    Store(dst).close()                          # create the schema
    c = sqlite3.connect(dst, isolation_level=None)
    try:
        c.execute("ATTACH DATABASE ? AS s", (str(src),))      # read only: nothing below writes to s
        c.execute("BEGIN IMMEDIATE")
        c.execute("CREATE TEMP TABLE sel(node_id INTEGER PRIMARY KEY)")
        if ids is None:
            c.execute("INSERT INTO sel SELECT node_id FROM s.node_v WHERE rev_to IS NULL AND status<>'tombstone'")
        else:
            c.executemany("INSERT OR IGNORE INTO sel VALUES(?)", [(int(i),) for i in ids])
            c.execute("DELETE FROM sel WHERE node_id NOT IN (SELECT node_id FROM s.node_v WHERE rev_to IS NULL AND status<>'tombstone')")
        n_sel = c.execute("SELECT COUNT(*) FROM sel").fetchone()[0]
        c.execute("INSERT INTO revisions(rev, parent, created, actor, kind, message) VALUES(1, 0, ?, 'system', 'import', ?)",
                  (now(), message or f"extracted {n_sel} nodes"))
        for t in ("cell_schemas", "sources", "settings"):
            c.execute(f"INSERT OR REPLACE INTO {t} SELECT * FROM s.{t}")
        c.execute("INSERT OR REPLACE INTO ids SELECT * FROM s.ids")
        for t, cols in _COPY_HEAD.items():
            cl = ",".join(cols)
            c.execute(f"INSERT INTO {t}({cl}, rev_from) SELECT {cl}, 1 FROM s.{t} WHERE rev_to IS NULL AND node_id IN (SELECT node_id FROM sel)")
        # MPNs and domains that the chosen nodes belong to
        c.execute("INSERT INTO mpn_v(mpn_id, slug, name, kind, description, version, manifest, installed, enabled, rev_from) "
                  "SELECT mpn_id, slug, name, kind, description, version, manifest, installed, enabled, 1 FROM s.mpn_v "
                  "WHERE rev_to IS NULL AND mpn_id IN (SELECT mpn_id FROM member_v)")
        c.execute("INSERT INTO domain_v(domain_id, mpn_id, parent_id, slug, name, depth, rev_from) "
                  "SELECT domain_id, mpn_id, parent_id, slug, name, depth, 1 FROM s.domain_v WHERE rev_to IS NULL AND mpn_id IN (SELECT mpn_id FROM mpn_v)")
        # relations among the chosen nodes only
        c.execute("INSERT INTO edge_v(edge_id, src, dst, type, weight, origin, evidence, rev_from) "
                  "SELECT edge_id, src, dst, type, weight, origin, evidence, 1 FROM s.edge_v WHERE rev_to IS NULL "
                  "AND src IN (SELECT node_id FROM sel) AND dst IN (SELECT node_id FROM sel)")
        # derived indexes stay valid for unchanged content: text embeddings, index bookkeeping, layout
        c.execute("INSERT INTO overlay SELECT * FROM s.overlay WHERE node_id IN (SELECT node_id FROM sel)")
        c.execute("INSERT INTO derived_state SELECT * FROM s.derived_state WHERE node_id IN (SELECT node_id FROM sel)")
        for t in ("boards",):
            c.execute(f"INSERT OR REPLACE INTO {t} SELECT * FROM s.{t}")
        c.execute("INSERT INTO layout SELECT * FROM s.layout WHERE node_id IN (SELECT node_id FROM sel)")
        c.execute("INSERT INTO layout_rtree SELECT * FROM s.layout_rtree WHERE id IN (SELECT node_id FROM sel)")
        c.execute("INSERT INTO regions SELECT * FROM s.regions WHERE (kind='mpn' AND ref_id IN (SELECT mpn_id FROM mpn_v)) "
                  "OR (kind='domain' AND ref_id IN (SELECT domain_id FROM domain_v)) OR kind NOT IN ('mpn','domain')")
        # learned-state bookkeeping: the router checkpoint stays usable for nodes whose content is unchanged
        for t in ("banks", "checkpoints"):
            c.execute(f"INSERT OR REPLACE INTO {t} SELECT * FROM s.{t}")
        c.execute("INSERT INTO bank_rows SELECT * FROM s.bank_rows WHERE node_id IN (SELECT node_id FROM sel)")
        c.execute("INSERT INTO node_fts(rowid, title, aliases, summary, body, sources) SELECT rowid, title, aliases, summary, body, sources "
                  "FROM s.node_fts WHERE rowid IN (SELECT node_id FROM sel)")
        n_edges = c.execute("SELECT COUNT(*) FROM edge_v").fetchone()[0]
        c.execute("UPDATE revisions SET stats=? WHERE rev=1", (json.dumps({"nodes": n_sel, "edges": n_edges}),))
        c.execute("COMMIT")
        c.execute("DETACH DATABASE s")
    finally:
        c.close()
    st = Store(dst)
    try:
        st.rebuild_summaries()
        from . import layout
        layout.rebuild_tiles(st.conn(), 1)
        st.checkpoint_wal()
        counts = st.counts()
    finally:
        st.close()
    return {"nodes": n_sel, "edges": n_edges, "counts": counts}


# ---------------------------------------------------------------------------------------------- merge
def merge_into(target: Store, src: str | Path, ids: Iterable[int] | None = None, on_conflict: str = "skip", actor: str = "local") -> dict:
    """Copy nodes from the network file `src` into `target` as one revision.

    Nodes get new ids in the target; `origin` is kept and the source identity is recorded in cell `provenance`
    ({"merged_from": {"uid": ..., "node_id": ...}}). MPNs are matched by slug (created when missing), domains by slug
    within their MPN. Relations among the copied nodes are copied too.
    on_conflict: a node with the same uid, or the same title and type, already in the target is
      "skip"  left out (reported), "both"  copied anyway (a second node)."""
    if on_conflict not in ("skip", "both"):
        raise ValueError("on_conflict must be skip or both")
    s = sqlite3.connect(f"file:{Path(src)}?mode=ro", uri=True)
    s.row_factory = sqlite3.Row
    try:
        if ids is None:
            src_ids = [r[0] for r in s.execute("SELECT node_id FROM node_v WHERE rev_to IS NULL AND status<>'tombstone' ORDER BY node_id")]
        else:
            src_ids = list(dict.fromkeys(int(i) for i in ids))
        by_uid = {r[0]: r[1] for r in target.q("SELECT uid, node_id FROM node_v WHERE rev_to IS NULL AND status<>'tombstone'")}
        by_title = {(r[0].lower(), r[1]): r[2] for r in target.q("SELECT title, type, node_id FROM node_v WHERE rev_to IS NULL AND status<>'tombstone'")}
        mpn_slug = {m["slug"]: int(m["id"]) for m in target.mpns()}
        out = {"copied": 0, "conflicts": [], "skipped": 0, "edges": 0, "mpns_created": [], "rev": target.head()}
        idmap: dict[int, int] = {}
        with target.edit(actor=actor, message=f"merge {len(src_ids)} nodes from {Path(src).name}") as tx:
            src_mpns = {r["mpn_id"]: dict(r) for r in s.execute("SELECT * FROM mpn_v WHERE rev_to IS NULL")}
            src_dom = {r["domain_id"]: dict(r) for r in s.execute("SELECT * FROM domain_v WHERE rev_to IS NULL")}
            dom_map: dict[int, int] = {}

            def tgt_mpn(mid: int) -> int:
                m = src_mpns[mid]
                if m["slug"] not in mpn_slug:
                    mpn_slug[m["slug"]] = tx.put_mpn(m["slug"], m["name"], kind=m["kind"], description=m["description"])
                    out["mpns_created"].append(m["name"])
                return mpn_slug[m["slug"]]

            def tgt_dom(did: int | None, mpn_t: int) -> int | None:
                if did is None or did not in src_dom:
                    return None
                if did not in dom_map:
                    d = src_dom[did]
                    r = tx.c.execute("SELECT domain_id FROM domain_v WHERE rev_to IS NULL AND mpn_id=? AND slug=?", (mpn_t, d["slug"])).fetchone()
                    dom_map[did] = r[0] if r else tx.put_domain(mpn_t, d["slug"], d["name"], depth=d["depth"])
                return dom_map[did]

            for i in range(0, len(src_ids), 500):
                chunk = src_ids[i:i + 500]
                ph = ",".join("?" * len(chunk))
                rows = s.execute(f"SELECT * FROM node_v WHERE rev_to IS NULL AND status<>'tombstone' AND node_id IN ({ph})", chunk).fetchall()
                mems: dict[int, list] = {}
                for m in s.execute(f"SELECT * FROM member_v WHERE rev_to IS NULL AND node_id IN ({ph}) ORDER BY role<>'primary'", chunk):
                    mems.setdefault(m["node_id"], []).append(dict(m))
                for r in rows:
                    hit = by_uid.get(r["uid"]) or by_title.get((r["title"].lower(), r["type"]))
                    if hit is not None:
                        out["conflicts"].append({"title": r["title"], "source_id": sid(r["node_id"]), "target_id": sid(hit)})
                        if on_conflict == "skip":
                            out["skipped"] += 1
                            continue
                    cells = json.loads(r["cells"])
                    prov = dict(cells.get("provenance") or {})
                    prov["merged_from"] = {"uid": r["uid"], "node_id": sid(r["node_id"]), "network": Path(src).parent.name or Path(src).name}
                    cells["provenance"] = prov
                    uid = r["uid"] if r["uid"] not in by_uid else None
                    nid = tx.put_node(uid=uid, type=r["type"], title=r["title"], summary=r["summary"], aliases=json.loads(r["aliases"]),
                                      schema_id=r["schema_id"], schema_version=r["schema_version"], status=r["status"],
                                      validation=r["validation"], origin=r["origin"], cells=cells)
                    if uid:
                        by_uid[uid] = nid
                    idmap[r["node_id"]] = nid
                    for m in mems.get(r["node_id"], []):
                        mt = tgt_mpn(m["mpn_id"])
                        tx.set_membership(nid, mt, tgt_dom(m["domain_id"], mt), evidence=json.loads(m["evidence"] or "{}"))
                    for ns in s.execute("SELECT source_id, locator FROM node_source_v WHERE rev_to IS NULL AND node_id=?", (r["node_id"],)):
                        so = s.execute("SELECT * FROM sources WHERE id=?", (ns[0],)).fetchone()
                        if so:
                            sid_t = tx.put_source(so["kind"], so["origin"], so["title"], url=so["url"], license=so["license"],
                                                  license_url=so["license_url"], revision=so["revision"], retrieved_at=so["retrieved_at"],
                                                  attribution=so["attribution"])
                            tx.link_source(nid, sid_t, ns[1])
                    out["copied"] += 1
            if idmap:
                keys = list(idmap)
                for i in range(0, len(keys), 450):
                    chunk = keys[i:i + 450]
                    ph = ",".join("?" * len(chunk))
                    for e in s.execute(f"SELECT * FROM edge_v WHERE rev_to IS NULL AND src IN ({ph})", chunk):
                        if e["dst"] in idmap:
                            tx.put_edge(idmap[e["src"]], idmap[e["dst"]], e["type"], weight=e["weight"], origin=e["origin"],
                                        evidence=json.loads(e["evidence"] or "{}"))
                            out["edges"] += 1
            out["rev"] = tx.rev
    finally:
        s.close()
    if idmap:
        from . import layout
        for nid in idmap.values():
            layout.place_node(target, 1, nid)
    out["conflict_count"] = len(out["conflicts"])
    out["conflicts"] = out["conflicts"][:200]
    return out


# ---------------------------------------------------------------------------------------------- compare
def _head(path: Path) -> dict[str, tuple[int, str, str, str]]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in c.execute(
            "SELECT uid, node_id, title, type, content_hash FROM node_v WHERE rev_to IS NULL AND status<>'tombstone'")}
    finally:
        c.close()


def compare(a: str | Path, b: str | Path, sample: int = 50) -> dict:
    """Differences from network a to network b, matched by portable node identity (uid)."""
    ha, hb = _head(Path(a)), _head(Path(b))
    only_a = [u for u in ha if u not in hb]
    only_b = [u for u in hb if u not in ha]
    changed = [u for u in ha if u in hb and ha[u][3] != hb[u][3]]
    same = len(ha) - len(only_a) - len(changed)

    def row(h, u):
        return {"uid": u, "id": sid(h[u][0]), "title": h[u][1], "type": h[u][2]}
    return {"a_nodes": len(ha), "b_nodes": len(hb), "unchanged": same, "removed": len(only_a), "added": len(only_b), "changed": len(changed),
            "removed_sample": [row(ha, u) for u in only_a[:sample]], "added_sample": [row(hb, u) for u in only_b[:sample]],
            "changed_sample": [row(hb, u) for u in changed[:sample]]}


def duplicates(store: Store, limit: int = 200) -> dict:
    """Live nodes that share a title and type (possible duplicates or conflicting knowledge)."""
    rows = store.q("SELECT lower(title) t, type, COUNT(*) n, group_concat(node_id) ids FROM node_v WHERE rev_to IS NULL AND status<>'tombstone' "
                   "GROUP BY lower(title), type HAVING n>1 ORDER BY n DESC, t LIMIT ?", (limit,))
    total = store.q1("SELECT COUNT(*) FROM (SELECT 1 FROM node_v WHERE rev_to IS NULL AND status<>'tombstone' GROUP BY lower(title), type HAVING COUNT(*)>1)")[0]
    return {"groups": total, "sample": [{"title": r["t"], "type": r["type"], "count": r["n"], "ids": r["ids"].split(",")} for r in rows]}


# ---------------------------------------------------------------------------------------------- statistics
def stats(path: str | Path) -> dict:
    """Facts read from the network file. Nothing is estimated."""
    p = Path(path)
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        q1 = lambda sql, a=(): c.execute(sql, a).fetchone()[0]
        live = q1("SELECT COUNT(*) FROM node_v WHERE rev_to IS NULL AND status='live'")
        out = {
            "nodes": live,
            "nodes_disabled": q1("SELECT COUNT(*) FROM node_v WHERE rev_to IS NULL AND status='disabled'"),
            "nodes_deleted": q1("SELECT COUNT(*) FROM node_v WHERE rev_to IS NULL AND status='tombstone'"),
            "by_type": {r[0]: r[1] for r in c.execute("SELECT type, COUNT(*) FROM node_v WHERE rev_to IS NULL AND status='live' GROUP BY type")},
            "relationships": q1("SELECT COUNT(*) FROM edge_v e JOIN node_v a ON a.node_id=e.src AND a.rev_to IS NULL AND a.status<>'tombstone' "
                                "JOIN node_v b ON b.node_id=e.dst AND b.rev_to IS NULL AND b.status<>'tombstone' WHERE e.rev_to IS NULL"),
            "invalid_relationships": q1("SELECT COUNT(*) FROM edge_v e WHERE e.rev_to IS NULL AND (NOT EXISTS(SELECT 1 FROM node_v a WHERE a.node_id=e.src "
                                        "AND a.rev_to IS NULL AND a.status<>'tombstone') OR NOT EXISTS(SELECT 1 FROM node_v b WHERE b.node_id=e.dst "
                                        "AND b.rev_to IS NULL AND b.status<>'tombstone'))"),
            # (an OR across src and dst defeats the indexes: one set of connected ids instead)
            "orphan_nodes": q1("SELECT COUNT(*) FROM node_v n WHERE n.rev_to IS NULL AND n.status='live' AND n.node_id NOT IN "
                               "(SELECT src FROM edge_v WHERE rev_to IS NULL UNION SELECT dst FROM edge_v WHERE rev_to IS NULL)"),
            "unassigned_nodes": q1("SELECT COUNT(*) FROM node_v n WHERE n.rev_to IS NULL AND n.status='live' AND NOT EXISTS(SELECT 1 FROM member_v m "
                                   "WHERE m.rev_to IS NULL AND m.node_id=n.node_id)"),
            "revisions": q1("SELECT MAX(rev) FROM revisions"),
            "last_modified": q1("SELECT created FROM revisions ORDER BY rev DESC LIMIT 1"),
            "file_bytes": p.stat().st_size + sum((p.parent / (p.name + s)).stat().st_size for s in ("-wal",) if (p.parent / (p.name + s)).exists()),
            "indexed_embeddings": q1("SELECT COUNT(*) FROM overlay o JOIN node_v n ON n.node_id=o.node_id AND n.rev_to IS NULL AND n.content_hash=o.content_hash"),
        }
        out["domains"] = [{"id": sid(r[0]), "name": r[1], "kind": r[2], "enabled": bool(r[3]), "nodes": r[4]} for r in c.execute(
            "SELECT m.mpn_id, m.name, m.kind, m.enabled, (SELECT COUNT(*) FROM member_v x JOIN node_v n ON n.node_id=x.node_id AND n.rev_to IS NULL "
            "AND n.status='live' WHERE x.rev_to IS NULL AND x.mpn_id=m.mpn_id) FROM mpn_v m WHERE m.rev_to IS NULL ORDER BY m.name")]
        try:
            sizes = {r[0]: r[1] for r in c.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name")}
            out["index_bytes"] = {"text_search": sum(v for k, v in sizes.items() if k.startswith("node_fts")),
                                  "embeddings": sizes.get("overlay", 0),
                                  "board": sum(v for k, v in sizes.items() if k.startswith(("layout", "tiles", "regions"))),
                                  "sql_indexes": sum(v for k, v in sizes.items() if k.startswith(("node_", "member_", "edge_", "mpn_", "domain_", "nsrc_", "jobs_", "bank_rows_"))
                                                     and not k.startswith("node_fts") and k not in ("node_v", "node_source_v"))}
        except sqlite3.Error:
            out["index_bytes"] = None        # SQLite built without the dbstat table
    finally:
        c.close()
    return out
