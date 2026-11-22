"""MPN projects on the local disk.

A project is a directory under ~/.local/share/mpn-studio/projects/<slug>/ :

    project.json     manifest (format mpn-studio-project/1): name, origin, schema versions, portable references
    store.sqlite     the versioned node store (authoritative state, every edit is a revision)
    banks/ jobs/ bundles/ packages/   created on demand by the existing tooling (MPN_DATA points here)

Every edit is committed to the store as it is made, so there is no unsaved state to lose; File > Save folds the
write-ahead log into the file and records a manifest summary. Anything that can modify existing user data
(schema/manifest migration, restore over a project) makes a verified backup first.

Interchange uses the existing portable formats (`yaob-bundle/1`, `yaob-mpn-package/1`), not database files.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import uuid
import zlib
from pathlib import Path

from .. import ARCHITECTURE_NAME, LEGACY_ARCHITECTURE_NAME
from ..store.schema import SCHEMA_VERSION
from . import paths

FORMAT = "mpn-studio-project/1"           # a project with its own store ("network"); unchanged so MPN Studio 0.3 still opens it
FORMAT_LINKED = "mpn-studio-project/2"    # a linked network or a fork of one (no store; MPN Studio 0.4 or newer)
FORMAT_VERSION = 1
KINDS = ("network", "linked", "fork")
OVERLAY_FILE = "overlay.sqlite"
BACKUP_FORMAT = "mpn-studio-backup/1"
STORE_FILE = "store.sqlite"


class ProjectError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60]
    if not s:
        raise ProjectError("Give the project a name.")
    return s


def project_dir(slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", slug or ""):
        raise ProjectError("Invalid project id.")
    return paths.projects_dir() / slug


def store_path(slug: str) -> Path:
    return project_dir(slug) / STORE_FILE


def unique_slug(name: str) -> str:
    base, n, slug = slugify(name), 1, slugify(name)
    while project_dir(slug).exists():
        n += 1
        slug = f"{base}-{n}"
    return slug


# ---------------------------------------------------------------------------- manifest
def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def upgrade_manifest(m: dict) -> tuple[dict, list[str]]:
    """Bring a manifest of any earlier shape to FORMAT_VERSION. Returns (manifest, what changed). Never drops unknown keys."""
    changed = []
    m = dict(m)
    if m.get("format") == FORMAT_LINKED:
        return m, changed
    if m.get("format") != FORMAT:
        if m.get("format") not in (None, ""):
            raise ProjectError(f"Unsupported project format {m.get('format')!r}.")
        m["format"] = FORMAT
        changed.append("format marker added")
    # 'Modular Parameter Network' was renamed 'Modular Persistent Network'; the identifiers it appears in are unchanged.
    if m.get("architecture") != ARCHITECTURE_NAME:
        m["architecture"] = ARCHITECTURE_NAME
        m["legacy_architecture_name"] = LEGACY_ARCHITECTURE_NAME
        changed.append("architecture name updated")
    m.setdefault("references", {"checkpoints": [], "core": None, "encoder": None})
    return m, changed


def read_manifest(slug: str) -> dict:
    p = project_dir(slug) / "project.json"
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        raise ProjectError("No such project.", 404)
    except ValueError:
        raise ProjectError("project.json is damaged. Restore the project from a backup.", 500)


def write_manifest(slug: str, m: dict) -> None:
    m["modified"] = now()
    _atomic_write(project_dir(slug) / "project.json", json.dumps(m, indent=2, ensure_ascii=False) + "\n")


def store_schema_version(db: Path) -> int | None:
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            r = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        finally:
            c.close()
        return int(r[0]) if r else None
    except sqlite3.Error:
        return None


def summarize(db: Path) -> dict:
    """Cheap facts for the project list, read straight from the file (no Store, no schema changes)."""
    out: dict = {"bytes": db.stat().st_size if db.exists() else 0}
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            out["head"] = c.execute("SELECT MAX(rev) FROM revisions").fetchone()[0]
            out["nodes"] = c.execute("SELECT COUNT(*) FROM node_v WHERE rev_to IS NULL AND status='live'").fetchone()[0]
            out["mpns"] = c.execute("SELECT COUNT(*) FROM mpn_v WHERE rev_to IS NULL").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error:
        pass
    return out


def kind_of(m: dict) -> str:
    k = m.get("kind") or "network"
    return k if k in KINDS else "network"


def summarize_overlay(path: Path) -> dict:
    out = {"bytes": path.stat().st_size if path.exists() else 0}
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            a, e, d = c.execute("SELECT SUM(state='put' AND created=1), SUM(state='put' AND created=0), SUM(state='del' AND created=0) "
                                "FROM cur_nodes").fetchone()
            out.update(nodes_added=a or 0, nodes_edited=e or 0, nodes_deleted=d or 0,
                       head=c.execute("SELECT COALESCE(MAX(rev),0) FROM revisions").fetchone()[0],
                       versions=c.execute("SELECT COUNT(*) FROM versions").fetchone()[0])
        finally:
            c.close()
    except sqlite3.Error:
        out["damaged"] = True
    return out


def _dir_bytes(d: Path) -> int:
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


def project_summary(slug: str, m: dict) -> dict:
    """What the home screen shows for one project. Read straight from the files; never opens a network."""
    d = project_dir(slug)
    kind = kind_of(m)
    base = {"id": slug, "uid": m.get("id"), "name": m.get("name", slug), "description": m.get("description", ""), "kind": kind,
            "created": m.get("created"), "modified": m.get("modified"), "origin": m.get("origin"),
            "core": (m.get("references") or {}).get("core")}
    if kind == "network":
        return {**base, **summarize(d / STORE_FILE)}
    net = m.get("network") or {}
    ident = net.get("identity") or {}
    out = {**base, "network": {"name": ident.get("name"), "nodes": ident.get("nodes"), "relationships": ident.get("relationships"),
                               "storage_bytes": ident.get("storage_bytes"), "known": ident.get("known"),
                               "sources": [{"mode": s.get("mode"), "label": s.get("label"), "target": s.get("target")} for s in net.get("sources") or []]},
           "nodes": ident.get("nodes"), "bytes": _dir_bytes(d)}
    if kind == "fork":
        ov = summarize_overlay(d / (m.get("overlay") or {}).get("file", OVERLAY_FILE))
        out["changes"] = ov
        out["head"] = ov.get("head")
        if ident.get("nodes") is not None:
            out["nodes"] = ident["nodes"] + ov.get("nodes_added", 0) - ov.get("nodes_deleted", 0)
        out["base"] = m.get("base_project")
    return out


def list_projects() -> list[dict]:
    out = []
    root = paths.projects_dir()
    for d in sorted(root.glob("*/project.json")) if root.exists() else []:
        slug = d.parent.name
        try:
            m = json.loads(d.read_text())
        except ValueError:
            out.append({"id": slug, "name": slug, "damaged": True})
            continue
        out.append(project_summary(slug, m))
    return sorted(out, key=lambda p: p.get("modified") or "", reverse=True)


# ---------------------------------------------------------------------------- linked networks and forks
def create_linked(name: str, sources: list[dict], identity: dict, description: str = "") -> str:
    """A project that names a large network and where to read it. Nothing is copied: the folder holds project.json only."""
    name = (name or "").strip() or identity.get("name") or "Linked network"
    clean = []
    for s in sources:
        if s.get("mode") not in ("local", "remote", "fgmpn") or not s.get("target"):
            raise ProjectError("Each source needs a folder or a network address.")
        clean.append({"mode": s["mode"], "target": str(s["target"]).strip(), "label": (s.get("label") or "").strip() or None})
    if not clean:
        raise ProjectError("Give at least one place to read the network from.")
    slug = unique_slug(name)
    d = project_dir(slug)
    d.mkdir(parents=True)
    m = {"format": FORMAT_LINKED, "kind": "linked", "id": str(uuid.uuid4()), "slug": slug, "name": name, "description": description,
         "architecture": ARCHITECTURE_NAME, "created": now(), "modified": now(),
         "network": {"identity": identity, "sources": clean, "preferred": 0, "protected": True},
         "origin": {"kind": "linked", "linked": now()}, "references": {"checkpoints": [], "core": None, "encoder": None}}
    write_manifest(slug, m)
    return slug


def create_fork(base_slug: str, name: str, overlay_from: Path | None = None, upto_rev: int | None = None, origin: dict | None = None) -> str:
    """An editable fork of a linked network: the base is referenced, never copied; edits go to overlay.sqlite."""
    from ..workspace.overlay import Overlay
    bm = read_manifest(base_slug)
    if kind_of(bm) not in ("linked", "fork"):
        raise ProjectError("Only linked networks and their forks are forked this way.")
    name = (name or "").strip()
    if not name:
        raise ProjectError("Give the new project a name.")
    slug = unique_slug(name)
    d = project_dir(slug)
    d.mkdir(parents=True)
    try:
        net = copy.deepcopy(bm["network"])
        net["protected"] = False
        if overlay_from is not None:
            src = Overlay(overlay_from)
            try:
                src.copy_to(d / OVERLAY_FILE, upto_rev)
            finally:
                src.close()
        else:
            Overlay(d / OVERLAY_FILE, net.get("identity"), create=True).close()
        root = bm.get("base_project") if kind_of(bm) == "fork" else base_slug
        m = {"format": FORMAT_LINKED, "kind": "fork", "id": str(uuid.uuid4()), "slug": slug, "name": name, "description": "",
             "architecture": ARCHITECTURE_NAME, "created": now(), "modified": now(), "network": net, "base_project": root,
             "overlay": {"file": OVERLAY_FILE}, "origin": origin or {"kind": "fork", "of": base_slug, "of_name": bm.get("name"), "at": now()},
             "references": {"checkpoints": [], "core": None, "encoder": None}}
        write_manifest(slug, m)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return slug


def overlay_path(slug: str) -> Path:
    m = read_manifest(slug)
    return project_dir(slug) / (m.get("overlay") or {}).get("file", OVERLAY_FILE)


# ---------------------------------------------------------------------------- create / import
def _new_manifest(name: str, slug: str, origin: dict, description: str = "") -> dict:
    return {"format": FORMAT, "id": str(uuid.uuid4()), "slug": slug, "name": name, "description": description,
            "architecture": ARCHITECTURE_NAME, "legacy_architecture_name": LEGACY_ARCHITECTURE_NAME,
            "created": now(), "modified": now(), "store": {"file": STORE_FILE, "schema_version": SCHEMA_VERSION},
            "origin": origin, "references": {"checkpoints": [], "core": None, "encoder": None}}


def create(name: str, description: str = "") -> str:
    name = (name or "").strip()
    slug = unique_slug(name)
    d = project_dir(slug)
    d.mkdir(parents=True)
    try:
        from ..store import layout, schemas
        from ..store.db import Store
        st = Store(d / STORE_FILE)           # creates the schema
        schemas.seed_schemas(st)
        layout.ensure_tiles(st, 1)
        st.checkpoint_wal()
        st.close()
        write_manifest(slug, _new_manifest(name, slug, {"kind": "new"}, description))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return slug


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _copy_store(src: Path, dst: Path) -> None:
    """Consistent copy of a SQLite file (WAL-safe) using SQLite's online backup. The source is opened read-only."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
        d.execute("PRAGMA journal_mode=WAL")
    finally:
        d.close()
        s.close()
    c = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
    try:
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        c.close()
    if ok != "ok":
        raise ProjectError(f"The copied store failed its integrity check ({ok}).", 500)


