"""Install official releases.

  mpn    <data>/releases/mpn/<id>/<version>/{store/, fylume-release.json, INSTALL.json}   (FG-MPN: read-only store folder)
         or, for format `studio-store`, a network project in the library (the downloaded file is removed after import)
  core   the GGUF goes into the core library (<data>/cores/<sha16>/model.gguf); a record is kept in releases/core/...
  model  both of the above, then a pairing that pins them. Neither component is copied into the pairing.

An install happens in a `<version>.partial/` folder beside its final place: files are downloaded and verified there, the
folder is checked as a whole (every listed file, the store's own manifest), and only then renamed into place. Nothing
half-installed is ever visible, and a stopped install resumes where it stopped.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from .. import __version__
from ..desktop import paths
from . import manifest as mf
from . import source
from .download import DownloadError, MARGIN, fetch_bytes, fetch_file, fmt_bytes, free_bytes, hf_endpoint, sha256_file, urls_for

Progress = Callable[[str, int, int], None]


class InstallError(Exception):
    def __init__(self, msg: str, status: int = 400, code: str = "install"):
        super().__init__(msg)
        self.status, self.code = status, code


def releases_dir() -> Path:
    return paths.data_dir() / "releases"


def release_dir(kind: str, rid: str, version: str, fmt: str | None = None) -> Path:
    """`<id>/<version>` for a network's FG-MPN payload and for everything else; `<id>/<version>.studio-store` for the Studio payload,
    so both payloads of one release can be installed side by side."""
    return releases_dir() / kind / rid / (version + ".studio-store" if fmt == "studio-store" else version)


def _write_json(p: Path, d: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


# ------------------------------------------------------------------------------------------------ what is installed
def installed(kind: str | None = None) -> list[dict]:
    out = []
    base = releases_dir()
    for f in sorted(base.glob("*/*/*/INSTALL.json")) if base.exists() else []:
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if kind and rec.get("kind") != kind:
            continue
        rec["path"] = str(f.parent)
        out.append(rec)
    return out


def get_installed(kind: str, rid: str, version: str | None = None, fmt: str | None = None) -> dict | None:
    rows = [r for r in installed(kind) if r["id"] == rid and (version is None or r["version"] == version) and (fmt is None or r.get("format") == fmt)]
    rows.sort(key=lambda r: mf.version_tuple(r["version"]), reverse=True)
    return rows[0] if rows else None


def store_dir(rec: dict) -> Path:
    """The FG-MPN store of an installed network: a folder the user keeps (linked), or the one inside the install (at its root, or under
    `store/` when the release keeps it there)."""
    return Path(rec["store_path"]) if rec.get("store_path") else (Path(rec["path"]) / "store" / (rec.get("store_prefix") or ""))


def files_dir(rec: dict) -> Path:
    """Where an install's own files (rec["files"]) are kept."""
    return store_dir(rec) if rec.get("store_path") else Path(rec["path"]) / "store"


# ------------------------------------------------------------------------------------------------ planning
def default_format(r: dict) -> str | None:
    """The payload of an mpn release installed when none is asked for: the first (primary) one this Studio can use."""
    return next((p["format"] for p in r.get("payloads") or [] if p["ok"]), None)


