# Vendored from the Yaob repository (mpn14b/netapi.py at f8bd650, 2026-11-13). Read-only use: MPN Studio
# opens MPN network packs with it. Keep in step with the source; the mpn-network/1 JSON must stay identical.
"""Read-only network queries over a whole MPN (`mpn-network/1`): overview, search, browse, node inspection,
relationships and graph neighbourhoods, always paged and bounded.

The same class serves the MPN-14B dashboard API (`/api/network/v1/...`, remote mode for MPN Studio on the LAN) and MPN
Studio's own backend when it opens a pack from disk (local mode, no Spark, no network): both return identical JSON.
Nothing here loads more than one page of nodes: search uses each partition's full-text index, browsing walks primary
keys with a cursor, and relationship lists are cut at the page size.
"""
from __future__ import annotations

import re
import time

from . import schema as S

HEX = re.compile(r"^[0-9a-f]{32}$")
WID = re.compile(r"^[QPL]\d+(-[SF]\d+)?$", re.I)
API = "mpn-network/1"
KIND_ORDER = {"concept": 0, "person": 0, "organisation": 0, "migrated": 0, "procedure": 0, "topic": 1, "venue": 1, "lexeme": 2,
              "sense": 2, "work": 3}
DS_ORDER = {"yaob-n10k": 3, "wikidata": 2, "wikidata-lexemes": 1}


