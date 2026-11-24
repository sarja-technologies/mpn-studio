"""Node networks: the projects of MPN Studio (one SQLite store each, see desktop/projects.py), plus the operations that
make them building blocks for models: create, import, duplicate, specialise, merge, compare, statistics, and the
optional learned router that a network can carry.

A network may carry a learned router (the Yaob N10K network does): router/<checkpoint id>/ inside the project folder,
recorded in project.json under references.router together with the encoder it was trained for. A model uses the
router only with that encoder; otherwise it routes by retrieval. Router files never change, so duplicates share them
by hard link instead of copying.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from .. import names
from ..desktop import projects
from ..desktop.projects import ProjectError
from ..store import netops


def _manifest(slug: str) -> dict:
    return projects.read_manifest(slug)


def router(slug: str) -> dict | None:
    r = (_manifest(slug).get("references") or {}).get("router")
    if not r:
        return None
    d = projects.project_dir(slug) / r["dir"]
    if not (d / "shards" / "shared.safetensors").exists():
        return None
    return {**r, "path": str(d)}


def info(slug: str) -> dict:
    m = _manifest(slug)
    s = projects.summarize(projects.store_path(slug))
    return {"id": slug, "name": names.network_display_name(m.get("name", slug)), "description": m.get("description", ""), "tags": m.get("tags", []),
            "created": m.get("created"), "modified": m.get("modified"), "origin": m.get("origin"), "router": router(slug), **s}


def list_networks() -> list[dict]:
    out = []
    for p in projects.list_projects():
        if p.get("damaged"):
            out.append(p)
            continue
        try:
            out.append(info(p["id"]))
        except ProjectError:
            continue
    return out


def create(name: str, description: str = "", tags: list[str] | None = None) -> dict:
    slug = projects.create(name, description)
    if tags:
        m = _manifest(slug)
        m["tags"] = [str(t).strip() for t in tags if str(t).strip()][:24]
        projects.write_manifest(slug, m)
    return info(slug)


def _link_tree(src: Path, dst: Path) -> None:
    """Copy a directory of immutable files by hard link where possible (same filesystem), else by copy."""
    for p in src.rglob("*"):
        t = dst / p.relative_to(src)
        if p.is_dir():
            t.mkdir(parents=True, exist_ok=True)
            continue
        t.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(p, t)
        except OSError:
            shutil.copy2(p, t)


def attach_router(slug: str, ck_dir: str | Path, encoder: dict | None) -> dict:
    """Store a router checkpoint with the network. `encoder`: {name, sha256} of the encoder GGUF it works with."""
    ck = Path(ck_dir)
    if not (ck / "shards" / "shared.safetensors").exists() or not (ck / "manifest.json").exists():
        raise ProjectError(f"{ck} is not a router checkpoint (shards/shared.safetensors and manifest.json are missing).")
    cid = json.loads((ck / "manifest.json").read_text()).get("id") or ck.name
    dst = projects.project_dir(slug) / "router" / cid
    if not dst.exists():
        _link_tree(ck, dst)
    m = _manifest(slug)
    m.setdefault("references", {})["router"] = {"id": cid, "dir": f"router/{cid}", "encoder": encoder}
    projects.write_manifest(slug, m)
    return router(slug)


def import_store(path: str | Path, name: str) -> dict:
    return info(projects.import_store_file(path, name))


def import_mpn_network(path: str | Path, name: str | None = None, progress=None) -> dict:
    """The network inside a .mpn model file, as a network of its own: the file is verified first, then the network member is
    extracted (hash-checked while copying) and adopted as a project. The core in the file is not touched or imported."""
    import tempfile
    from . import mpnfile
    f = mpnfile.MpnFile(path)
    net = (f.manifest.get("network") or {}).get("member")
    if not net or net not in f.members:
        raise ProjectError(f"{Path(path).name} has no network inside it.")
    rep = f.verify(progress) if f.size < (2 << 30) else None       # a complete model is large; each member is checked as it is read anyway
    if rep is not None and not rep["ok"]:
        raise ProjectError(f"{Path(path).name} is damaged: " + ", ".join(m["name"] for m in rep["members"] if not m["ok"]) + ". Nothing was imported.")
    from ..desktop import paths as dpaths
    dpaths.cache_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dpaths.cache_dir()) as td:
        out = f.extract(net, Path(td) / "network.sqlite", progress)
        slug = projects.import_store_file(out, name or f.manifest.get("name") or Path(path).stem, origin_detail=f".mpn file {Path(path).name}", kind="mpn-file")
    return info(slug)


def import_studio_payload(db: str | Path, checkpoint: str | Path | None, name: str, detail: str = "", encoder: dict | None = None) -> str:
    """The Studio payload of an official network release (`studio/network/yaob-n10k.sqlite`, optionally `studio/network/checkpoint/`): the same two
    pieces `import_yaob_release` takes from a Yaob folder. `encoder` {name, sha256} says which encoder the router was trained for."""
    slug = projects.import_store_file(db, name, origin_detail=detail, kind="release")
    if checkpoint and Path(checkpoint).is_dir():
        try:
            ename = json.loads((Path(checkpoint) / "manifest.json").read_text()).get("encoder", {}).get("name")
            attach_router(slug, checkpoint, {"name": encoder.get("name") or ename, "sha256": encoder.get("sha256")} if encoder is not None else None)
        except (ProjectError, OSError, ValueError):
            shutil.rmtree(projects.project_dir(slug), ignore_errors=True)
            raise
    return slug


def set_router_encoder(slug: str, encoder: dict) -> bool:
    """Record which encoder GGUF (name, sha256) a network's router works with. False if the network has no router."""
    m = _manifest(slug)
    r = (m.get("references") or {}).get("router")
    if not r:
        return False
    r["encoder"] = {"name": encoder.get("name"), "sha256": encoder.get("sha256")}
    projects.write_manifest(slug, m)
    return True