def import_store_file(src: str | Path, name: str, origin_detail: str = "", kind: str = "store-file") -> str:
    """Adopt an existing MPN Studio store (for example a snapshot of the Spark's yaob-n10k.sqlite) as a new project.
    The original file is only read."""
    src = Path(src)
    if not src.is_file():
        raise ProjectError(f"{src} is not a file.", 404)
    v = store_schema_version(src)
    if v is None:
        raise ProjectError(f"{src.name} is not an MPN Studio store.")
    if v > SCHEMA_VERSION:
        raise ProjectError(f"This store uses schema {v}; this MPN Studio understands up to {SCHEMA_VERSION}. Update MPN Studio.")
    slug = unique_slug(name)
    d = project_dir(slug)
    d.mkdir(parents=True)
    try:
        _copy_store(src, d / STORE_FILE)
        m = _new_manifest(name, slug, {"kind": kind, "detail": origin_detail or str(src), "imported": now(),
                                       "source_file": src.name, "source_schema_version": v})
        m["store"]["schema_version"] = v
        write_manifest(slug, m)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return slug


def import_bundle_dir(bundle_dir: str | Path, name: str | None = None) -> str:
    from ..store import bundle
    bdir = Path(bundle_dir)
    man = bdir / "manifest.json"
    if not man.is_file():
        raise ProjectError(f"{bdir} has no manifest.json. Choose the folder that contains it.")
    manifest = json.loads(man.read_text())
    if manifest.get("format") != bundle.FORMAT:
        raise ProjectError(f"Not a Yaob bundle (format {manifest.get('format')!r}). Single MPN packages are installed into an open project instead.")
    name = name or manifest.get("name") or bdir.name
    slug = unique_slug(name)
    d = project_dir(slug)
    d.mkdir(parents=True)
    try:
        # checkpoint tensors in a bundle are restored only when torch is available; the store content never needs it
        res = bundle.import_bundle(bdir, d / STORE_FILE, ck_root=d / "banks")
        m = _new_manifest(name, slug, {"kind": "bundle", "detail": str(bdir), "imported": now(), "bundle_manifest_sha256": sha256_file(man)})
        write_manifest(slug, m)
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        if isinstance(e, ValueError):
            raise ProjectError(str(e))
        raise
    return slug


