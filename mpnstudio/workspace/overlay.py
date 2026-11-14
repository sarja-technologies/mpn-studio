"""Copy-on-write edits over a read-only MPN network (`mpn-overlay/1`).

A fork of a large network (Yaob Origin N119M: 119 million nodes, 129 GiB) never copies the network. The base stays
where it is, opened read-only (a pack folder on this computer or the network API of another computer); this file
holds only what the fork changed:

    revisions   one row per edit, undo or redo (undo and redo are revisions too, as in the project store)
    node_ops    (rev, nid) -> put | del | base      a put carries the whole edited node document
    edge_ops    (rev, src, rel, dst) -> add | del | base
    cur_nodes   the state at the head revision, for reads (only nodes the fork touched)
    cur_edges   the same for relationships
    ofts        full-text index over cur_nodes (titles, aliases, summaries of edited and new nodes)
    versions    named revisions ("Save version")

`base` means "as in the base network": an undo that reaches back before a node's first edit writes `base`, and the
node reads from the base again. Nothing in this module can write to the base: it never receives a handle that could.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

FORMAT = "mpn-overlay/1"

DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions(rev INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,
  message TEXT NOT NULL DEFAULT '', undo_of INTEGER);
CREATE TABLE IF NOT EXISTS node_ops(rev INTEGER NOT NULL, nid TEXT NOT NULL, state TEXT NOT NULL, doc TEXT,
  PRIMARY KEY(nid, rev));
CREATE INDEX IF NOT EXISTS node_ops_rev ON node_ops(rev);
CREATE TABLE IF NOT EXISTS edge_ops(rev INTEGER NOT NULL, src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL,
  state TEXT NOT NULL, label TEXT, PRIMARY KEY(src, rel, dst, rev));
CREATE INDEX IF NOT EXISTS edge_ops_rev ON edge_ops(rev);
CREATE TABLE IF NOT EXISTS cur_nodes(id INTEGER PRIMARY KEY, nid TEXT NOT NULL UNIQUE, state TEXT NOT NULL, doc TEXT,
  rev INTEGER NOT NULL, created INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS cur_edges(src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, state TEXT NOT NULL,
  label TEXT, rev INTEGER NOT NULL, PRIMARY KEY(src, rel, dst));
CREATE INDEX IF NOT EXISTS cur_edges_dst ON cur_edges(dst);
CREATE VIRTUAL TABLE IF NOT EXISTS ofts USING fts5(title, aliases, summary, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS versions(name TEXT PRIMARY KEY, rev INTEGER NOT NULL, created REAL NOT NULL, note TEXT NOT NULL DEFAULT '');
"""

EDITABLE = ("title", "summary", "aliases", "domain", "subdomain", "kind", "notes", "statements")


class OverlayError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def new_node_id() -> str:
    # 32 hex digits like the base ids; the 'f0' prefix marks ids made in MPN Studio forks
    return "f0" + uuid.uuid4().hex[2:]


def _fts_text(doc: dict) -> tuple[str, str, str]:
    return (doc.get("title") or "", " ".join(doc.get("aliases") or [])[:2000], (doc.get("summary") or "")[:2000])


class _Rows:
    """A finished cursor: the rows were read while the lock was held."""
    def __init__(self, rows: list, lastrowid: int | None):
        self._rows, self.lastrowid = rows, lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _Locked:
    """The change file's one connection, shared by the server's threads. Every statement runs under the overlay's lock
    and its rows are read before the lock is released, so two requests can never interleave on the connection."""
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn, self._lock = conn, lock

    def execute(self, sql: str, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            return _Rows(cur.fetchall() if cur.description else [], cur.lastrowid)

    def executescript(self, sql: str):
        with self._lock:
            return self._conn.executescript(sql)

    def close(self):
        with self._lock:
            self._conn.close()

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, f):
        self._conn.row_factory = f