class NetworkReader:
    def __init__(self, fed, info: dict | None = None):
        """fed: a pack.Federation over the network's packs. info: optional facts the store itself does not hold
        (dataset names and licences, network name and version, last validation, location)."""
        self.fed = fed
        self.info = info or {}
        self._ov = {"t": 0, "d": None}
        self.ds = {int(k): v for k, v in (self.info.get("datasets") or {}).items()}

    # ------------------------------------------------------------------ overview
    def overview(self, max_age_s: float = 60) -> dict:
        if self._ov["d"] and time.time() - self._ov["t"] < max_age_s:
            return self._ov["d"]
        tot = {"validated": 0, "quarantined": 0, "relationships": 0, "by_kind": {}, "by_domain": {}, "by_dataset": {}}
        packs, size = [], 0
        for p, st in self.fed.packs:
            c = st.counts()
            for k in ("by_kind", "by_domain", "by_dataset"):
                for kk, v in c[k].items():
                    tot[k][kk] = tot[k].get(kk, 0) + v
            tot["validated"] += c["validated"]
            tot["quarantined"] += c.get("quarantined", 0)
            tot["relationships"] += c["edges"]
            psize = sum(st.path(i).stat().st_size for i in range(st.P) if st.path(i).exists())
            size += psize
            packs.append({"pack_id": p.get("pack_id"), "version": p.get("version"), "partitions": st.P, "bytes": psize,
                          "validated": c["validated"], "live": bool(p.get("live"))})
        datasets = []
        for k, n in sorted(tot["by_dataset"].items(), key=lambda kv: -kv[1]):
            d = self.ds.get(int(k), {})
            datasets.append({"id": int(k), "slug": d.get("slug", str(k)), "name": d.get("name", str(k)), "licence": d.get("licence"),
                             "nodes": n})
        out = {"api": API, "name": self.info.get("name", "MPN network"), "version": self.info.get("version"),
               "location": self.info.get("location", "local"), "read_only": True,
               "validated_nodes": tot["validated"], "quarantined_nodes": tot["quarantined"], "relationships": tot["relationships"],
               "domains": sorted(({"domain": k, "nodes": v} for k, v in tot["by_domain"].items()), key=lambda x: -x["nodes"]),
               "kinds": sorted(({"kind": k, "nodes": v} for k, v in tot["by_kind"].items()), key=lambda x: -x["nodes"]),
               "datasets": datasets, "packs": packs, "storage_bytes": size,
               "indexes": {"full_text": "built (SQLite FTS5 over titles and aliases, every partition)",
                           "identifiers": "built (xref)", "vectors": "none (retrieval is lexical)"},
               "last_validation": self.info.get("last_validation"), "construction": self.info.get("construction"),
               "target_nodes": self.info.get("target_nodes"), "ts": time.time()}
        self._ov = {"t": time.time(), "d": out}
        return out

    # ------------------------------------------------------------------ helpers
    def _card(self, d: dict) -> dict:
        ds = self.ds.get(int(d.get("dataset") or 0), {})
        return {"id": d["id"], "title": d["title"], "kind": d["kind"], "domain": d["domain"], "subdomain": d.get("subdomain"),
                "summary": (d.get("summary") or "")[:300] or None, "dataset": ds.get("slug", d.get("dataset")),
                "licence": d.get("licence"), "status": d.get("status"), "conf": d.get("conf"), "pack": d.get("pack")}

    def _filters(self, kind: str | None, domain: str | None, dataset: str | None):
        if kind and kind not in S.KIND_ID:
            raise ValueError("unknown kind")
        if domain and domain not in S.DOMAIN_ID:
            raise ValueError("unknown domain")
        ds_id = None
        if dataset:
            ds_id = next((k for k, v in self.ds.items() if v.get("slug") == dataset or str(k) == dataset), None)
            if ds_id is None:
                raise ValueError("unknown dataset")
        return ds_id

    def lookup_id(self, q: str) -> dict | None:
        """A node id (32 hex), a Wikidata id (Q42, P31, L7), or a source key (doi:10…, openalex:W…, orcid:…, wd:Q42)."""
        s = q.strip()
        if HEX.match(s.lower()):
            return self.fed.get(s.lower())
        if WID.match(s):
            s = s.upper()
            return self.fed.resolve(("wdl:" if s[0] == "L" else "wd:") + s)
        if re.match(r"^(wd|wdl|doi|openalex|orcid|ror|issn_l|n10k|yaob):", s, re.I):
            k, v = s.split(":", 1)
            return self.fed.resolve(f"{k.lower()}:{v}")
        if s.lower().startswith("10.") and "/" in s:
            return self.fed.resolve("doi:" + s.lower())
        return None

    # ------------------------------------------------------------------ search and browse
    def search(self, q: str, kind: str | None = None, domain: str | None = None, dataset: str | None = None,
               page: int = 0, limit: int = 25) -> dict:
        t0 = time.time()
        limit, page = max(1, min(int(limit), 100)), max(0, min(int(page), 40))
        ds_id = self._filters(kind, domain, dataset)
        exact = self.lookup_id(q)
        if exact:
            return {"query": q, "mode": "id", "page": 0, "results": [self._card(exact)], "more": False,
                    "ms": round((time.time() - t0) * 1000, 1)}
        want = (page + 1) * limit
        filt = bool(domain or ds_id is not None)
        hits = self.fed.search(q, want * (4 if filt else 1) + 1, [kind] if kind else None)
        if not hits:
            hits = self.fed.search(q, want * (4 if filt else 1) + 1, [kind] if kind else None, any_terms=True)
        # same title: entities before the works that carry their name, curated data before bulk data
        ql = q.strip().lower()
        hits.sort(key=lambda h: (h["title"].lower() != ql, KIND_ORDER.get(h["kind"], 3), -DS_ORDER.get(self.ds.get(int(h.get("dataset") or 0), {}).get("slug"), 0)))
        if domain:
            hits = [h for h in hits if h["domain"] == domain]
        if ds_id is not None:
            hits = [h for h in hits if int(h.get("dataset") or 0) == ds_id]
        sl = hits[page * limit:(page + 1) * limit]
        return {"query": q, "mode": "text", "page": page, "limit": limit, "results": [self._card(h) for h in sl],
                "more": len(hits) > (page + 1) * limit, "ms": round((time.time() - t0) * 1000, 1)}

    def browse(self, domain: str | None = None, kind: str | None = None, dataset: str | None = None,
               cursor: str | None = None, limit: int = 25, boundary: bool = False) -> dict:
        """Walk the network in storage order with a cursor ("partition:rid"), filtered by domain, kind and dataset.
        Each call reads at most a bounded window of rows, so a rare filter returns fewer rows and a cursor to continue."""
        t0 = time.time()
        limit = max(1, min(int(limit), 100))
        ds_id = self._filters(kind, domain, dataset)
        conds, args = ["status=1"], []
        if domain:
            conds.append("domain=?")
            args.append(S.DOMAIN_ID[domain])
        if kind:
            conds.append("kind=?")
            args.append(S.KIND_ID[kind])
        elif not domain:
            conds.append("kind!=?")             # claims are reached from their subject
            args.append(S.KIND_ID["claim"])
        if ds_id is not None:
            conds.append("ds=?")
            args.append(ds_id)
        pack_i, part, rid = 0, 0, 0
        if cursor:
            a, b, c = (cursor.split(":") + ["0", "0"])[:3]
            pack_i, part, rid = int(a), int(b), int(c)
        out, scanned, window = [], 0, 400_000
        while pack_i < len(self.fed.packs) and len(out) < limit and scanned < 3 * window:
            p, st = self.fed.packs[pack_i]
            if part >= st.P:
                pack_i, part, rid = pack_i + 1, 0, 0
                continue
            c = st.conn(part)
            want = limit - len(out)
            rows = c.execute(f"SELECT {st.COLS} FROM nodes WHERE rid>? AND rid<=? AND {' AND '.join(conds)} ORDER BY rid LIMIT ?",
                             (rid, rid + window, *args, want)).fetchall()
            mx = c.execute("SELECT max(rid) FROM nodes").fetchone()[0] or 0
            scanned += window
            for r in rows:
                d = st._row(r)
                d["pack"] = p.get("pack_id")
                if boundary and not self._crosses(d):
                    continue
                out.append(self._card(self.fed.render(d)))
            # a full page: continue after its last row; otherwise this window is exhausted
            rid = rows[-1][0] if len(rows) == want else rid + window
            if rid >= mx:
                part, rid = part + 1, 0
        nxt = f"{pack_i}:{part}:{rid}" if pack_i < len(self.fed.packs) else None
        return {"results": out, "cursor": nxt, "ms": round((time.time() - t0) * 1000, 1),
                "filters": {"domain": domain, "kind": kind, "dataset": dataset, "boundary": boundary}}

    def _crosses(self, d: dict) -> bool:
        for e in self.fed.neighbors(d["id"], "out", None, 12):
            t = self.fed.get(e["node"], render=False)
            if t and t["domain"] != d["domain"]:
                return True
        return False

    # ------------------------------------------------------------------ one node
    def _rels(self, d: dict, direction: str, rel: str | None, offset: int, limit: int) -> tuple[list[dict], bool]:
        es = self.fed.neighbors(d["id"], direction, rel, offset + limit + 1)
        page = es[offset:offset + limit]
        out = []
        for e in page:
            t = self.fed.get(e["node"], render=False)
            out.append({"rel": (e.get("rel_label") or e["rel"]).replace("_", " "), "rel_id": e["rel"], "node": e["node"],
                        "via": e.get("via"), "title": t["title"] if t else None, "domain": t["domain"] if t else None,
                        "kind": t["kind"] if t else None, "cross_domain": bool(t and t["domain"] != d["domain"]),
                        "present": t is not None})
        return out, len(es) > offset + limit

    def node(self, nid: str, rel_limit: int = 30, claim_limit: int = 40) -> dict | None:
        d = self.lookup_id(nid) if not HEX.match(nid) else self.fed.get(nid)
        if not d:
            return None
        out_r, more_out = self._rels(d, "out", None, 0, rel_limit)
        in_r, more_in = self._rels(d, "in", None, 0, rel_limit)
        claims = self.fed.claims(d["id"], claim_limit + 1)
        ds = self.ds.get(int(d.get("dataset") or 0), {})
        cells = d.get("cells") or {}
        return {"node": {**self._card(d), "aliases": (d.get("aliases") or [])[:40], "key": d.get("key"), "ids": d.get("ids"),
                         "created": d.get("created"), "lang": d.get("lang"), "subject": d.get("subject"),
                         "cells": {"knowledge": cells.get("knowledge"), "thinking": cells.get("thinking"),
                                   "reasoning": cells.get("reasoning")},
                         "provenance": {**(d.get("prov") or {}), "dataset_name": ds.get("name"), "dataset_licence": ds.get("licence"),
                                        "attribution": ds.get("attribution")},
                         "validation": {"status": d.get("status"), "confidence": d.get("conf"), "method": d.get("method"),
                                        "evidence": d.get("evidence"),
                                        "checks": ((cells.get("reasoning") or {}).get("checks"))}},
                "claims": [{"id": c["id"], "property": (((c.get("cells") or {}).get("knowledge") or {}).get("facts") or [{}])[0].get("property"),
                            "value": (((c.get("cells") or {}).get("knowledge") or {}).get("facts") or [{}])[0].get("value"),
                            "conf": c.get("conf"), "basis": (c.get("evidence") or {}).get("basis")} for c in claims[:claim_limit]],
                "more_claims": len(claims) > claim_limit,
                "out": out_r, "more_out": more_out, "in": in_r, "more_in": more_in,
                "cross_domain": sorted({r["domain"] for r in out_r + in_r if r["cross_domain"] and r["domain"]})}

    def relationships(self, nid: str, direction: str = "out", rel: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        d = self.fed.get(nid)
        if not d:
            return {"results": [], "more": False}
        r, more = self._rels(d, "in" if direction == "in" else "out", rel, max(0, int(offset)), max(1, min(int(limit), 200)))
        return {"results": r, "more": more, "offset": offset}

    def graph(self, nid: str, limit: int = 40) -> dict:
        """The node, its neighbours (out and in, up to `limit`), and the relationships among them, for drawing."""
        d = self.fed.get(nid)
        if not d:
            return {"nodes": [], "edges": []}
        half = max(4, min(int(limit), 120)) // 2
        nodes = {d["id"]: {"id": d["id"], "title": d["title"], "domain": d["domain"], "kind": d["kind"], "center": True}}
        edges = []
        for direction in ("out", "in"):
            rels, _ = self._rels(d, direction, None, 0, half)
            for r in rels:
                if not r["present"]:
                    continue
                nodes.setdefault(r["node"], {"id": r["node"], "title": r["title"], "domain": r["domain"], "kind": r["kind"]})
                edges.append({"src": d["id"] if direction == "out" else r["node"], "dst": r["node"] if direction == "out" else d["id"],
                              "rel": r["rel"], "cross_domain": r["cross_domain"]})
        ids = set(nodes)
        for n in list(nodes)[1:25]:            # links between the neighbours themselves
            for e in self.fed.neighbors(n, "out", None, 20):
                if e["node"] in ids and e["node"] != n:
                    edges.append({"src": n, "dst": e["node"], "rel": (e.get("rel_label") or e["rel"]).replace("_", " "),
                                  "cross_domain": nodes[n]["domain"] != nodes[e["node"]]["domain"]})
        return {"center": d["id"], "nodes": list(nodes.values()), "edges": edges}
