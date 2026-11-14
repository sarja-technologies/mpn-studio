"""`.mpnfork` files: a fork's changes as one portable, checksummed file (`mpn-fork/1`).

    manifest.json   format, name, the base network's identity and sources, head revision, change counts, SHA-256 of
                    overlay.sqlite
    overlay.sqlite  the fork's change file (a consistent copy)

The base network is never inside: a fork file is kilobytes to megabytes whatever the size of the network. Opening one
on another computer needs that network (a linked project with the same identity, or one of the recorded sources).
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from ..desktop import projects as P
from .overlay import Overlay

FORMAT = "mpn-fork/1"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def export(slug: str, dest: str | Path) -> dict:
    m = P.read_manifest(slug)
    if P.kind_of(m) != "fork":
        raise P.ProjectError("Only a fork's changes are exported as a fork file.")
    dest = Path(dest)
    if dest.suffix != ".mpnfork":
        dest = dest.with_name(dest.name + ".mpnfork")
    if dest.exists():
        raise P.ProjectError(f"{dest.name} already exists. Choose another name.", 409)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        snap = Path(tmp) / "overlay.sqlite"
        c = sqlite3.connect(P.overlay_path(slug), timeout=30)
        try:
            c.execute("VACUUM INTO ?", (str(snap),))
        finally:
            c.close()
        ov = P.summarize_overlay(snap)
        man = {"format": FORMAT, "name": m.get("name"), "created": P.now(), "studio_project": m.get("id"),
               "network": {"identity": (m.get("network") or {}).get("identity"), "sources": (m.get("network") or {}).get("sources")},
               "head": ov.get("head"), "changes": {k: ov.get(k) for k in ("nodes_added", "nodes_edited", "nodes_deleted")},
               "overlay_sha256": _sha(snap), "overlay_bytes": snap.stat().st_size}
        part = dest.with_suffix(".part")
        with tarfile.open(part, "w:gz", compresslevel=6) as t:
            data = json.dumps(man, indent=2).encode()
            ti = tarfile.TarInfo("manifest.json")
            ti.size, ti.mtime = len(data), time.time()
            t.addfile(ti, io.BytesIO(data))
            t.add(snap, "overlay.sqlite")
        part.replace(dest)
    verify(dest)
    return {"file": str(dest), "bytes": dest.stat().st_size, "sha256": _sha(dest), **{k: man[k] for k in ("head", "changes", "overlay_sha256")}}


def verify(path: str | Path) -> dict:
    path = Path(path)
    try:
        with tarfile.open(path, "r:gz") as t:
            man = json.loads(t.extractfile("manifest.json").read())
            h = hashlib.sha256()
            f = t.extractfile("overlay.sqlite")
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
    except (tarfile.TarError, KeyError, OSError, EOFError, ValueError) as e:
        raise P.ProjectError(f"{path.name} is not a readable fork file ({e}).")
    if man.get("format") != FORMAT:
        raise P.ProjectError(f"{path.name} is not a fork file (format {man.get('format')!r}).")
    if h.hexdigest() != man.get("overlay_sha256"):
        raise P.ProjectError(f"{path.name} failed verification: its changes do not match the recorded checksum.", 422)
    return man


def import_file(path: str | Path, name: str | None = None) -> str:
    """Open a fork file as a new fork project. Uses this computer's linked project for the same network when there is one."""
    path = Path(path)
    man = verify(path)
    net = man.get("network") or {}
    want = set((net.get("identity") or {}).get("pack_ids") or [])
    base_slug = None
    for p in P.list_projects():
        if p.get("kind") == "linked":
            bm = P.read_manifest(p["id"])
            if want & set(((bm.get("network") or {}).get("identity") or {}).get("pack_ids") or []):
                base_slug, net = p["id"], bm["network"]
                break
    nm = (name or "").strip() or man.get("name") or path.stem
    slug = P.unique_slug(nm)
    d = P.project_dir(slug)
    d.mkdir(parents=True)
    try:
        with tarfile.open(path, "r:gz") as t, t.extractfile("overlay.sqlite") as src, (d / P.OVERLAY_FILE).open("wb") as dst:
            shutil.copyfileobj(src, dst)
        if _sha(d / P.OVERLAY_FILE) != man["overlay_sha256"]:
            raise P.ProjectError("The extracted changes do not match the fork file's checksum.", 500)
        Overlay(d / P.OVERLAY_FILE).close()             # format check
        m = {"format": P.FORMAT_LINKED, "kind": "fork", "id": P.uuid.uuid4().__str__(), "slug": slug, "name": nm, "description": "",
             "architecture": P.ARCHITECTURE_NAME, "created": P.now(), "modified": P.now(),
             "network": {**net, "protected": False}, "base_project": base_slug, "overlay": {"file": P.OVERLAY_FILE},
             "origin": {"kind": "fork-file", "detail": path.name, "imported": P.now(), "fork_file_sha256": _sha(path)},
             "references": {"checkpoints": [], "core": None, "encoder": None}}
        P.write_manifest(slug, m)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return slug