def _select(r: dict, file: str | None, fmt: str | None = None) -> list[dict]:
    """The files of the release that this install downloads: for a core, the chosen GGUF; for a network, only the payload Studio
    needs (`fgmpn` to explore, `studio-store` to answer in a pairing) plus small documents."""
    files = r["files"]
    small = [f for f in files if not f.get("format") and f.get("role") in ("manifest", "docs") and f["bytes"] <= 1 << 20]
    if r["kind"] == "mpn":
        pl = {p["format"]: p for p in r.get("payloads") or []}
        fmt = fmt or default_format(r)
        if fmt not in pl:
            raise InstallError(f"This release has no {fmt or 'readable'} payload." + (" It has: " + ", ".join(pl) + "." if pl else ""), 404, "no_payload")
        if not pl[fmt]["ok"]:
            raise InstallError(pl[fmt]["reason"], 422, "incompatible")
        return pl[fmt]["files"] + [f for f in small if f not in pl[fmt]["files"]]
    if r["kind"] != "core":
        return files
    gg = [f for f in (r["detail"].get("formats") or []) if f.get("kind") == "gguf"]
    if not gg:
        raise InstallError("This core has no GGUF file Studio can serve.", 422)
    pick = next((f for f in gg if file in (f.get("file"), f.get("quant"))), None) if file else gg[0]
    if pick is None:
        raise InstallError(f"No format {file!r} in this release. Available: {', '.join(str(f.get('quant')) for f in gg)}.", 404)
    names = {pick["file"]}
    return [f for f in files if f["path"] in names or (f.get("role") in ("manifest", "docs") and f["bytes"] <= 1 << 20)]


def plan(r: dict, file: str | None = None, root: Path | None = None, fmt: str | None = None) -> dict:
    """What an install would download, and whether the disk has room. Does not touch the network."""
    sel = _select(r, file, fmt)
    root = root or releases_dir()
    stage = _stage(r, fmt)
    have = 0
    for f in sel:
        for cand in (stage / "store" / f["path"], stage / (f["path"] + ".part"), stage / "store" / (f["path"] + ".part")):
            if cand.exists():
                have += min(cand.stat().st_size, f["bytes"])
                break
    need = sum(f["bytes"] for f in sel) - have
    return {"files": len(sel), "download_bytes": sum(f["bytes"] for f in sel), "remaining_bytes": need, "free_bytes": free_bytes(root),
            "enough_space": free_bytes(root) >= need + MARGIN, "format": fmt or (default_format(r) if r["kind"] == "mpn" else None)}


def _stage(r: dict, fmt: str | None = None) -> Path:
    d = release_dir(r["kind"], r["id"], r["version"], fmt if r["kind"] == "mpn" else None)
    return d.with_name(d.name + ".partial")


def check_compatible(r: dict) -> None:
    if r.get("download_only"):
        raise InstallError(r["download_only"]["reason"], 422, "download_only")
    if r["problems"]:
        raise InstallError("This release is not valid: " + "; ".join(r["problems"][:3]) + ".", 422, "invalid")
    if r["compat"]["reasons"]:
        raise InstallError(" ".join(r["compat"]["reasons"]), 422, "incompatible")


# ------------------------------------------------------------------------------------------------ download + verify
def _download_all(r: dict, files: list[dict], dest_root: Path, progress: Progress | None, cancel: threading.Event | None, label: str = "") -> None:
    total = sum(f["bytes"] for f in files)
    done = [0]

    def tick(n: int):
        done[0] = max(0, done[0] + n)
        if progress:
            progress(label, done[0], total)
    for f in files:
        if cancel is not None and cancel.is_set():
            raise DownloadError("cancelled", "Cancelled. What was downloaded is kept, and the next install continues from it.", 499)
        if progress:
            progress(f["path"], done[0], total)
        label = f["path"]
        fetch_file(f.get("urls") or urls_for(r["hosting"], f["path"]), dest_root / f["path"], f["bytes"], f["sha256"], tick, cancel)


def _check_space(r: dict, files: list[dict], root: Path, fmt: str | None = None) -> None:
    stage = _stage(r, fmt)
    have = 0
    for f in files:
        for cand in (stage / "store" / f["path"], stage / "store" / (f["path"] + ".part")):
            if cand.exists():
                have += min(cand.stat().st_size, f["bytes"])
                break
    need = sum(f["bytes"] for f in files) - have
    root.mkdir(parents=True, exist_ok=True)
    free = free_bytes(root)
    if free < need + MARGIN:
        raise DownloadError("disk_space", f"{r['name']} needs {fmt_bytes(need)} more disk space and {fmt_bytes(free)} is free on {root}. Free some space and try again; "
                            "what has been downloaded is kept.", 507)


