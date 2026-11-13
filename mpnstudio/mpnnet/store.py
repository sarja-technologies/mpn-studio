# Vendored from the Yaob repository (mpn14b/store.py at f8bd650, 2026-11-13). Read-only use: MPN Studio
# opens MPN network packs with it. Keep in step with the source; the mpn-network/1 JSON must stay identical.
"""Partitioned, disk-backed node store.

Layout: `<root>/p00.sqlite … p63.sqlite`. A node lives in partition `id[0] % P`. SQLite (WAL) gives crash-consistent
transactions, lazy page-level reads and a bounded page cache per connection, so nothing is ever loaded whole.

Exactly-once accounting: every write batch carries a durable (job, seq). Each partition records the batches it has
applied in the same transaction as the rows and the counters, so replaying a batch after a crash changes nothing and
the counters always equal the stored rows.

The same layout is the MPN Pack storage format (see pack.py): a pack is a sealed copy of these partitions plus a
manifest, and the reader below works on both.
"""
from __future__ import annotations

import os
import random
import re
import sqlite3
import threading
import zlib
from pathlib import Path

import orjson

from . import schema as S

P_DEFAULT = 64

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS nodes(
  rid INTEGER PRIMARY KEY, nid BLOB NOT NULL UNIQUE, kind INT NOT NULL, domain INT NOT NULL, ds INT NOT NULL,
  lic INT NOT NULL, status INT NOT NULL, conf REAL NOT NULL, lang TEXT NOT NULL, title TEXT NOT NULL,
  subj BLOB, created INT NOT NULL, body BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS nodes_subj ON nodes(subj) WHERE subj IS NOT NULL;
CREATE TABLE IF NOT EXISTS edges(src BLOB NOT NULL, rel TEXT NOT NULL, dst BLOB NOT NULL, dir INT NOT NULL, via BLOB,
  PRIMARY KEY(src, rel, dst, dir)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS xref(ident BLOB PRIMARY KEY, nid BLOB NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS prov(nid BLOB NOT NULL, ds INT NOT NULL, rec TEXT NOT NULL, PRIMARY KEY(nid, ds, rec)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS dupcand(ident TEXT NOT NULL, a BLOB NOT NULL, b BLOB NOT NULL, PRIMARY KEY(ident, a, b)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS counts(kind INT, domain INT, ds INT, status INT, n INT NOT NULL,
  PRIMARY KEY(kind, domain, ds, status)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ecounts(ds INT PRIMARY KEY, n INT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS applied(job TEXT NOT NULL, seq INT NOT NULL, PRIMARY KEY(job, seq)) WITHOUT ROWID;
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, aliases, content='', contentless_delete=1,
  tokenize='unicode61 remove_diacritics 2');
"""

BODY_FIELDS = ("key", "summary", "aliases", "ids", "cells", "prov", "evidence", "method", "subdomain", "rel", "version",
               "modified", "licence", "links")


def pack_body(n: dict) -> bytes:
    return zlib.compress(orjson.dumps({k: n[k] for k in BODY_FIELDS if n.get(k) not in (None, [], {}, "")}), 6)


def unpack_body(b: bytes) -> dict:
    return orjson.loads(zlib.decompress(b))


_fts_tok = re.compile(r"\w+", re.UNICODE)


def fts_query(q: str, any_terms: bool = False) -> str | None:
    toks = [t for t in _fts_tok.findall(q.lower()) if len(t) > 1 or t.isdigit()][:12]
    if not toks:
        return None
    return (" OR " if any_terms else " ").join(f'"{t}"' for t in toks)


class Store:
    """Reader and writer over one partitioned store (a live store or a mounted pack)."""

    def __init__(self, root: str | os.PathLike, partitions: int = P_DEFAULT, readonly: bool = False,
                 cache_kib: int = 8192, create: bool = False):
        self.root = Path(root)
        self.P = partitions
        self.ro = readonly
        self.cache_kib = cache_kib
        self._local = threading.local()
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            for i in range(self.P):
                c = sqlite3.connect(self.path(i))
                c.executescript(DDL)
                c.execute("INSERT OR IGNORE INTO meta VALUES('partition', ?)", (str(i),))
                c.execute("INSERT OR IGNORE INTO meta VALUES('partitions', ?)", (str(self.P),))
                c.execute("INSERT OR IGNORE INTO meta VALUES('format', 'mpn-store/1')")
                c.commit()
                c.close()

    def path(self, i: int) -> Path:
        return self.root / f"p{i:02d}.sqlite"

    def part(self, nid: bytes) -> int:
        return nid[0] % self.P

    def conn(self, i: int) -> sqlite3.Connection:
        cs = getattr(self._local, "c", None)
        if cs is None or self._local.pid != os.getpid():
            cs = self._local.c = {}
            self._local.pid = os.getpid()
        c = cs.get(i)
        if c is None:
            if self.ro:
                c = sqlite3.connect(f"file:{self.path(i)}?mode=ro", uri=True, timeout=60, check_same_thread=False)
            else:
                c = sqlite3.connect(self.path(i), timeout=120, isolation_level=None, check_same_thread=False)
                c.execute("PRAGMA synchronous=NORMAL")
            c.execute(f"PRAGMA cache_size=-{self.cache_kib}")
            c.execute("PRAGMA busy_timeout=120000")
            cs[i] = c
        return c

    def close(self):
        for c in (getattr(self._local, "c", None) or {}).values():
            c.close()
        self._local.c = {}

    # ------------------------------------------------------------------ writing
    def write_batch(self, job: str, seq: int, ds: int, nodes: list[dict], edges: list[tuple] = (),
                    xrefs: list[tuple] = (), prov: list[tuple] = (), rank: dict[int, int] | None = None) -> dict:
        """nodes: validated node dicts with `nid` (bytes) and optional `adj` (relationships kept as a compact
        adjacency list inside the node body, counted once when the node is new).
        edges: (src, rel, dst, via, mode); mode 'both' stores a forward and a reverse row, 'rev' only the reverse
        row (the forward direction is the claim node itself), 'fwd' only the forward row. Each relationship is
        counted once. xrefs: (identifier key, nid): the identifier resolves to nid. prov: (nid, record).
        Returns exact counts."""
        by: dict[int, dict] = {}

        def slot(i):
            return by.setdefault(i, {"n": [], "e": [], "x": [], "p": []})
        for n in nodes:
            slot(self.part(n["nid"]))["n"].append(n)
        for src, rel, dst, via, mode in edges:
            # the relationship is counted on the row written for it first: forward if any, else reverse
            if mode in ("both", "fwd"):
                slot(self.part(src))["e"].append((src, rel, dst, 0, via, True))
            if mode in ("both", "rev"):
                slot(self.part(dst))["e"].append((dst, rel, src, 1, via, mode == "rev"))
        for ident, nid in xrefs:
            ih = S.h16(ident)
            if ih != nid:
                slot(self.part(ih))["x"].append((ih, nid, ident))
        for nid, rec in prov:
            slot(self.part(nid))["p"].append((nid, ds, rec))
        out = {"inserted": 0, "duplicates": 0, "superseded": 0, "edges": 0, "xref_conflicts": 0, "replayed": 0,
               "by_kind": {}, "by_domain": {}}
        order = list(by)
        random.shuffle(order)       # spread writers over partitions
        for i in order:
            w = by[i]
            c = self.conn(i)
            c.execute("BEGIN IMMEDIATE")
            try:
                if c.execute("SELECT 1 FROM applied WHERE job=? AND seq=?", (job, seq)).fetchone():
                    c.execute("ROLLBACK")
                    out["replayed"] += 1
                    continue
                new = w["n"]
                if new:
                    have = {}
                    ids = [n["nid"] for n in new]
                    for k in range(0, len(ids), 900):
                        ch = ids[k:k + 900]
                        for r in c.execute(f"SELECT nid, rid, ds, kind, domain, status FROM nodes WHERE nid IN ({','.join('?' * len(ch))})", ch):
                            have[r[0]] = r
                    seen = set()
                    rows, cnt, adj = [], {}, 0
                    my_rank = (rank or {}).get(ds, 0)
                    for n in new:
                        old = have.get(n["nid"])
                        if old is not None and n["nid"] not in seen and my_rank > (rank or {}).get(old[2], 0):
                            # a higher-precedence source (curated migration > Wikidata > others) replaces the body
                            seen.add(n["nid"])
                            kid, did = S.KIND_ID[n["kind"]], S.DOMAIN_ID[n.get("domain", "general")]
                            c.execute("UPDATE nodes SET kind=?, domain=?, ds=?, lic=?, conf=?, lang=?, title=?, subj=?, body=? WHERE rid=?",
                                      (kid, did, ds, S.LIC_ID[n["licence"]], n["conf"], n["lang"], n["title"], n.get("subject"),
                                       pack_body(n), old[1]))
                            c.execute("DELETE FROM fts WHERE rowid=?", (old[1],))
                            if kid in S.SEARCHABLE:
                                c.execute("INSERT INTO fts(rowid,title,aliases) VALUES(?,?,?)",
                                          (old[1], n["title"], " ".join(n.get("aliases") or [])[:2000]))
                            c.execute("UPDATE counts SET n=n-1 WHERE kind=? AND domain=? AND ds=? AND status=?", (old[3], old[4], old[2], old[5]))
                            c.execute("INSERT INTO counts VALUES(?,?,?,?,1) ON CONFLICT DO UPDATE SET n=n+1", (kid, did, ds, old[5]))
                            w["p"].append((n["nid"], old[2], "superseded"))
                            out["superseded"] += 1
                            continue
                        if old is not None or n["nid"] in seen:
                            out["duplicates"] += 1
                            w["p"].append((n["nid"], ds, n["prov"]["record"]))
                            continue
                        seen.add(n["nid"])
                        kid, did = S.KIND_ID[n["kind"]], S.DOMAIN_ID[n.get("domain", "general")]
                        rows.append((n["nid"], kid, did, ds, S.LIC_ID[n["licence"]], 1, n["conf"], n["lang"], n["title"],
                                     n.get("subject"), n.get("created") or S.now(), pack_body(n),
                                     " ".join(n.get("aliases") or [])[:2000] if kid in S.SEARCHABLE else None))
                        cnt[(kid, did)] = cnt.get((kid, did), 0) + 1
                        adj += int(n.get("adj") or 0)
                    for r in rows:
                        cur = c.execute("INSERT INTO nodes(nid,kind,domain,ds,lic,status,conf,lang,title,subj,created,body)"
                                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", r[:12])
                        if r[12] is not None:
                            c.execute("INSERT INTO fts(rowid,title,aliases) VALUES(?,?,?)", (cur.lastrowid, r[8], r[12]))
                    for (kid, did), m in cnt.items():
                        c.execute("INSERT INTO counts VALUES(?,?,?,1,?) ON CONFLICT DO UPDATE SET n=n+excluded.n",
                                  (kid, did, ds, m))
                        out["by_kind"][S.KINDS[kid]] = out["by_kind"].get(S.KINDS[kid], 0) + m
                        out["by_domain"][S.DOMAINS[did]] = out["by_domain"].get(S.DOMAINS[did], 0) + m
                    out["inserted"] += len(rows)
                    if adj:
                        c.execute("INSERT INTO ecounts VALUES(?,?) ON CONFLICT DO UPDATE SET n=n+excluded.n", (ds, adj))
                        out["edges"] += adj
                if w["e"]:
                    counted = [e[:5] for e in w["e"] if e[5]]
                    other = [e[:5] for e in w["e"] if not e[5]]
                    before = c.total_changes
                    c.executemany("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)", counted)
                    added = c.total_changes - before
                    c.executemany("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)", other)
                    if added:
                        c.execute("INSERT INTO ecounts VALUES(?,?) ON CONFLICT DO UPDATE SET n=n+excluded.n", (ds, added))
                    out["edges"] += added
                for ih, nid, ident in w["x"]:
                    r = c.execute("SELECT nid FROM xref WHERE ident=?", (ih,)).fetchone()
                    if r is None:
                        c.execute("INSERT INTO xref VALUES(?,?)", (ih, nid))
                    elif r[0] != nid:
                        a, b = sorted((r[0], nid))
                        c.execute("INSERT OR IGNORE INTO dupcand VALUES(?,?,?)", (ident, a, b))
                        out["xref_conflicts"] += 1
                if w["p"]:
                    c.executemany("INSERT OR IGNORE INTO prov VALUES(?,?,?)", w["p"])
                c.execute("INSERT INTO applied VALUES(?,?)", (job, seq))
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:        # SQLite may already have rolled back (disk full, I/O error)
                    c.execute("ROLLBACK")
                raise
        return out

    def set_status(self, nid: bytes, status: int) -> bool:
        """Quarantine or restore one node; counters move in the same transaction."""
        c = self.conn(self.part(nid))
        c.execute("BEGIN IMMEDIATE")
        try:
            r = c.execute("SELECT kind, domain, ds, status FROM nodes WHERE nid=?", (nid,)).fetchone()
            if r is None or r[3] == status:
                c.execute("ROLLBACK")
                return False
            c.execute("UPDATE nodes SET status=? WHERE nid=?", (status, nid))
            c.execute("UPDATE counts SET n=n-1 WHERE kind=? AND domain=? AND ds=? AND status=?", (r[0], r[1], r[2], r[3]))
            c.execute("INSERT INTO counts VALUES(?,?,?,?,1) ON CONFLICT DO UPDATE SET n=n+1", (r[0], r[1], r[2], status))
            c.execute("COMMIT")
            return True
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ reading
    def _row(self, r, full: bool = True) -> dict:
        rid, nid, kind, domain, ds, lic, status, conf, lang, title, subj, created, body = r
        d = {"id": nid.hex(), "kind": S.KINDS[kind], "domain": S.DOMAINS[domain], "dataset": ds,
             "licence": S.LIC_NAME.get(lic, "unknown"), "status": S.STATUS_NAME.get(status, str(status)),
             "conf": conf, "lang": lang, "title": title, "created": created}
        if subj:
            d["subject"] = subj.hex()
        if full:
            d.update(unpack_body(body))
        return d

    COLS = "rid,nid,kind,domain,ds,lic,status,conf,lang,title,subj,created,body"

    def get(self, nid: bytes, full: bool = True) -> dict | None:
        r = self.conn(self.part(nid)).execute(f"SELECT {self.COLS} FROM nodes WHERE nid=?", (nid,)).fetchone()
        return self._row(r, full) if r else None

    def resolve(self, key: str) -> bytes | None:
        return self.resolve_nid(S.node_id(key))

    def resolve_nid(self, nid: bytes) -> bytes | None:
        """An id stored in a relationship may be an alias (e.g. wd:Q.. of a node keyed by its DOI)."""
        c = self.conn(self.part(nid))
        if c.execute("SELECT 1 FROM nodes WHERE nid=?", (nid,)).fetchone():
            return nid
        r = c.execute("SELECT nid FROM xref WHERE ident=?", (nid,)).fetchone()
        return r[0] if r else None

    def claims(self, subj: bytes, limit: int = 200, offset: int = 0) -> list[dict]:
        rows = self.conn(self.part(subj)).execute(
            f"SELECT {self.COLS} FROM nodes WHERE subj=? AND status=1 LIMIT ? OFFSET ?", (subj, limit, offset)).fetchall()
        return [self._row(r) for r in rows]

    def edges(self, nid: bytes, direction: str = "out", rel: str | None = None, limit: int = 200) -> list[dict]:
        d = 0 if direction == "out" else 1
        q = "SELECT rel, dst, via FROM edges WHERE src=? AND dir=?" + (" AND rel=?" if rel else "") + " LIMIT ?"
        args = (nid, d, rel, limit) if rel else (nid, d, limit)
        return [{"rel": r[0], "node": r[1].hex(), "via": r[2].hex() if r[2] else None}
                for r in self.conn(self.part(nid)).execute(q, args)]

    def search(self, q: str, limit: int = 20, kinds: list[str] | None = None, any_terms: bool = False,
               partitions: list[int] | None = None) -> list[dict]:
        fq = fts_query(q, any_terms)
        if not fq:
            return []
        hits = []
        kind_ids = {S.KIND_ID[k] for k in kinds} if kinds else None
        for i in (partitions if partitions is not None else range(self.P)):
            c = self.conn(i)
            try:
                rows = c.execute(f"SELECT n.{self.COLS.replace(',', ',n.')}, f.rank FROM (SELECT rowid, rank FROM fts "
                                 f"WHERE fts MATCH ? ORDER BY rank LIMIT ?) f JOIN nodes n ON n.rid=f.rowid "
                                 f"WHERE n.status=1", (fq, limit * 2)).fetchall()
            except sqlite3.OperationalError:
                continue
            for r in rows:
                if kind_ids and r[2] not in kind_ids:
                    continue
                d = self._row(r[:13])
                d["score"] = -float(r[13])
                hits.append(d)
        tl = q.strip().lower()
        hits.sort(key=lambda d: (d["title"].lower() != tl, -d["score"]))
        return hits[:limit]

    def title_lookup(self, name: str, limit: int = 8, kinds: list[str] | None = None) -> list[dict]:
        """Nodes whose title equals `name` (case-insensitive). Uses an anchored phrase on the title column without
        ranking, so the cost does not grow with how many other titles contain the words."""
        toks = [t for t in _fts_tok.findall(name.lower())][:12]
        if not toks:
            return []
        fq = 'title : ^"' + " ".join(toks) + '"'
        want = name.strip().lower()
        kind_ids = {S.KIND_ID[k] for k in kinds} if kinds else None
        out = []
        for i in range(self.P):
            try:
                rows = self.conn(i).execute(f"SELECT n.{self.COLS.replace(',', ',n.')} FROM (SELECT rowid FROM fts WHERE fts MATCH ? LIMIT 40) f "
                                            f"JOIN nodes n ON n.rid=f.rowid WHERE n.status=1", (fq,)).fetchall()
            except sqlite3.OperationalError:
                continue
            for r in rows:
                if r[9].lower() == want and (not kind_ids or r[2] in kind_ids):
                    out.append(self._row(r))
        return out[:limit * 4]          # every partition is read: the caller ranks the copies

    def sample(self, n: int, kind: str | None = None, seed: int | None = None) -> list[dict]:
        rnd = random.Random(seed)
        out, tries = [], 0
        while len(out) < n and tries < n * 20:
            tries += 1
            c = self.conn(rnd.randrange(self.P))
            mx = c.execute("SELECT max(rid) FROM nodes").fetchone()[0]
            if not mx:
                continue
            q = f"SELECT {self.COLS} FROM nodes WHERE rid>=? AND status=1" + (" AND kind=?" if kind else "") + " LIMIT 1"
            r = c.execute(q, (rnd.randint(1, mx), S.KIND_ID[kind]) if kind else (rnd.randint(1, mx),)).fetchone()
            if r:
                out.append(self._row(r))
        return out

    def counts(self) -> dict:
        tot = {"validated": 0, "quarantined": 0, "edges": 0, "by_kind": {}, "by_domain": {}, "by_dataset": {}}
        for i in range(self.P):
            c = self.conn(i)
            for kind, domain, ds, status, n in c.execute("SELECT kind, domain, ds, status, n FROM counts"):
                st = S.STATUS_NAME.get(status, "other")
                tot[st] = tot.get(st, 0) + n
                if status == 1:
                    tot["by_kind"][S.KINDS[kind]] = tot["by_kind"].get(S.KINDS[kind], 0) + n
                    tot["by_domain"][S.DOMAINS[domain]] = tot["by_domain"].get(S.DOMAINS[domain], 0) + n
                    tot["by_dataset"][ds] = tot["by_dataset"].get(ds, 0) + n
            for ds, n in c.execute("SELECT ds, n FROM ecounts"):
                tot["edges"] += n
        return tot

    def disk_bytes(self) -> int:
        tot = 0
        for p in self.root.glob("p*.sqlite*"):
            try:
                tot += p.stat().st_size
            except FileNotFoundError:
                pass
        return tot

    def integrity(self, partitions: list[int] | None = None, quick: bool = True) -> dict:
        """quick_check every partition and compare the counters with the stored rows."""
        res = {"ok": True, "partitions": {}}
        for i in (partitions if partitions is not None else range(self.P)):
            c = self.conn(i)
            chk = c.execute("PRAGMA quick_check" if quick else "PRAGMA integrity_check").fetchone()[0]
            c.execute("BEGIN")          # one snapshot: a batch committed between the two counts is not a mismatch
            try:
                stored = c.execute("SELECT count(*) FROM nodes").fetchone()[0]
                counted = c.execute("SELECT coalesce(sum(n),0) FROM counts").fetchone()[0]
            finally:
                c.execute("COMMIT")
            ok = chk == "ok" and stored == counted
            res["partitions"][i] = {"check": chk, "rows": stored, "counted": counted, "ok": ok}
            res["ok"] &= ok
        return res

