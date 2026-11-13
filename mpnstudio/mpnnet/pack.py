# Vendored from the Yaob repository (mpn14b/pack.py at f8bd650, 2026-11-13). Read-only use: MPN Studio
# opens MPN network packs with it. Keep in step with the source; the mpn-network/1 JSON must stay identical.
"""MPN Pack: the universal, model-independent distribution format of a Modular Persistent Network.

```
<pack_id>-<version>/
  manifest.json        format mpn-pack/1: identity, schema version, counts, licences, datasets, files with SHA-256,
                       retrieval interface, disk and measured memory requirements
  parts/p00.sqlite …   node, relationship, alias, provenance and full-text shards (store.py layout)
  ATTRIBUTION.md       every dataset in the pack with its licence and required attribution
  SHA256SUMS
```

A pack needs no model and no Fylume service: any program that speaks `mpn-retrieval/1` (the `Federation` class
below, the `mpn-pack` CLI, or plain SQLite) can search and traverse it lazily from disk. Several packs can be mounted
at once; queries fan out over all of them and results carry the pack id, so nothing is physically merged.
"""
from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import sqlite3
import time
import zlib
from pathlib import Path

import orjson

from . import schema as S
from .store import DDL, Store, unpack_body

FORMAT = "mpn-pack/1"
RETRIEVAL = "mpn-retrieval/1"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(8 << 20):
            h.update(b)
    return h.hexdigest()