def verify_files(root: Path, files: list[dict], progress: Progress | None = None) -> list[str]:
    """Every file listed is present with the right size and SHA-256. Returns the problems found."""
    bad, total, done = [], sum(f["bytes"] for f in files), 0
    for f in files:
        p = root / f["path"]
        if not p.is_file():
            bad.append(f"{f['path']}: missing")
        elif p.stat().st_size != f["bytes"]:
            bad.append(f"{f['path']}: {p.stat().st_size} bytes, expected {f['bytes']}")
        else:
            base = done

            def step(n):
                nonlocal base
                base += n
                if progress:
                    progress("checking " + f["path"], base, total)
            if sha256_file(p, step) != f["sha256"]:
                bad.append(f"{f['path']}: SHA-256 differs from the release manifest")
        done += f["bytes"]
    return bad


# ------------------------------------------------------------------------------------------------ install
def install(r: dict, progress: Progress | None = None, cancel: threading.Event | None = None, file: str | None = None, fmt: str | None = None) -> dict:
    """Install one mpn or core release. For a network, `fmt` is the payload: "fgmpn" (explore, read-only) or "studio-store" (answer in
    a pairing); only that payload's files are downloaded. Idempotent: an installed, intact version is returned as it is."""
    if r["kind"] == "model":
        raise InstallError("A pairing installs through install_model.")
    check_compatible(r)
    if r["kind"] == "mpn":
        fmt = fmt or default_format(r)
    cur = get_installed(r["kind"], r["id"], r["version"], fmt if r["kind"] == "mpn" else None)
    if cur and _intact(cur):
        return cur
    files = _select(r, file, fmt)
    stage = _stage(r, fmt)
    root = stage / "store"
    _check_space(r, files, releases_dir(), fmt)
    root.mkdir(parents=True, exist_ok=True)
    _download_all(r, files, root, progress, cancel)
    if progress:
        progress("verifying", 0, 1)
    bad = verify_files(root, files)
    if bad:
        raise InstallError("The download did not verify: " + "; ".join(bad[:3]) + ". Nothing was installed.", 422, "verify")
    rec = {"kind": r["kind"], "id": r["id"], "name": r["name"], "version": r["version"], "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "studio": __version__, "licence": r["licence"], "hosting": r["hosting"]["primary"], "bytes": sum(f["bytes"] for f in files),
           "warnings": []}
    if r["kind"] == "mpn":
        _finish_mpn(r, rec, stage, root, fmt, files)
    else:
        _finish_core(r, rec, stage, root, files, file)
    _write_json(stage / "fylume-release.json", r["raw"])
    _write_json(stage / "INSTALL.json", rec)
    final = release_dir(r["kind"], r["id"], r["version"], fmt if r["kind"] == "mpn" else None)
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():                                    # an older, damaged install of the same version: keep it aside, never delete
        paths.trash_dir().mkdir(parents=True, exist_ok=True)
        shutil.move(str(final), paths.trash_dir() / f"release-{r['id']}-{r['version']}-{int(time.time())}")
    os.replace(stage, final)
    if r["kind"] == "core":
        shutil.rmtree(final / "store", ignore_errors=True)         # the weights now live in the core library
    if progress:
        n = sum(f["bytes"] for f in files) or 1
        progress("installed", n, n)
    return get_installed(r["kind"], r["id"], r["version"], fmt if r["kind"] == "mpn" else None) or rec