class Overlay:
    def __init__(self, path: str | Path, base_identity: dict | None = None, create: bool = False):
        self.path = Path(path)
        if not create and not self.path.is_file():
            raise OverlayError(f"The fork's change file is missing ({self.path.name}). Restore the project from a backup.", 500)
        self._lock = threading.RLock()
        self._c = _Locked(sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30), self._lock)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA synchronous=NORMAL")
        self._c.executescript(DDL)
        if create:
            self._set("format", FORMAT)
            self._set("created", time.time())
            self._set("base", base_identity or {})
            self._set("undo", [])
            self._set("redo", [])
        elif self._get("format") != FORMAT:
            raise OverlayError(f"{self.path.name} is not an MPN Studio change file ({self._get('format')!r}).", 500)

    # ------------------------------------------------------------------ small helpers
    def _get(self, key: str, default=None):
        r = self._c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def _set(self, key: str, value) -> None:
        self._c.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def close(self) -> None:
        with self._lock:
            try:
                self._c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._c.close()

    def checkpoint(self) -> None:
        with self._lock:
            self._c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @property
    def base_identity(self) -> dict:
        return self._get("base", {})

    def head(self) -> int:
        return self._c.execute("SELECT COALESCE(MAX(rev), 0) FROM revisions").fetchone()[0]

    # ------------------------------------------------------------------ reads at head
    def node_state(self, nid: str) -> tuple[str, dict | None]:
        """('put', doc) | ('del', None) | ('base', None)."""
        r = self._c.execute("SELECT state, doc FROM cur_nodes WHERE nid=?", (nid,)).fetchone()
        if not r:
            return "base", None
        return r["state"], (json.loads(r["doc"]) if r["doc"] else None)

    def node_states(self, nids: list[str]) -> dict[str, tuple[str, dict | None]]:
        out = {}
        for i in range(0, len(nids), 400):
            chunk = nids[i:i + 400]
            q = ",".join("?" * len(chunk))
            for r in self._c.execute(f"SELECT nid, state, doc FROM cur_nodes WHERE nid IN ({q})", chunk):
                out[r["nid"]] = (r["state"], json.loads(r["doc"]) if r["doc"] else None)
        return out

    def edges_of(self, nid: str) -> list[dict]:
        """Relationships of `nid` that the fork added or removed (both directions)."""
        rows = self._c.execute("SELECT src, rel, dst, state, label FROM cur_edges WHERE src=? UNION ALL "
                               "SELECT src, rel, dst, state, label FROM cur_edges WHERE dst=? AND src!=?", (nid, nid, nid)).fetchall()
        return [dict(r) for r in rows]

    def search(self, q: str, limit: int = 50) -> list[dict]:
        terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in q).split() if t]
        if not terms:
            return []
        expr = " ".join(f'"{t}"*' for t in terms[:12])
        try:
            rows = self._c.execute("SELECT c.doc FROM ofts JOIN cur_nodes c ON c.id=ofts.rowid WHERE ofts MATCH ? "
                                   "AND c.state='put' ORDER BY rank LIMIT ?", (expr, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [json.loads(r[0]) for r in rows]

    def added_nodes(self, domain: str | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
        rows = self._c.execute("SELECT doc FROM cur_nodes WHERE state='put' AND created=1 ORDER BY id LIMIT ? OFFSET ?",
                               (limit * 4 if domain else limit, offset)).fetchall()
        docs = [json.loads(r[0]) for r in rows]
        if domain:
            docs = [d for d in docs if d.get("domain") == domain][:limit]
        return docs

    def stats(self) -> dict:
        c = self._c
        added, edited, deleted = c.execute("SELECT SUM(state='put' AND created=1), SUM(state='put' AND created=0), "
                                           "SUM(state='del' AND created=0) FROM cur_nodes").fetchone()
        e = c.execute("SELECT SUM(state='add'), SUM(state='del') FROM cur_edges").fetchone()
        self.checkpoint_quiet()
        wal = Path(str(self.path) + "-wal")
        size = self.path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
        return {"nodes_added": added or 0, "nodes_edited": edited or 0, "nodes_deleted": deleted or 0,
                "relationships_added": e[0] or 0, "relationships_removed": e[1] or 0, "revision": self.head(),
                "bytes": size, "can_undo": bool(self._get("undo", [])), "can_redo": bool(self._get("redo", []))}

    def checkpoint_quiet(self) -> None:
        try:
            self._c.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ writes
    def _new_rev(self, kind: str, message: str, undo_of: int | None = None) -> int:
        cur = self._c.execute("INSERT INTO revisions(ts, kind, message, undo_of) VALUES(?,?,?,?)", (time.time(), kind, message, undo_of))
        return cur.lastrowid

    def _apply_node(self, rev: int, nid: str, state: str, doc: dict | None, created: bool | None = None) -> None:
        c = self._c
        c.execute("INSERT OR REPLACE INTO node_ops VALUES(?,?,?,?)", (rev, nid, state, json.dumps(doc, ensure_ascii=False) if doc else None))
        old = c.execute("SELECT id, created FROM cur_nodes WHERE nid=?", (nid,)).fetchone()
        if old:
            c.execute("DELETE FROM ofts WHERE rowid=?", (old["id"],))
        if state == "base":
            if old:
                c.execute("DELETE FROM cur_nodes WHERE id=?", (old["id"],))
            return
        made = int(created if created is not None else (old["created"] if old else 0))
        if old:
            c.execute("UPDATE cur_nodes SET state=?, doc=?, rev=?, created=? WHERE id=?",
                      (state, json.dumps(doc, ensure_ascii=False) if doc else None, rev, made, old["id"]))
            rid = old["id"]
        else:
            rid = c.execute("INSERT INTO cur_nodes(nid, state, doc, rev, created) VALUES(?,?,?,?,?)",
                            (nid, state, json.dumps(doc, ensure_ascii=False) if doc else None, rev, made)).lastrowid
        if state == "put" and doc:
            c.execute("INSERT INTO ofts(rowid, title, aliases, summary) VALUES(?,?,?,?)", (rid, *_fts_text(doc)))

    def _apply_edge(self, rev: int, src: str, rel: str, dst: str, state: str, label: str | None) -> None:
        c = self._c
        c.execute("INSERT OR REPLACE INTO edge_ops VALUES(?,?,?,?,?,?)", (rev, src, rel, dst, state, label))
        if state == "base":
            c.execute("DELETE FROM cur_edges WHERE src=? AND rel=? AND dst=?", (src, rel, dst))
        else:
            c.execute("INSERT OR REPLACE INTO cur_edges VALUES(?,?,?,?,?,?)", (src, rel, dst, state, label, rev))

    def edit(self, message: str, nodes: list[tuple[str, str, dict | None, bool | None]] = (), edges: list[tuple[str, str, str, str, str | None]] = ()) -> int:
        """One revision. nodes: (nid, 'put'|'del', doc, created). edges: (src, rel, dst, 'add'|'del', label)."""
        if not nodes and not edges:
            raise OverlayError("Nothing to change.")
        with self._lock:
            self._c.execute("BEGIN IMMEDIATE")
            try:
                rev = self._new_rev("edit", message)
                for nid, state, doc, created in nodes:
                    self._apply_node(rev, nid, state, doc, created)
                for src, rel, dst, state, label in edges:
                    if state == "del":   # removing a relationship this fork added cancels the addition
                        cur = self._c.execute("SELECT state FROM cur_edges WHERE src=? AND rel=? AND dst=?", (src, rel, dst)).fetchone()
                        if cur is not None and cur["state"] == "add":
                            state = "base"
                    self._apply_edge(rev, src, rel, dst, state, label)
                undo = self._get("undo", [])
                undo.append(rev)
                self._set("undo", undo[-500:])
                self._set("redo", [])
                self._c.execute("COMMIT")
            except BaseException:
                self._c.execute("ROLLBACK")
                raise
        return rev

    def _state_before(self, table: str, key: tuple, rev: int):
        if table == "node":
            r = self._c.execute("SELECT state, doc FROM node_ops WHERE nid=? AND rev<? ORDER BY rev DESC LIMIT 1", (*key, rev)).fetchone()
            return ("base", None) if not r else (r["state"], json.loads(r["doc"]) if r["doc"] else None)
        r = self._c.execute("SELECT state, label FROM edge_ops WHERE src=? AND rel=? AND dst=? AND rev<? ORDER BY rev DESC LIMIT 1",
                            (*key, rev)).fetchone()
        return ("base", None) if not r else (r["state"], r["label"])

    def _created_flag(self, nid: str) -> int:
        r = self._c.execute("SELECT 1 FROM node_ops o JOIN revisions r ON r.rev=o.rev WHERE o.nid=? AND o.state='put' AND "
                            "r.kind='edit' AND json_extract(o.doc, '$.created_in_fork')=1 LIMIT 1", (nid,)).fetchone()
        return 1 if r else 0

    def _revert_to(self, target_rev: int, source_rev: int, kind: str) -> int:
        """New revision that sets every key touched by `source_rev` to its state just before it (undo) or at it (redo)."""
        c = self._c
        new = self._new_rev(kind, f"{kind} of revision {source_rev}", source_rev)
        for r in c.execute("SELECT nid FROM node_ops WHERE rev=?", (source_rev,)).fetchall():
            nid = r["nid"]
            if kind == "undo":
                state, doc = self._state_before("node", (nid,), source_rev)
            else:
                o = c.execute("SELECT state, doc FROM node_ops WHERE nid=? AND rev=?", (nid, source_rev)).fetchone()
                state, doc = o["state"], json.loads(o["doc"]) if o["doc"] else None
            self._apply_node(new, nid, state, doc, bool(self._created_flag(nid)))
        for r in c.execute("SELECT src, rel, dst FROM edge_ops WHERE rev=?", (source_rev,)).fetchall():
            key = (r["src"], r["rel"], r["dst"])
            if kind == "undo":
                state, label = self._state_before("edge", key, source_rev)
            else:
                o = c.execute("SELECT state, label FROM edge_ops WHERE src=? AND rel=? AND dst=? AND rev=?", (*key, source_rev)).fetchone()
                state, label = o["state"], o["label"]
            self._apply_edge(new, *key, state, label)
        return new

    def undo(self) -> int | None:
        with self._lock:
            undo = self._get("undo", [])
            if not undo:
                return None
            self._c.execute("BEGIN IMMEDIATE")
            try:
                r = undo.pop()
                new = self._revert_to(r, r, "undo")
                redo = self._get("redo", [])
                redo.append(r)
                self._set("undo", undo)
                self._set("redo", redo)
                self._c.execute("COMMIT")
            except BaseException:
                self._c.execute("ROLLBACK")
                raise
            return new

    def redo(self) -> int | None:
        with self._lock:
            redo = self._get("redo", [])
            if not redo:
                return None
            self._c.execute("BEGIN IMMEDIATE")
            try:
                r = redo.pop()
                new = self._revert_to(r, r, "redo")
                undo = self._get("undo", [])
                undo.append(new)
                self._set("undo", undo)
                self._set("redo", redo)
                self._c.execute("COMMIT")
            except BaseException:
                self._c.execute("ROLLBACK")
                raise
            return new

    # ------------------------------------------------------------------ history, versions, compare
    def history(self, limit: int = 100) -> list[dict]:
        rows = self._c.execute("SELECT r.*, (SELECT COUNT(*) FROM node_ops WHERE rev=r.rev) AS nodes, "
                               "(SELECT COUNT(*) FROM edge_ops WHERE rev=r.rev) AS edges FROM revisions r ORDER BY rev DESC LIMIT ?",
                               (limit,)).fetchall()
        return [dict(r) for r in rows]

    def versions(self) -> list[dict]:
        return [dict(r) for r in self._c.execute("SELECT * FROM versions ORDER BY rev DESC, created DESC")]

    def save_version(self, name: str, note: str = "") -> dict:
        name = (name or "").strip()[:120]
        if not name:
            raise OverlayError("Give the version a name.")
        with self._lock:
            if self._c.execute("SELECT 1 FROM versions WHERE name=?", (name,)).fetchone():
                raise OverlayError(f"A version called “{name}” already exists.", 409)
            self._c.execute("INSERT INTO versions VALUES(?,?,?,?)", (name, self.head(), time.time(), note))
        return {"name": name, "rev": self.head()}

    def version_rev(self, name: str) -> int:
        if name in ("", "current", None):
            return self.head()
        if name == "original":
            return 0
        r = self._c.execute("SELECT rev FROM versions WHERE name=?", (name,)).fetchone()
        if not r:
            raise OverlayError(f"No version called “{name}”.", 404)
        return r[0]

    def node_at(self, nid: str, rev: int) -> tuple[str, dict | None]:
        r = self._c.execute("SELECT state, doc FROM node_ops WHERE nid=? AND rev<=? ORDER BY rev DESC LIMIT 1", (nid, rev)).fetchone()
        return ("base", None) if not r else (r["state"], json.loads(r["doc"]) if r["doc"] else None)

    def edge_at(self, key: tuple, rev: int) -> tuple[str, str | None]:
        r = self._c.execute("SELECT state, label FROM edge_ops WHERE src=? AND rel=? AND dst=? AND rev<=? ORDER BY rev DESC LIMIT 1",
                            (*key, rev)).fetchone()
        return ("base", None) if not r else (r["state"], r["label"])

    def compare(self, ra: int, rb: int, limit: int = 2000) -> dict:
        """Nodes and relationships whose state differs between revisions ra and rb (either order)."""
        lo, hi = min(ra, rb), max(ra, rb)
        c = self._c
        nids = [r[0] for r in c.execute("SELECT DISTINCT nid FROM node_ops WHERE rev>? AND rev<=? LIMIT ?", (lo, hi, limit + 1))]
        nodes = []
        for nid in nids[:limit]:
            a, b = self.node_at(nid, ra), self.node_at(nid, rb)
            if a == b:
                continue
            nodes.append({"id": nid, "before": a[0], "after": b[0], "before_doc": a[1], "after_doc": b[1]})
        keys = c.execute("SELECT DISTINCT src, rel, dst FROM edge_ops WHERE rev>? AND rev<=? LIMIT ?", (lo, hi, limit + 1)).fetchall()
        edges = []
        for k in keys[:limit]:
            key = (k["src"], k["rel"], k["dst"])
            a, b = self.edge_at(key, ra), self.edge_at(key, rb)
            if a[0] == b[0]:
                continue
            edges.append({"src": key[0], "rel": key[1], "dst": key[2], "label": b[1] or a[1], "before": a[0], "after": b[0]})
        return {"from": ra, "to": rb, "nodes": nodes, "relationships": edges, "truncated": len(nids) > limit or len(keys) > limit}

    # ------------------------------------------------------------------ copies
    def copy_to(self, dest: str | Path, upto_rev: int | None = None) -> None:
        """A new change file with this fork's changes up to `upto_rev` (all of them by default): used to duplicate a fork
        and to open a saved version as its own project. The copy has its own history starting from that state."""
        dest = Path(dest)
        with self._lock:
            self.checkpoint_quiet()
            if upto_rev is None:
                if dest.exists():
                    raise OverlayError(f"{dest} exists.", 409)
                self._c.execute("VACUUM INTO ?", (str(dest),))
                return
            new = Overlay(dest, self.base_identity, create=True)
            try:
                nodes, edges = [], []
                for r in self._c.execute("SELECT DISTINCT nid FROM node_ops WHERE rev<=?", (upto_rev,)).fetchall():
                    st, doc = self.node_at(r[0], upto_rev)
                    if st != "base":
                        nodes.append((r[0], st, doc, bool(doc and doc.get("created_in_fork"))))
                for r in self._c.execute("SELECT DISTINCT src, rel, dst FROM edge_ops WHERE rev<=?", (upto_rev,)).fetchall():
                    st, label = self.edge_at((r[0], r[1], r[2]), upto_rev)
                    if st != "base":
                        edges.append((r[0], r[1], r[2], st, label))
                if nodes or edges:
                    new.edit(f"State of revision {upto_rev}", nodes, edges)
                    new._set("undo", [])
            finally:
                new.close()

    def integrity(self) -> str:
        return self._c.execute("PRAGMA integrity_check").fetchone()[0]