def build(cfg: dict | None, store: Store, ctl, pack_id: str, version: str, public_only: bool = True,
          domains: list[str] | None = None, kinds: list[str] | None = None, limit_per_partition: int | None = None,
          out_root: str | Path | None = None, name: str | None = None, description: str = "") -> dict:
    """Snapshot (part of) a store into a pack. Reads a consistent snapshot of each partition while writers run."""
    from . import config
    out_root = Path(out_root) if out_root else config.path(cfg, "packs")
    final = out_root / f"{pack_id}-{version}"
    tmp = out_root / f".{pack_id}-{version}.building"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "parts").mkdir(parents=True)
    conds = ["n.status=1"]
    if public_only:
        conds.append(f"n.lic IN ({','.join(str(i) for i in sorted(S.PUBLIC_LIC_IDS))})")
    if domains:
        conds.append(f"n.domain IN ({','.join(str(S.DOMAIN_ID[d]) for d in domains)})")
    if kinds:
        conds.append(f"n.kind IN ({','.join(str(S.KIND_ID[k]) for k in kinds)})")
    where = " AND ".join(conds)
    filtered = bool(domains or kinds or limit_per_partition)
    t0 = time.time()
    for i in range(store.P):
        dst = tmp / "parts" / f"p{i:02d}.sqlite"
        d = sqlite3.connect(dst, isolation_level=None)
        d.executescript(DDL)
        d.execute("PRAGMA journal_mode=DELETE")
        d.execute("ATTACH DATABASE ? AS s", (f"file:{store.path(i)}?mode=ro",))
        d.execute("BEGIN")
        lim = f" LIMIT {int(limit_per_partition)}" if limit_per_partition else ""
        d.execute(f"INSERT INTO nodes SELECT n.* FROM s.nodes n WHERE {where} ORDER BY n.rid{lim}")
        rows = d.execute(f"SELECT rid, kind, title, body FROM nodes").fetchall()
        fts = []
        for rid, kind, title, body in rows:
            if kind in S.SEARCHABLE:
                al = orjson.loads(zlib.decompress(body)).get("aliases") or []
                fts.append((rid, title, " ".join(al)[:2000]))
        d.executemany("INSERT INTO fts(rowid,title,aliases) VALUES(?,?,?)", fts)
        if filtered:
            d.execute("INSERT INTO edges SELECT e.* FROM s.edges e WHERE e.src IN (SELECT nid FROM nodes)")
            d.execute("INSERT INTO prov SELECT p.* FROM s.prov p WHERE p.nid IN (SELECT nid FROM nodes)")
        else:
            d.execute("INSERT INTO edges SELECT * FROM s.edges")
            d.execute("INSERT INTO prov SELECT * FROM s.prov")
        d.execute("INSERT INTO xref SELECT * FROM s.xref")
        d.execute("INSERT INTO counts SELECT kind, domain, ds, status, count(*) FROM nodes GROUP BY kind, domain, ds, status")
        d.execute("INSERT INTO ecounts SELECT 0, (SELECT count(*) FROM edges WHERE dir=0) + "
                  "(SELECT count(*) FROM edges WHERE dir=1 AND (rel GLOB 'P[0-9]*' OR rel='author'))")
        d.execute("INSERT OR REPLACE INTO meta VALUES('pack_id', ?)", (pack_id,))
        d.execute("INSERT OR REPLACE INTO meta VALUES('format', 'mpn-store/1')")
        d.execute("COMMIT")
        d.execute("DETACH DATABASE s")
        d.execute("VACUUM")
        d.close()
    ps = Store(tmp / "parts", store.P, readonly=True)
    c = ps.counts()
    ds_rows = {r["id"]: r for r in (ctl.q("SELECT * FROM datasets") if ctl else [])}
    lic_counts: dict[str, int] = {}
    for i in range(store.P):
        for lic, n in ps.conn(i).execute("SELECT lic, count(*) FROM nodes GROUP BY lic"):
            lic_counts[S.LIC_NAME.get(lic, "unknown")] = lic_counts.get(S.LIC_NAME.get(lic, "unknown"), 0) + n
    datasets = [{"slug": ds_rows[k]["slug"], "name": ds_rows[k]["name"], "publisher": ds_rows[k]["publisher"],
                 "url": ds_rows[k]["url"], "version": ds_rows[k]["version"], "licence": ds_rows[k]["licence"],
                 "attribution": ds_rows[k]["attribution"], "nodes": n}
                for k, n in sorted(c["by_dataset"].items()) if k in ds_rows]
    # measured reader footprint: open the pack cold and run the known-item benchmark
    from .milestones import retrieval_bench
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    bench = retrieval_bench(ps, 30, seed=1)
    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    ps.close()
    pack_lic = "CC-BY-SA-4.0" if any(S.LICENCES.get(k, {}).get("share_alike") for k in lic_counts) else \
        ("CC-BY-4.0" if any(S.LICENCES.get(k, {}).get("attribution") for k in lic_counts) else "CC0-1.0")
    attr = ["# Attribution", "", f"Pack `{pack_id}` {version}, built by Fylume from the datasets below.", "",
            f"Database licence: **{pack_lic}** (the most restrictive licence of its contents). Parts from CC0 sources "
            "remain usable under CC0 on their own.", "", "| Dataset | Publisher | Version | Licence | Nodes | Attribution |",
            "|---|---|---|---|---|---|"]
    for d_ in datasets:
        attr.append(f"| {d_['name']} | {d_['publisher']} | {d_['version'] or ''} | {d_['licence']} | {d_['nodes']:,} | {d_['attribution']} |")
    (tmp / "ATTRIBUTION.md").write_text("\n".join(attr) + "\n")
    files = []
    for p in sorted(tmp.rglob("*")):
        if p.is_file():
            files.append({"path": str(p.relative_to(tmp)), "bytes": p.stat().st_size, "sha256": sha256(p)})
    total_bytes = sum(f["bytes"] for f in files)
    manifest = {
        "format": FORMAT, "pack_id": pack_id, "name": name or pack_id, "version": version, "description": description,
        "schema": {"node": S.FORMAT, "version": S.SCHEMA_VERSION, "cells": "3CN (knowledge, thinking, reasoning)"},
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "partitions": store.P,
        "counts": {"nodes": c["validated"], "by_kind": c["by_kind"], "by_domain": c["by_domain"],
                   "relationships": c["edges"]},
        "selection": {"public_only": public_only, "domains": domains, "kinds": kinds, "limit_per_partition": limit_per_partition},
        "licence": pack_lic, "licences": lic_counts, "datasets": datasets,
        "retrieval": {"interface": RETRIEVAL, "full_text": "SQLite FTS5 (titles, aliases)", "graph": "edges + xref tables",
                      "embeddings": None, "benchmark": bench},
        "requirements": {"disk_bytes": total_bytes, "measured_reader_peak_rss_kib": rss1, "measured_rss_growth_kib": rss1 - rss0,
                         "recommended_ram": "1 GiB (bounded page cache: 8 MiB per partition by default)",
                         "python": ">=3.10 with orjson, or any SQLite 3.43+ client"},
        "compatibility": {"mpn_studio": "mount through /api/packs or import a selection as yaob-mpn-package/1",
                          "yaob_next": "mounted through YAOB_MPN_PACKS (federated retrieval)", "cores": "any"},
        "files": files, "build_seconds": round(time.time() - t0, 1),
    }
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (tmp / "SHA256SUMS").write_text("".join(f"{f['sha256']}  {f['path']}\n" for f in files)
                                    + f"{sha256(tmp / 'manifest.json')}  manifest.json\n")
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    if ctl:
        ctl.event("info", "pack", f"built pack {pack_id} {version}: {c['validated']:,} nodes, {total_bytes / 1e6:.1f} MB", {"path": str(final)})
    return {"path": str(final), "manifest": manifest}