def _finish_mpn(r: dict, rec: dict, stage: Path, root: Path, fmt: str, files: list[dict]) -> None:
    rec["format"] = fmt
    rec["files"] = [{"path": f["path"], "bytes": f["bytes"], "sha256": f["sha256"]} for f in files]
    d = r["detail"]
    rec.update({"nodes": d.get("nodes"), "relationships": d.get("relationships"), "format_version": d.get("format_version"), "read_only": bool(d.get("read_only", True)),
                "studio_min": d.get("studio_min"), "verification": d.get("verification")})
    if fmt == "fgmpn":
        from ..mpnnet import fgmpn_local
        from .manifest import store_prefix
        rec["store_prefix"] = store_prefix(files) or ""
        try:
            info = fgmpn_local.inspect_store(root / rec["store_prefix"])
        except fgmpn_local.FgError as e:
            raise InstallError(f"The download is complete but is not a usable FG-MPN store: {e}", 422, "missing_component")
        if not info["ok"]:
            raise InstallError("The store is missing parts: " + "; ".join((info["missing"] + info["wrong_size"])[:3]) + ".", 422, "missing_component")
        rec["store"] = {"nodes": info["nodes"], "segments": info["segments"], "generation": info["generation"], "manifest": info["manifest"]}
        if d.get("nodes") and info["nodes"] and d["nodes"] != info["nodes"]:
            rec["warnings"].append(f"The release says {d['nodes']:,} nodes; the store's segments hold {info['nodes']:,}.")
    elif fmt == "studio-store":
        from ..desktop import projects
        from ..library import networks
        mine = next(p for p in r["payloads"] if p["format"] == "studio-store")["files"]
        sq = [f for f in mine if f["path"] == mf.STUDIO_NETWORK] or [f for f in mine if f["path"].endswith((".sqlite", ".db"))]
        if len(sq) != 1:
            raise InstallError(f"A Studio payload must list exactly one network file ({mf.STUDIO_NETWORK}).", 422, "missing_component")
        ck = root / mf.STUDIO_CHECKPOINT.rstrip("/")
        try:
            slug = networks.import_studio_payload(root / sq[0]["path"], ck if ck.is_dir() else None, r["name"], f"{r['id']} {r['version']}",
                                                  (d.get("router_encoder") or {}))
        except projects.ProjectError as e:
            raise InstallError(f"The network file is not a store this Studio opens: {e}", 422, "incompatible")
        rec["project"] = slug
        rec["router"] = ck.is_dir()
        rec["read_only"] = False
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir()


def _finish_core(r: dict, rec: dict, stage: Path, root: Path, files: list[dict], file: str | None) -> None:
    from ..library import cores
    ggs = [f for f in r["detail"]["formats"] if f.get("kind") == "gguf"]
    gg = next((g for g in ggs if file in (g.get("file"), g.get("quant"))), ggs[0]) if file else ggs[0]
    f = next(x for x in files if x["path"] == gg["file"])
    existing = cores.find_by_hash(f["sha256"])
    if existing and existing["present"]:
        rec["core_id"] = existing["id"]
        rec["warnings"].append("The same weights were already in the core library; nothing was added.")
        return
    try:
        c = cores.import_gguf(root / f["path"], mode="move", name=f"{r['name']} {gg.get('quant') or ''}".strip(), known_sha256=f["sha256"],
                              release={"id": r["id"], "version": r["version"], "quant": gg.get("quant"), "hosting": r["hosting"]["primary"]})
    except cores.CoreError as e:
        raise InstallError(f"The file downloaded and verified, but Studio cannot read it as a GGUF: {e}", 422, "incompatible")
    rec.update({"core_id": c["id"], "quant": gg.get("quant"), "architecture": c["info"].get("architecture"), "parameters": c["info"].get("parameters")})


def _intact(rec: dict) -> bool:
    if rec["kind"] == "core":
        from ..library import cores
        try:
            return bool(cores.get(rec["core_id"])["present"])
        except (cores.CoreError, KeyError):
            return False
    if rec.get("format") == "studio-store":
        from ..desktop import projects
        return projects.project_dir(rec.get("project") or "-").exists()
    from ..mpnnet import fgmpn_local
    try:
        return fgmpn_local.inspect_store(store_dir(rec))["ok"]
    except fgmpn_local.FgError:
        return False


