"""Yaob packages: one folder (or zip) with a cognitive core, a network and the manifest that pairs them (`fylume-package/1`).

    manifest.json        {"schema": "fylume-package/1", "family", "configuration" ("C4B-N10K" | "C4B-N119M"), "version",
                          "core": {id, version, repo, revision, file: "cognitive-core/<gguf>", sha256, bytes, parameters, quant},
                          "encoder": {same, optional},
                          "mpn": {id, version, repo, revision, format: "fgmpn" | "studio-store", path: "mpn/...", nodes, files: [{path, bytes, sha256}]},
                          "runtime": {studio, fgmpn, llama_cpp}, "checksums": "checksums.sha256"}
    checksums.sha256     "<sha256>  <path>" for every file of the package
    cognitive-core/*.gguf, encoder/*.gguf (optional), mpn/...

`import_package` verifies the checksums, registers the network and the GGUF files WHERE THEY ARE (an FG-MPN store of tens of
gigabytes is never copied; a zip is extracted once into Studio's data folder and then used there), and creates the pairing. For
N10K the package carries the Studio payload (studio/network/yaob-n10k.sqlite and its router checkpoint) and the FG-MPN store; the
Studio payload is what answers (its router and encoder are unchanged) and the FG-MPN store is registered beside it to explore. For
N119M the package carries only the FG-MPN store, which a pairing reads natively. Importing a package again finds its pairing.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

from .. import __version__
from .. import names
from ..desktop import paths
from .manifest import FGMPN_VERSIONS, STUDIO_CHECKPOINT, STUDIO_NETWORK, version_tuple

SCHEMA = "fylume-package/1"
Progress = Callable[[str, int, int], None]


class PackageError(Exception):
    def __init__(self, msg: str, status: int = 400, code: str = "package"):
        super().__init__(msg)
        self.status, self.code = status, code


def packages_dir() -> Path:
    return paths.data_dir() / "packages"


def _rel(p: str, what: str) -> str:
    if not isinstance(p, str) or not p or p.startswith(("/", "\\")) or ".." in Path(p).parts or re.match(r"^[A-Za-z]:", p):
        raise PackageError(f"The package's {what} must be a relative path inside the package (got {p!r}).", 422, "invalid")
    return p


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:60] or "package"


def read_manifest(root: Path) -> dict:
    f = root / "manifest.json"
    if not f.is_file():
        raise PackageError(f"{root} is not a Yaob package: manifest.json is missing.", 422, "invalid")
    try:
        m = json.loads(f.read_text())
    except (OSError, ValueError) as e:
        raise PackageError(f"manifest.json cannot be read: {e}", 422, "invalid")
    if m.get("schema") != SCHEMA:
        raise PackageError(f"manifest.json is {m.get('schema')!r}; this Studio reads {SCHEMA}.", 422, "invalid")
    for k in ("family", "configuration", "version", "core", "mpn", "checksums"):
        if not m.get(k):
            raise PackageError(f"manifest.json has no {k}.", 422, "invalid")
    core, mpn = m["core"], m["mpn"]
    for part, name in ((core, "core"), (m.get("encoder"), "encoder")):
        if part is None and name == "encoder":
            continue
        for k in ("file", "sha256", "bytes"):
            if not part.get(k):
                raise PackageError(f"manifest.json: the {name} has no {k}.", 422, "invalid")
        _rel(part["file"], f"{name} file")
        if not re.fullmatch(r"[0-9a-f]{64}", str(part["sha256"])):
            raise PackageError(f"manifest.json: the {name}'s sha256 is not a SHA-256.", 422, "invalid")
    if mpn.get("format") not in ("fgmpn", "studio-store"):
        raise PackageError(f"manifest.json: the network's format is {mpn.get('format')!r}; Studio reads fgmpn and studio-store.", 422, "invalid")
    _rel(mpn.get("path"), "network path")
    _rel(m["checksums"], "checksums file")
    for f_ in mpn.get("files") or []:
        _rel(f_.get("path"), "network file path")
        if not re.fullmatch(r"[0-9a-f]{64}", str(f_.get("sha256"))) or not isinstance(f_.get("bytes"), int):
            raise PackageError(f"manifest.json: network file {f_.get('path')} lacks bytes or sha256.", 422, "invalid")
    rt = m.get("runtime") or {}
    if rt.get("studio") and version_tuple(rt["studio"]) > version_tuple(__version__):
        raise PackageError(f"This package needs MPN Studio {rt['studio']} or newer; this is {__version__}.", 422, "incompatible")
    if rt.get("fgmpn") and ".".join(str(rt["fgmpn"]).split(".")[:2]) not in FGMPN_VERSIONS:
        raise PackageError(f"This package's network is FG-MPN {rt['fgmpn']}; this Studio reads FG-MPN {', '.join(v + '.x' for v in FGMPN_VERSIONS)}.", 422, "incompatible")
    return m


def parse_checksums(root: Path, m: dict) -> dict[str, str]:
    f = root / m["checksums"]
    if not f.is_file():
        raise PackageError(f"The package's checksums file {m['checksums']} is missing.", 422, "invalid")
    out = {}
    for line in f.read_text().splitlines():
        mm = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if mm:
            out[mm.group(2).lstrip("./")] = mm.group(1).lower()
    if not out:
        raise PackageError(f"{m['checksums']} lists no files.", 422, "invalid")
    return out


def _files(root: Path, m: dict) -> list[dict]:
    """Every file the manifest names, with its size and SHA-256, resolved against the package folder."""
    out = []
    for part in ("core", "encoder"):
        p = m.get(part)
        if p:
            out.append({"path": p["file"], "bytes": int(p["bytes"]), "sha256": p["sha256"], "role": part})
    base = m["mpn"]["path"].rstrip("/")
    for f in m["mpn"].get("files") or []:
        rel = f["path"]
        cand = [rel, f"{base}/{rel}"]
        use = next((c for c in cand if (root / c).is_file()), cand[0])
        out.append({"path": use, "bytes": int(f["bytes"]), "sha256": f["sha256"], "role": "mpn"})
    return out


def sha256_file(p: Path, tick: Callable[[int], None] | None = None) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""):
            h.update(b)
            if tick:
                tick(len(b))
    return h.hexdigest()


def check(root: Path, m: dict | None = None, verify: str = "full", progress: Progress | None = None, cancel: threading.Event | None = None) -> dict:
    """Does the folder hold what its manifest says? `size`: every listed file exists with the right size (seconds, whatever the size).
    `full`: also the SHA-256 of every file, against the manifest and the checksums file."""
    m = m or read_manifest(root)
    files = _files(root, m)
    sums = parse_checksums(root, m)
    problems = []
    seen: dict[str, dict] = {}
    for f in files:
        seen[f["path"]] = f
    for path, sha in sums.items():                                   # the checksums file may list more (docs); it must not contradict the manifest
        if path in seen and seen[path]["sha256"] != sha:
            problems.append(f"{path}: the manifest and {m['checksums']} give different SHA-256 values")
        if path not in seen and (root / path).is_file():
            seen[path] = {"path": path, "bytes": (root / path).stat().st_size, "sha256": sha, "role": "other"}
    for path, f in seen.items():
        p = root / path
        if not p.is_file():
            problems.append(f"{path}: missing")
        elif p.stat().st_size != f["bytes"]:
            problems.append(f"{path}: {p.stat().st_size} bytes, the manifest says {f['bytes']}")
    total = sum(f["bytes"] for f in seen.values())
    if verify == "full" and not problems:
        done = 0
        for path, f in seen.items():
            if cancel and cancel.is_set():
                raise PackageError("Cancelled.", 499, "cancelled")
            base = done

            def tick(n, _p=path):
                nonlocal base
                base += n
                if progress:
                    progress(f"checking {_p}", base, total)
            if sha256_file(root / path, tick) != f["sha256"]:
                problems.append(f"{path}: SHA-256 differs")
            done += f["bytes"]
    return {"ok": not problems, "problems": problems, "files": len(seen), "bytes": total, "verify": verify}


# ------------------------------------------------------------------------------------------------ zip
def extract_zip(zpath: Path, progress: Progress | None = None, cancel: threading.Event | None = None) -> Path:
    """A package zip, extracted once into <data>/packages/<name>/ (the folder is then the package). Refuses paths that leave the folder,
    and checks free space first."""
    from .download import free_bytes, fmt_bytes
    try:
        z = zipfile.ZipFile(zpath)
    except (OSError, zipfile.BadZipFile) as e:
        raise PackageError(f"{zpath.name} is not a readable zip file: {e}", 422, "invalid")
    infos = [i for i in z.infolist() if not i.is_dir()]
    names = [i.filename for i in infos]
    top = {n.split("/", 1)[0] for n in names}
    prefix = ""
    if "manifest.json" not in names:
        if len(top) == 1 and f"{next(iter(top))}/manifest.json" in names:
            prefix = next(iter(top)) + "/"
        else:
            raise PackageError(f"{zpath.name} holds no manifest.json at its top level.", 422, "invalid")
    total = sum(i.file_size for i in infos)
    base = packages_dir()
    base.mkdir(parents=True, exist_ok=True)
    free = free_bytes(base)
    if free < total + (256 << 20):
        raise PackageError(f"Extracting {zpath.name} needs {fmt_bytes(total)} and {fmt_bytes(free)} is free. Nothing was extracted.", 507, "disk_space")
    name = _slug(zpath.stem)
    dest = base / name
    stage = base / (name + ".partial")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    done = 0
    try:
        for i in infos:
            rel = i.filename[len(prefix):] if prefix and i.filename.startswith(prefix) else i.filename
            if prefix and not i.filename.startswith(prefix):
                continue
            target = (stage / rel).resolve()
            if stage.resolve() not in target.parents:
                raise PackageError(f"{zpath.name} holds a path that leaves the package folder ({i.filename}).", 422, "invalid")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, open(target, "wb") as out:
                for b in iter(lambda: src.read(8 << 20), b""):
                    if cancel and cancel.is_set():
                        raise PackageError("Cancelled.", 499, "cancelled")
                    out.write(b)
                    done += len(b)
                    if progress:
                        progress(f"extracting {rel}", done, total)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if dest.exists():
        shutil.rmtree(dest)
    stage.rename(dest)
    return dest


def inspect(path: str | Path) -> dict:
    """What a package folder says about itself, and whether its files are all there at the right size. Reads no file's contents."""
    root = Path(path).expanduser().resolve()
    if root.is_file():
        raise PackageError("Give the package folder, or import the zip.", 422, "invalid")
    m = read_manifest(root)
    c = check(root, m, "size")
    mpn = m["mpn"]
    return {"path": str(root), "family": m["family"], "configuration": names.configuration(m["configuration"]), "version": m["version"], "core": {k: m["core"].get(k) for k in ("id", "version", "file", "bytes", "parameters", "quant")},
            "encoder": ({k: m["encoder"].get(k) for k in ("id", "version", "file", "bytes")} if m.get("encoder") else None),
            "mpn": {k: mpn.get(k) for k in ("id", "version", "format", "path", "nodes")}, "runtime": m.get("runtime"), "files": c["files"], "bytes": c["bytes"], "ok": c["ok"], "problems": c["problems"]}