def verify(path: str | Path, deep: bool = True) -> dict:
    path = Path(path)
    m = json.loads((path / "manifest.json").read_text())
    if m.get("format") != FORMAT:
        return {"ok": False, "error": f"not an {FORMAT} pack"}
    bad = []
    for f in m["files"]:
        p = path / f["path"]
        if not p.exists() or p.stat().st_size != f["bytes"]:
            bad.append({"path": f["path"], "error": "missing or wrong size"})
        elif deep and sha256(p) != f["sha256"]:
            bad.append({"path": f["path"], "error": "checksum mismatch"})
    res = {"ok": not bad, "pack_id": m["pack_id"], "version": m["version"], "files": len(m["files"]), "bad": bad}
    if not bad:
        st = Store(path / "parts", m["partitions"], readonly=True)
        integ = st.integrity()
        cnt = st.counts()
        res["integrity"] = integ["ok"]
        res["nodes"] = cnt["validated"]
        res["ok"] = integ["ok"] and cnt["validated"] == m["counts"]["nodes"]
        st.close()
    return res


# ---------------------------------------------------------------------------- mounting
def mounts_file() -> Path:
    return Path(os.environ.get("MPN_MOUNTS", Path.home() / ".config" / "mpn" / "mounts.json"))


def mounts() -> list[dict]:
    f = mounts_file()
    return json.loads(f.read_text()).get("packs", []) if f.exists() else []


def _save(packs: list[dict]):
    f = mounts_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"format": "mpn-mounts/1", "packs": packs}, indent=1))
    os.replace(tmp, f)


def mount(path: str | Path, verify_files: bool = True, live: bool = False) -> dict:
    path = Path(path).resolve()
    if live:      # a live, still-growing store (no manifest): mounted read-only
        meta = sqlite3.connect(f"file:{path / 'p00.sqlite'}?mode=ro", uri=True).execute("SELECT v FROM meta WHERE k='partitions'").fetchone()
        entry = {"pack_id": "mpn14b-live", "version": "live", "path": str(path), "parts": str(path),
                 "partitions": int(meta[0]), "live": True, "mounted_at": time.time()}
    else:
        if verify_files:
            v = verify(path, deep=False)
            if not v["ok"]:
                raise ValueError(f"pack failed verification: {v}")
        m = json.loads((path / "manifest.json").read_text())
        entry = {"pack_id": m["pack_id"], "version": m["version"], "path": str(path), "parts": str(path / "parts"),
                 "partitions": m["partitions"], "nodes": m["counts"]["nodes"], "licence": m["licence"], "mounted_at": time.time()}
    packs = [p for p in mounts() if p["pack_id"] != entry["pack_id"]] + [entry]
    _save(packs)
    return entry