def verify_installed(kind: str, rid: str, version: str, progress: Progress | None = None, fmt: str | None = None) -> dict:
    rec = get_installed(kind, rid, version, fmt)
    if not rec:
        raise InstallError("That release is not installed.", 404)
    if kind == "core":
        from ..library import cores
        try:
            c = cores.get(rec["core_id"])
        except cores.CoreError:
            return {"ok": False, "problems": ["The core is no longer in the library."]}
        if not c["present"]:
            return {"ok": False, "problems": [f"The weights file is missing: {c['path']}"]}
        got = cores.sha256_file(Path(c["path"]), progress)
        return {"ok": got == c["sha256"], "problems": [] if got == c["sha256"] else ["The weights file does not match its SHA-256."]}
    if rec.get("format") == "studio-store":
        return {"ok": _intact(rec), "problems": [] if _intact(rec) else ["The network project is missing."]}
    from ..mpnnet import fgmpn_local
    try:
        v = fgmpn_local.verify_store(store_dir(rec), progress)
    except fgmpn_local.FgError as e:
        return {"ok": False, "problems": [str(e)]}
    if v["ok"] and rec.get("files"):
        bad = verify_files(files_dir(rec), [dict(f, role="data") for f in rec["files"]])
        v = {"ok": not bad, "problems": bad, "files": len(rec["files"])}
    v["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if v["ok"]:
        path = rec.pop("path")
        rec["verified_at"] = v["verified_at"]
        _write_json(Path(path) / "INSTALL.json", rec)
    return v


def uninstall(kind: str, rid: str, version: str, fmt: str | None = None) -> dict:
    rec = get_installed(kind, rid, version, fmt)
    if not rec:
        raise InstallError("That release is not installed.", 404)
    if kind == "mpn" and rec.get("format") != "studio-store":
        from ..library import models
        users = [m["name"] for m in models.list_models() if ((m.get("network") or {}).get("release") or {}).get("id") == rid]
        if users:
            raise InstallError(f"It is used by {', '.join(users)}. Change those pairings first.", 409)
    t = paths.trash_dir() / f"release-{kind}-{rid}-{version}-{int(time.time())}"
    t.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(rec["path"], t)
    return {"removed": f"{rid} {version}", "moved_to": str(t), "note": "The core library and any network project made from it are separate; remove those there."
            if kind in ("core",) or rec.get("format") == "studio-store" else None}


# ------------------------------------------------------------------------------------------------ prebuilt pairings
def resolve_pin(kind: str, pin: dict) -> dict:
    """The component a pairing pins. A manifest-form pin carries `manifest_sha256`: the component is found in the catalogue when its
    checksum is the pinned one, otherwise fetched from the pinned repository and revision, and a manifest that is not the pinned bytes
    is refused. A website-form component (id or name, and a version, no manifest checksum) is looked up in the catalogue; its files are
    checked by their own SHA-256 when installed."""
    want = str(pin.get("manifest_sha256") or "").lower()
    if not want:
        rels = [r for r in source.load()["releases"] if r["kind"] == kind]
        hit = next((r for r in rels if pin.get("id") and r["id"] == pin["id"] and r["version"] == pin.get("version")), None)
        if hit is None and pin.get("repo_url"):           # a component names its repository: match the entry whose links point there
            hit = next((r for r in rels if r["version"] == pin.get("version") and pin["repo_url"] in (r.get("links") or {}).values()), None)
        if hit is None and not pin.get("id") and pin.get("name"):
            hit = next((r for r in rels if r["version"] == pin.get("version") and pin["name"] in (r["name"], r["id"])), None)
        if hit:
            return hit
        raise InstallError(f"The {kind} {pin.get('id') or pin.get('name')} {pin.get('version')} this pairing is made of is not in the catalogue. Refresh the catalogue.", 404, "missing_component")
    for r in source.load()["releases"]:
        if r["kind"] == kind and r["id"] == pin["id"] and r["version"] == pin["version"] and r.get("manifest_sha256") == want:
            return r
    repo, rev = pin.get("repo"), pin.get("revision")
    if not repo or not rev:
        raise InstallError(f"The pairing pins {pin['id']} {pin['version']} without a repository and revision, and it is not in the catalogue with that checksum.", 422, "missing_component")
    url = f"{hf_endpoint()}/{repo}/resolve/{rev}/fylume-release.json"
    try:
        raw = fetch_bytes(url)
    except DownloadError as e:
        raise InstallError(f"The {kind} {pin['id']} {pin['version']} this pairing needs could not be fetched: {e}", 502, "unreachable")
    if mf.sha256_bytes(raw) != want:
        raise InstallError(f"The manifest of {pin['id']} {pin['version']} at {repo}@{rev[:10]} does not match the checksum the pairing pins "
                           f"(got {mf.sha256_bytes(raw)[:16]}…, expected {want[:16]}…). It was not installed.", 422, "checksum_mismatch")
    try:
        doc = json.loads(raw.decode("utf-8"))
        prim = (doc.get("hosting") or {}).get("primary") if isinstance(doc, dict) else None
        if isinstance(prim, dict) and prim.get("type") == "huggingface" and not prim.get("revision") and prim.get("repo") == repo:
            prim["revision"] = rev            # a manifest cannot name the commit that holds it: the pin that fetched it does
        r = mf.parse(doc)
        r["manifest_sha256"] = want
    except (ValueError, UnicodeDecodeError, mf.ManifestError) as e:
        raise InstallError(str(e), 422, "invalid")
    if r["kind"] != kind or r["id"] != pin["id"] or r["version"] != pin["version"]:
        raise InstallError(f"{repo}@{rev[:10]} holds {r['kind']} {r['id']} {r['version']}, not the {kind} {pin['id']} {pin['version']} the pairing pins.", 422, "revision_mismatch")
    return r


def studio_payload_problem(mpn_r: dict) -> str | None:
    """Why a pairing cannot answer from this network release, or None when it carries a usable Studio payload."""
    if any(p["format"] == "studio-store" and p["ok"] for p in mpn_r.get("payloads") or []):
        return None
    have = [p["format"] for p in mpn_r.get("payloads") or []]
    bad = next((p["reason"] for p in mpn_r.get("payloads") or [] if p["format"] == "studio-store" and not p["ok"]), None)
    return (f"{mpn_r['name']} {mpn_r['version']} has no Studio payload (studio-store), so a pairing cannot answer from it"
            + (f": {bad}" if bad else (f"; it carries only {', '.join(have)}, which Studio can explore (read-only) but not answer from" if have else ""))
            + ". Ask for a release that carries studio/network/yaob-n10k.sqlite, or use a network you imported.")


def install_model(r: dict, progress: Progress | None = None, cancel: threading.Event | None = None, core_endpoint: str | None = None,
                  core_file: str | None = None) -> dict:
    """Install a prebuilt pairing: its network (the Studio payload, which is what answers), its core (or, when `core_endpoint` names a
    configured server, no core download), its pinned retrieval encoder if it has one, and a pairing that refers to all of them. Installed
    networks and cores stay usable with anything else."""
    from ..library import models
    check_compatible(r)
    d = r["detail"]
    mpn_r = resolve_pin("mpn", d["mpn"])
    check_compatible(mpn_r)
    why = studio_payload_problem(mpn_r)
    if why:
        raise InstallError(why, 422, "no_studio_payload")
    core_r = enc_r = None
    if not core_endpoint:
        core_r = resolve_pin("core", d["core"])
        check_compatible(core_r)
    if d.get("encoder"):
        enc_r = resolve_pin("core", d["encoder"])
        check_compatible(enc_r)
    # room for everything before downloading anything
    parts = [(mpn_r, None, "studio-store")] + ([(core_r, core_file or d["core"].get("file"), None)] if core_r else []) + \
            ([(enc_r, d["encoder"].get("file"), None)] if enc_r else [])
    need = sum(plan(x, f, fmt=fm)["remaining_bytes"] for x, f, fm in parts)
    free = free_bytes(releases_dir() if releases_dir().exists() else paths.data_dir())
    if free < need + MARGIN:
        raise DownloadError("disk_space", f"{r['name']} needs {fmt_bytes(need)} and {fmt_bytes(free)} is free. Nothing was downloaded.", 507)
    n = len(parts)

    def sub(i):
        def p(label, done, tot):
            if progress:
                progress(f"{i + 1}/{n} {label}", done, tot)
        return p
    mrec = install(mpn_r, sub(0), cancel, fmt="studio-store")
    crec = install(core_r, sub(1), cancel, core_file or d["core"].get("file")) if core_r else None
    erec = install(enc_r, sub(n - 1), cancel, d["encoder"].get("file")) if enc_r else None
    if erec and mrec.get("project"):         # the router was trained for this encoder: say which file that is
        from ..library import cores, networks
        e = cores.get(erec["core_id"])
        networks.set_router_encoder(mrec["project"], {"name": e["name"], "sha256": e["sha256"]})
    return models.create_from_release(r, mrec, crec, core_endpoint, erec)


# ------------------------------------------------------------------------------------------------ a folder the user already has
def install_local_fgmpn(path: str | Path, name: str | None = None, copy: bool = False, progress: Progress | None = None,
                        cancel: threading.Event | None = None) -> dict:
    """Register an FG-MPN store folder from this computer. It is verified (every segment's size and SHA-256 against the store's
    own manifest) before it is accepted. By default it is used where it is; `copy` puts a copy in Studio's folder."""
    from ..mpnnet import fgmpn_local
    src = Path(path).expanduser().resolve()
    try:
        info = fgmpn_local.inspect_store(src)
    except fgmpn_local.FgError as e:
        raise InstallError(str(e), 422, "invalid")
    if not info["ok"]:
        raise InstallError("The store is missing parts: " + "; ".join((info["missing"] + info["wrong_size"])[:3]) + ".", 422, "missing_component")
    v = fgmpn_local.verify_store(src, progress)
    if not v["ok"]:
        raise InstallError("The store does not verify: " + "; ".join(v["problems"][:3]) + ". It was not added.", 422, "verify")
    rid = "local-" + (re.sub(r"[^a-z0-9]+", "-", (name or src.name).lower()).strip("-")[:40] or "network")
    version = time.strftime("%Y%m%d-%H%M%S")
    final = release_dir("mpn", rid, version)
    stage = final.with_name(final.name + ".partial")
    stage.mkdir(parents=True, exist_ok=True)
    rec = {"kind": "mpn", "id": rid, "name": name or src.name, "version": version, "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "studio": __version__, "format": "fgmpn", "origin": "local", "read_only": True, "nodes": info["nodes"], "bytes": info["bytes"],
           "licence": {"spdx": None, "notes": "Not stated: a folder imported from this computer."}, "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "store": {"nodes": info["nodes"], "segments": info["segments"], "generation": info["generation"], "manifest": info["manifest"]}, "warnings": []}
    if copy:
        free = free_bytes(releases_dir() if releases_dir().exists() else paths.data_dir())
        if free < info["bytes"] + MARGIN:
            shutil.rmtree(stage, ignore_errors=True)
            raise DownloadError("disk_space", f"A copy needs {fmt_bytes(info['bytes'])} and {fmt_bytes(free)} is free. Add it without copying instead.", 507)
        shutil.copytree(src, stage / "store")
    else:
        rec["store_path"] = str(src)
    _write_json(stage / "INSTALL.json", rec)
    os.replace(stage, final)
    return get_installed("mpn", rid, version)