# ------------------------------------------------------------------------------------------------ import
def _store_root(root: Path, m: dict) -> Path | None:
    from ..mpnnet import fgmpn_local
    base = root / m["mpn"]["path"]
    for c in (base, base / "store"):
        if fgmpn_local.is_store(c):
            return c
    return None


def import_package(path: str | Path, progress: Progress | None = None, cancel: threading.Event | None = None, verify: str = "full", pairing: bool = True) -> dict:
    """Verify a package and make it usable: network and GGUF files registered where they are, and a pairing that uses them."""
    from ..library import cores, models, networks
    from . import install
    if verify not in ("full", "size"):
        raise PackageError("verify must be full or size.", 400, "invalid")
    src = Path(path).expanduser().resolve()
    if not src.exists():
        raise PackageError(f"No such file or folder: {src}", 404, "not_found")
    extracted = None
    if src.is_file():
        if src.suffix.lower() != ".zip":
            raise PackageError("A package is a folder, or a .zip of one.", 422, "invalid")
        root = extracted = extract_zip(src, progress, cancel)
    else:
        root = src
    m = read_manifest(root)
    rep = check(root, m, verify, progress, cancel)
    if not rep["ok"]:
        raise PackageError("The package does not verify: " + "; ".join(rep["problems"][:4]) + (f" (and {len(rep['problems']) - 4} more)" if len(rep["problems"]) > 4 else "") + ". Nothing was registered.",
                           422, "verify")
    mpn, cfg = m["mpn"], names.configuration(m["configuration"])
    existing = next((x for x in models.list_models() if ((x.get("release") or {}).get("package") or {}).get("path") == str(root)
                     and x["release"]["package"].get("version") == m["version"]), None)
    out: dict = {"path": str(root), "family": m["family"], "configuration": cfg, "version": m["version"], "verified": verify, "extracted": bool(extracted), "registered": []}
    # ---- cores, registered where they are
    def reg_core(part: dict, kind: str) -> dict:
        c = cores.import_gguf(root / part["file"], mode="reference", name=f"{names.family_name(m['family'])} {part.get('id') or kind} {part.get('quant') or ''}".strip(), known_sha256=part["sha256"])
        if c["kind"] != kind:
            raise PackageError(f"{part['file']} is a {c['kind']}, not a {'cognitive core' if kind == 'core' else 'retrieval encoder'}.", 422, "invalid")
        out["registered"].append({"kind": kind, "id": c["id"], "path": str(root / part["file"]), "in_place": True})
        return c
    core = reg_core(m["core"], "core")
    enc = reg_core(m["encoder"], "encoder") if m.get("encoder") else None
    # ---- the network
    name = f"{names.family_name(m['family'])} {cfg} {m['version']}"
    net_slug, fg_rec = None, None
    store = _store_root(root, m)
    sq = root / mpn["path"] / STUDIO_NETWORK
    if mpn["format"] == "studio-store":
        if not sq.is_file():
            raise PackageError(f"The package says its network is a Studio store but {mpn['path']}/{STUDIO_NETWORK} is missing.", 422, "invalid")
        ck = root / mpn["path"] / STUDIO_CHECKPOINT.rstrip("/")
        try:
            net_slug = networks.import_studio_payload(sq, ck if ck.is_dir() else None, names.component_name(mpn.get("id") or cfg, m["family"]), f"package {m['family']} {cfg} {m['version']}",
                                                      {"name": enc["name"], "sha256": enc["sha256"]} if enc else None)
        except Exception as e:
            raise PackageError(f"The Studio network of the package could not be imported: {e}", 422, "incompatible")
        out["registered"].append({"kind": "network", "format": "studio-store", "project": net_slug})
    if store is not None:
        fg_rec = install.install_local_fgmpn(store, names.component_name(mpn.get("id") or cfg, m["family"]), False, progress, cancel, "size", rid=_slug(names.component_id(mpn.get("id")) or f"{m['family']}-{cfg}"), version=str(mpn.get("version") or m["version"]),
                                             extra={"package": {"family": m["family"], "configuration": cfg, "version": m["version"], "path": str(root)}, "nodes": mpn.get("nodes"),
                                                    "name": names.component_name(mpn.get("id") or cfg, m["family"])})
        out["registered"].append({"kind": "network", "format": "fgmpn", "path": str(store), "in_place": True, "id": fg_rec["id"], "version": fg_rec["version"]})
    elif mpn["format"] == "fgmpn":
        raise PackageError(f"The package says its network is an FG-MPN store but no store (CURRENT and manifests/) is under {mpn['path']}.", 422, "invalid")
    if existing or not pairing:
        out["pairing"] = existing
        out["already"] = bool(existing)
        return out
    # ---- the pairing
    p = models.create(name, f"Package {m['family']} {cfg} {m['version']}", core["id"], net_slug, enc["id"] if (enc and net_slug) else None)
    raw = models._read(p["id"])
    if not net_slug:
        raw["network"] = {"release": {"id": fg_rec["id"], "version": fg_rec["version"], "format": "fgmpn"}}
    raw["model_id"] = _slug(f"{m['family']}-{cfg}")
    raw["release"] = {"package": {"family": m["family"], "configuration": cfg, "version": m["version"], "path": str(root), "mpn": mpn.get("id"), "core": m["core"].get("id")}}
    models._write(p["id"], raw)
    out["pairing"] = models.get(p["id"])
    out["already"] = False
    return out