def unmount(pack_id: str) -> bool:
    packs = mounts()
    rest = [p for p in packs if p["pack_id"] != pack_id]
    _save(rest)
    return len(rest) != len(packs)


# ---------------------------------------------------------------------------- federated retrieval
_LAB = {"t": 0.0, "p": {}, "i": {}}


def _label_cache() -> dict:
    """Optional local label files (Wikidata property and item labels) written by the builder."""
    if time.time() - _LAB["t"] > 600:
        _LAB["t"] = time.time()
        try:
            from . import config
            d = config.path(config.load(), "labels")
            for k, name in (("p", "properties.json"), ("i", "items.json")):
                f = d / name
                if f.exists():
                    _LAB[k] = json.loads(f.read_text())
        except Exception:
            pass
    return _LAB


def _is_wid(v) -> bool:
    return isinstance(v, str) and len(v) > 1 and v[0] in "QPL" and v[1:].isdigit()


class Federation:
    """mpn-retrieval/1. Search, read and traverse every mounted pack lazily from disk."""

    def __init__(self, packs: list[dict] | None = None, cache_kib: int = 4096):
        self.packs = []
        for p in (packs if packs is not None else mounts()):
            try:
                self.packs.append((p, Store(p["parts"], p["partitions"], readonly=True, cache_kib=cache_kib)))
            except Exception:
                continue

    def info(self) -> list[dict]:
        out = []
        for p, st in self.packs:
            c = st.counts()
            out.append({k: p.get(k) for k in ("pack_id", "version", "path", "live", "licence")} |
                       {"nodes": c["validated"], "relationships": c["edges"], "by_kind": c["by_kind"], "by_domain": c["by_domain"]})
        return out

    def search(self, q: str, limit: int = 10, kinds: list[str] | None = None, any_terms: bool = False) -> list[dict]:
        hits = []
        for p, st in self.packs:
            for h in st.search(q, limit, kinds, any_terms):
                h["pack"] = p["pack_id"]
                hits.append(self.render(h))
        tl = q.strip().lower()
        hits.sort(key=lambda d: (d["title"].lower() != tl, -d.get("score", 0)))
        return hits[:limit]

    def title_lookup(self, name: str, limit: int = 8, kinds: list[str] | None = None) -> list[dict]:
        out = []
        for p, st in self.packs:
            for h in st.title_lookup(name, limit, kinds):
                h["pack"] = p["pack_id"]
                out.append(h)
        return out          # unranked: callers rank the copies

    def get(self, nid: str | bytes, render: bool = True) -> dict | None:
        b = bytes.fromhex(nid) if isinstance(nid, str) else nid
        for p, st in self.packs:
            r = st.resolve_nid(b)
            if r:
                d = st.get(r)
                if d:
                    d["pack"] = p["pack_id"]
                    return self.render(d) if render else d
        return None

    def _vlabel(self, vid: str) -> str | None:
        lab = _label_cache()
        if vid[0] == "P":
            v = lab["p"].get(vid)
            if v:
                return v.get("label") if isinstance(v, dict) else v
        if vid in lab["i"]:
            return lab["i"][vid]
        cache = self.__dict__.setdefault("_lc", {})
        if vid not in cache:
            d = self.get(S.node_id(("wd:" if vid[0] in "QP" else "wdl:") + vid.split("-")[0]), render=False)
            cache[vid] = d["title"] if d else None
            if len(cache) > 50000:
                cache.clear()
        return cache[vid]

    def render(self, d: dict) -> dict:
        """Readable values: entity ids in facts become labels (from the packs or the local label files)."""
        k = (d.get("cells") or {}).get("knowledge") or {}
        changed = False
        for f in k.get("facts") or []:
            pid, vid = f.get("pid"), f.get("value_id")
            if pid and f.get("property") == pid:
                lab = self._vlabel(pid)
                if lab:
                    f["property"] = lab
                    changed = True
            if vid and f.get("value") == vid:
                lab = self._vlabel(vid)
                if lab:
                    f["value"] = lab
                    changed = True
        # class names, referral titles and relation ids written before their labels were known
        th = (d.get("cells") or {}).get("thinking") or {}
        if th.get("entity_class"):
            th["entity_class"] = [(self._vlabel(c) or c) if _is_wid(c) else c for c in th["entity_class"]]
        for r in ((d.get("cells") or {}).get("reasoning") or {}).get("referrals") or []:
            node = str(r.get("node", ""))
            wid = node.split(":", 1)[1] if node.startswith("wd:") else None
            if wid and r.get("title") == wid:
                r["title"] = self._vlabel(wid) or wid
            if _is_wid(r.get("relation", "")):
                r["relation"] = self._vlabel(r["relation"]) or r["relation"]
        if changed and d.get("kind") == "claim" and k.get("facts"):
            f = k["facts"][0]
            subj = d["title"].split(" — ", 1)[0]
            d["title"] = f"{subj} — {f['property']}: {f['value']}"
        if changed and d.get("kind") == "lexeme":
            fl = {f.get("property"): f.get("value") for f in k.get("facts") or []}
            d["summary"] = f"{fl.get('language', '')} {fl.get('lexical category', '')}".strip()
        return d

    def resolve(self, key: str) -> dict | None:
        return self.get(S.node_id(key))

    def label(self, key_or_id: str) -> str | None:
        d = self.resolve(key_or_id) if ":" in key_or_id else self.get(key_or_id)
        return d["title"] if d else None

    def claims(self, nid: str, limit: int = 100) -> list[dict]:
        b = bytes.fromhex(nid)
        out = []
        for p, st in self.packs:
            for c in st.claims(b, limit):
                c["pack"] = p["pack_id"]
                out.append(self.render(c))
        return out[:limit]

    def neighbors(self, nid: str, direction: str = "out", rel: str | None = None, limit: int = 50) -> list[dict]:
        b = bytes.fromhex(nid)
        node = self.get(b)
        keys = [b]
        if node:      # reverse rows may be keyed by an alias (wd:Q…, openalex:…) of this node
            for k, v in (node.get("ids") or {}).items():
                if k in ("wikidata", "openalex", "doi", "orcid", "ror", "issn_l"):
                    keys.append(S.node_id(f"{'wd' if k == 'wikidata' else k}:{v}"))
        out = []
        for p, st in self.packs:
            for k in dict.fromkeys(keys):
                for e in st.edges(k, direction, rel, limit):
                    if _is_wid(e.get("rel", "")) and e["rel"][0] == "P":
                        e["rel_label"] = self._vlabel(e["rel"])
                    out.append({**e, "pack": p["pack_id"]})
        return out[:limit]

    def evidence(self, question: str, k: int = 6, claims_per_node: int = 8) -> list[dict]:
        """Bounded evidence for a cognitive core: top nodes for the question with their best claims, as text."""
        hits = self.search(question, k, any_terms=False) or self.search(question, k, any_terms=True)
        out = []
        for h in hits:
            facts = [f"{f.get('property')}: {f.get('value')}" for f in ((h.get("cells") or {}).get("knowledge") or {}).get("facts", [])[:6]]
            stm = [s.get("text") for s in ((h.get("cells") or {}).get("knowledge") or {}).get("statements", [])[:3] if s.get("text")]
            cl = [((c.get("cells") or {}).get("knowledge") or {}).get("facts", [{}])[0] for c in self.claims(h["id"], claims_per_node)]
            out.append({"id": h["id"], "pack": h["pack"], "title": h["title"], "kind": h["kind"], "domain": h["domain"],
                        "summary": h.get("summary"), "statements": stm, "facts": facts,
                        "claims": [f"{c.get('property')}: {c.get('value')}" for c in cl if c],
                        "source": (h.get("prov") or {}).get("url"), "dataset": (h.get("prov") or {}).get("dataset"),
                        "licence": h["licence"], "conf": h["conf"]})
        return out