def export_bundle_dir(store, slug: str, dest: str | Path, name: str | None = None) -> dict:
    """Write the open project as a `yaob-bundle/1` directory (verified by checksums on import)."""
    from ..store import bundle
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ProjectError(f"{dest} is not empty. Choose an empty or new folder.", 409)
    m = read_manifest(slug)
    man = bundle.export_bundle(store, dest, name or m.get("name") or slug, bundle.active_checkpoint_dir(store), store.get_setting("core_reference") or {}, None)
    return {"path": str(dest), "revision": man["revision"], "counts": man["counts"], "files": len(man["files"]), "checkpoint": man.get("checkpoint")}


# ---------------------------------------------------------------------------- open (with migration)
def prepare_open(slug: str) -> dict:
    """Verify a project can be opened; back it up and migrate its manifest first when it is older. Returns the manifest."""
    m = read_manifest(slug)
    if kind_of(m) != "network":
        if m.get("format") != FORMAT_LINKED:
            raise ProjectError(f"Unsupported project format {m.get('format')!r}.")
        if kind_of(m) == "fork" and not (project_dir(slug) / (m.get("overlay") or {}).get("file", OVERLAY_FILE)).is_file():
            raise ProjectError("The fork's change file is missing. Restore the project from a backup.", 500)
        return m
    db = store_path(slug)
    if not db.is_file():
        raise ProjectError("The project's store file is missing. Restore it from a backup.", 500)
    v = store_schema_version(db)
    if v is not None and v > SCHEMA_VERSION:
        raise ProjectError(f"This project was saved by a newer MPN Studio (store schema {v}; this version supports {SCHEMA_VERSION}).")
    m2, changed = upgrade_manifest(m)
    if v is not None and v < SCHEMA_VERSION:
        changed.append(f"store schema {v} -> {SCHEMA_VERSION}")
    if changed:
        backup(slug, reason="before migration: " + "; ".join(changed))
        m2["store"] = dict(m2.get("store") or {}, file=STORE_FILE, schema_version=SCHEMA_VERSION if v is not None else v)
        m2.setdefault("migrations", []).append({"at": now(), "changes": changed})
        write_manifest(slug, m2)
        return m2
    return m