def import_yaob_release(folder: str | Path, name: str = "Yaob N10K-C4B") -> dict:
    """A Yaob release folder (network/yaob-n10k.sqlite + network/checkpoint + models/encoder/encoder.gguf)."""
    f = Path(folder)
    db, ck, enc = f / "network" / "yaob-n10k.sqlite", f / "network" / "checkpoint", f / "models" / "encoder" / "encoder.gguf"
    if not db.exists():
        raise ProjectError(f"{f} is not a Yaob release folder (network/yaob-n10k.sqlite is missing).")
    slug = projects.import_store_file(db, name, origin_detail=f"Yaob release folder {f.name}", kind="yaob-release")
    if ck.exists():
        from .cores import sha256_file
        encoder = {"name": json.loads((ck / "manifest.json").read_text()).get("encoder", {}).get("name"),
                   "sha256": sha256_file(enc) if enc.exists() else None}
        attach_router(slug, ck, encoder)
    return info(slug)


def _new_project_from_store(src_store: Path, name: str, origin: dict, extract_ids: list[int] | None = None, full_copy: bool = False,
                            description: str = "") -> str:
    slug = projects.unique_slug(name)
    d = projects.project_dir(slug)
    d.mkdir(parents=True)
    try:
        if full_copy:
            projects._copy_store(src_store, d / projects.STORE_FILE)
        else:
            netops.extract(src_store, d / projects.STORE_FILE, extract_ids, message=f"from {origin.get('of')}")
        m = projects._new_manifest(name, slug, origin, description)
        projects.write_manifest(slug, m)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return slug


def _carry_router(src_slug: str, dst_slug: str) -> None:
    r = router(src_slug)
    if r:
        attach_router(dst_slug, r["path"], r.get("encoder"))


def duplicate(slug: str, name: str, store=None) -> dict:
    """An independent copy: the whole store with its history (SQLite online backup), the router by hard link.
    Editing the copy never changes the original."""
    src = _manifest(slug)
    if store is not None:
        store.checkpoint_wal()
    new = _new_project_from_store(projects.store_path(slug), name, {"kind": "duplicate", "of": slug, "of_name": src.get("name"),
                                                                      "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                  full_copy=True, description=src.get("description", ""))
    m = _manifest(new)
    m["tags"] = src.get("tags", [])
    projects.write_manifest(new, m)
    _carry_router(slug, new)
    return info(new)


def specialise(slug: str, name: str, filt: dict | None = None, ids: list[int] | None = None, description: str = "") -> dict:
    """A new network holding only the nodes that match `filt` (or the given ids), with the relations among them."""
    from ..store.db import Store
    src = projects.store_path(slug)
    if ids is None:
        st = Store(src)
        try:
            ids = netops.select_ids(st, filt or {})
        finally:
            st.close()
    if not ids:
        raise ProjectError("No node matches; nothing to put in the new network.")
    new = _new_project_from_store(src, name, {"kind": "specialised", "of": slug, "of_name": _manifest(slug).get("name"), "filter": filt,
                                              "nodes": len(ids), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                                  extract_ids=ids, description=description)
    _carry_router(slug, new)
    return info(new)


def merge(target_slug: str, source_slug: str, filt: dict | None = None, on_conflict: str = "skip", target_store=None) -> dict:
    """Copy nodes of `source_slug` (all, or those matching `filt`) into `target_slug` as one revision."""
    from ..store.db import Store
    if target_slug == source_slug:
        raise ProjectError("Choose two different networks.")
    src = projects.store_path(source_slug)
    ids = None
    if filt:
        s = Store(src)
        try:
            ids = netops.select_ids(s, filt)
        finally:
            s.close()
    own = target_store is None
    st = target_store or Store(projects.store_path(target_slug))
    try:
        return netops.merge_into(st, src, ids, on_conflict=on_conflict)
    finally:
        if own:
            st.close()


def compare(a: str, b: str) -> dict:
    return netops.compare(projects.store_path(a), projects.store_path(b))


def stats(slug: str) -> dict:
    out = netops.stats(projects.store_path(slug))
    d = projects.project_dir(slug)
    out["folder_bytes"] = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
    out["router"] = router(slug)
    return out


def network_sha(slug: str) -> str:
    """Identity of the network's current content: the head revision and the file it lives in."""
    c = sqlite3.connect(f"file:{projects.store_path(slug)}?mode=ro", uri=True)
    try:
        head = c.execute("SELECT MAX(rev) FROM revisions").fetchone()[0]
    finally:
        c.close()
    return f"{_manifest(slug).get('id')}@{head}"