def record_save(slug: str, store) -> dict:
    store.checkpoint_wal()
    m = read_manifest(slug)
    m["summary"] = {"head": store.head(), "saved": now()}
    write_manifest(slug, m)
    return {"saved": m["summary"]["saved"], "head": m["summary"]["head"]}


# ---------------------------------------------------------------------------- backup / restore / delete / rename
def backup(slug: str, reason: str = "manual") -> Path:
    """One file: <data>/backups/<slug>/<stamp>.mpnbak (tar.gz of project.json, a consistent copy of the project's data file
    (store or fork change file; a linked network has none) and a checksum record). A linked network's own data is never
    copied into a backup: it stays where it is and has its own backups."""
    m = read_manifest(slug)
    kind = kind_of(m)
    out_dir = paths.backups_dir() / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + ".mpnbak")
    with tempfile.TemporaryDirectory(dir=paths.cache_dir() if paths.cache_dir().exists() else None) as tmp:
        files: dict[str, Path] = {}
        meta = {"format": BACKUP_FORMAT, "project": slug, "name": m.get("name"), "kind": kind, "created": now(), "reason": reason}
        if kind == "network":
            snap = Path(tmp) / STORE_FILE
            _copy_store(store_path(slug), snap)
            files[STORE_FILE] = snap
            meta.update(store_sha256=sha256_file(snap), store_bytes=snap.stat().st_size, head=summarize(snap).get("head"))
        elif kind == "fork":
            snap = Path(tmp) / OVERLAY_FILE
            c = sqlite3.connect(overlay_path(slug), timeout=30)
            try:
                c.execute("VACUUM INTO ?", (str(snap),))
            finally:
                c.close()
            files[OVERLAY_FILE] = snap
            meta.update(head=summarize_overlay(snap).get("head"))
        meta["files"] = {k: sha256_file(v) for k, v in files.items()}
        tmp_out = out.with_suffix(".part")
        with tarfile.open(tmp_out, "w:gz", compresslevel=3) as t:
            for k, v in files.items():
                t.add(v, k)
            t.add(project_dir(slug) / "project.json", "project.json")
            data = json.dumps(meta, indent=2).encode()
            ti = tarfile.TarInfo("BACKUP.json")
            ti.size = len(data)
            ti.mtime = time.time()
            t.addfile(ti, io.BytesIO(data))
        os.replace(tmp_out, out)
    verify_backup(out)
    return out


def verify_backup(path: Path) -> dict:
    """Read the archive back and check every data file against its recorded checksum. Raises ProjectError if anything is off."""
    try:
        with tarfile.open(path, "r:gz") as t:
            names = t.getnames()
            for need in ("project.json", "BACKUP.json"):
                if need not in names:
                    raise ProjectError(f"{path.name} is not a valid backup (missing {need}).")
            meta = json.loads(t.extractfile("BACKUP.json").read())
            want = meta.get("files") or ({STORE_FILE: meta.get("store_sha256")} if meta.get("store_sha256") else None)
            if want is None:
                raise ProjectError(f"{path.name} is not a valid backup (no checksums).")
            got = {}
            for name in want:
                if name not in names:
                    raise ProjectError(f"{path.name} is not a valid backup (missing {name}).")
                h = hashlib.sha256()
                f = t.extractfile(name)
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
                got[name] = h.hexdigest()
    except (tarfile.TarError, OSError, EOFError, ValueError, zlib.error) as e:
        raise ProjectError(f"{path.name} cannot be read: {e}")
    if meta.get("format") != BACKUP_FORMAT or got != want:
        raise ProjectError(f"{path.name} failed verification (checksum mismatch).", 500)
    return meta


def list_backups(slug: str | None = None) -> list[dict]:
    root = paths.backups_dir()
    out = []
    for p in sorted(root.glob(f"{slug or '*'}/*.mpnbak"), reverse=True) if root.exists() else []:
        out.append({"id": f"{p.parent.name}/{p.name}", "project": p.parent.name, "file": str(p), "bytes": p.stat().st_size,
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime))})
    return out


def backup_path(backup_id: str) -> Path:
    proj, _, fn = backup_id.partition("/")
    if not re.fullmatch(r"[a-z0-9-]+", proj) or not re.fullmatch(r"[0-9-]+\.mpnbak", fn):
        raise ProjectError("Invalid backup id.")
    p = paths.backups_dir() / proj / fn
    if not p.is_file():
        raise ProjectError("No such backup.", 404)
    return p


def restore(backup_file: str | Path, name: str | None = None) -> str:
    """Restore a backup as a NEW project. Existing projects are never overwritten."""
    p = Path(backup_file)
    meta = verify_backup(p)
    nm = name or f"{meta.get('name') or meta.get('project')} (restored)"
    slug = unique_slug(nm)
    d = project_dir(slug)
    d.mkdir(parents=True)
    want = meta.get("files") or {STORE_FILE: meta["store_sha256"]}
    try:
        with tarfile.open(p, "r:gz") as t:
            for member in (*want, "project.json"):
                with t.extractfile(member) as src, (d / member).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        m = read_manifest(slug)
        m.update({"id": str(uuid.uuid4()), "slug": slug, "name": nm})
        m["origin"] = {"kind": "restore", "detail": p.name, "restored": now(), "backup_created": meta.get("created")}
        write_manifest(slug, m)
        for f, h in want.items():
            if sha256_file(d / f) != h:
                raise ProjectError("Restored data does not match the backup checksum.", 500)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return slug


def rename(slug: str, name: str) -> None:
    m = read_manifest(slug)
    m["name"] = (name or "").strip() or m["name"]
    write_manifest(slug, m)


def trash(slug: str) -> Path:
    """Delete = move to <data>/trash/. Nothing is destroyed; the user can empty the folder."""
    src = project_dir(slug)
    if not src.exists():
        raise ProjectError("No such project.", 404)
    paths.trash_dir().mkdir(parents=True, exist_ok=True)
    dst = paths.trash_dir() / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.move(str(src), dst)
    return dst
