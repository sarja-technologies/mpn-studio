"""HTTP API for official releases and cores on servers.

  /api/catalogue            list (cached), refresh, import a manifest file, remove an imported one, plan an install, install
  /api/releases             what is installed; verify; remove; add an FG-MPN folder from this computer
  /api/core-servers         cores on OpenAI-compatible servers: list, add, change, remove, test (saved or not yet saved)
  /api/library/models/{id}/rebind     apply a changed core to a built pairing without rebuilding
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..catalogue import install, manifest as mf, source
from ..catalogue.download import DownloadError
from ..library import coreconf, models, networks
from ..mpnnet import fgmpn_local


def _abs(p: str | None, what: str = "file or folder") -> str:
    import os
    if not p or not os.path.isabs(p):
        raise HTTPException(400, f"Choose a {what} with a full path.")
    return p


def view(r: dict, index: dict | None = None) -> dict:
    """A release for the UI: what a person needs to choose, without the raw manifest."""
    d = r["detail"] or {}
    inst = install.get_installed(r["kind"], r["id"], r["version"])
    prim = r["hosting"].get("primary") or {}
    out = {"id": r["id"], "kind": r["kind"], "name": r["name"], "version": r["version"], "released_at": r["released_at"], "licence": r["licence"],
           "hosting": prim.get("type"), "repo": prim.get("repo"), "totals": r["totals"], "summary": r.get("summary"), "status": r.get("status"),
           "problems": r["problems"], "compat": r["compat"], "origin": r.get("origin"), "manifest_sha256": r.get("manifest_sha256"),
           "download_only": r.get("download_only"), "links": r.get("links") or {},
           "installed": {k: inst.get(k) for k in ("installed_at", "verified_at", "core_id", "project", "format", "path")} if inst else None}
    if r["kind"] == "mpn":
        out["mpn"] = {k: d.get(k) for k in ("format", "format_version", "nodes", "nodes_validated", "relationships", "read_only", "studio_min", "verification", "source")}
        out["payloads"] = []
        for p in r.get("payloads") or []:
            i = install.get_installed("mpn", r["id"], r["version"], p["format"])
            plan = None
            if p["ok"]:
                try:
                    plan = install.plan(r, fmt=p["format"])
                except install.InstallError:
                    pass
            out["payloads"].append({"format": p["format"], "ok": p["ok"], "reason": p["reason"], "bytes": p["bytes"], "plan": plan,
                                    "installed": bool(i), "project": (i or {}).get("project")})
        out["default_format"] = install.default_format(r)
        out["plan"] = next((p["plan"] for p in out["payloads"] if p["format"] == out["default_format"]), None)
        out["installed"] = next(({"format": p["format"], "installed_at": None} for p in out["payloads"] if p["installed"]), out["installed"])
    elif r["kind"] == "core":
        sizes = {f["path"]: f["bytes"] for f in r["files"]}
        out["core"] = {**{k: d.get(k) for k in ("parameters", "architecture", "derivation", "fine_tuned", "context_length", "api", "base_model")},
                       "formats": [{"kind": f.get("kind"), "quant": f.get("quant"), "file": f.get("file"), "bytes": sizes.get(f.get("file"))} for f in d.get("formats") or []]}
    else:
        out["model"] = {"pairing": d.get("pairing"), "defaults": d.get("defaults"), "runtime": d.get("runtime"), "tested": d.get("tested")}
        for part in ("mpn", "core", "encoder"):                # is each pinned component already installed, and does the catalogue know it?
            p = d.get(part)
            if not p:
                continue
            found = (index or {}).get((part if part != "encoder" else "core", p.get("id") or p.get("name"), p.get("version")))
            out["model"][part] = {**p, "installed": bool(found and install.get_installed(found["kind"], found["id"], found["version"])), "in_catalogue": bool(found)}
            if part == "mpn" and found:
                why = install.studio_payload_problem(found)
                if why:
                    out["model"]["blocked"] = why
    if r["kind"] == "core" and not r["problems"] and not r.get("download_only"):
        try:
            out["plan"] = install.plan(r)
        except install.InstallError:
            out["plan"] = None
    return out


def prog(t):
    def p(label, done, total):
        t.progress = {"label": label, "done": done, "total": total}
    return p


def register(app: FastAPI, start_task: Callable) -> None:
    for exc in (source.CatalogueError, install.InstallError, DownloadError, coreconf.CoreServerError, fgmpn_local.FgError, mf.ManifestError):
        @app.exception_handler(exc)
        async def _err(_: Request, e: Exception):
            return JSONResponse({"error": str(e), "code": getattr(e, "code", None)}, status_code=getattr(e, "status", 400))

    # ---------------------------------------------------------------- catalogue
    @app.get("/api/catalogue")
    def catalogue():
        c = source.load()
        index = {}
        for r in c["releases"]:
            for key in (r["id"], r["name"]):
                index.setdefault((r["kind"], key, r["version"]), r)
        return {"source": c["source"], "default_url": source.DEFAULT_URL, "releases": [view(r, index) for r in c["releases"]], "rejected": c["rejected"],
                "installed": install.installed(), "fgmpn": fgmpn_local.runtime_info(), "studio": __version__}

    @app.post("/api/catalogue/refresh")
    def catalogue_refresh():
        r = source.refresh()
        return {"fetched": r["releases"], "skipped": r["skipped"], **catalogue()}

    @app.post("/api/catalogue/import")
    def catalogue_import(body: dict):
        added = source.import_file(_abs(body.get("path"), "manifest file"))
        return {"added": added, **catalogue()}

    @app.delete("/api/catalogue/local/{kind}/{rid}/{version}")
    def catalogue_remove(kind: str, rid: str, version: str):
        return {"removed": source.remove_local(kind, rid, version)}

    @app.get("/api/catalogue/plan")
    def catalogue_plan(kind: str, id: str, version: str, file: str | None = None):
        r = source.find(kind, id, version)
        if not r:
            raise HTTPException(404, "No such release in the catalogue.")
        return install.plan(r, file) if kind != "model" else {"note": "A pairing installs its network and its core."}

    @app.post("/api/catalogue/install")
    def catalogue_install(body: dict):
        """body: {kind, id, version, file?, core_endpoint?}. Runs as a task: progress, Cancel (the part is kept), resume."""
        r = source.find(body.get("kind"), body.get("id") or "", body.get("version"))
        if not r:
            raise HTTPException(404, "That release is not in the catalogue. Refresh it, or import its manifest.")
        file, ep, fmt = body.get("file"), body.get("core_endpoint"), body.get("format")
        if ep:
            coreconf.get(ep)
        if r.get("download_only"):
            raise install.InstallError(r["download_only"]["reason"], 422, "download_only")

        def run(t):
            if r["kind"] == "model":
                return install.install_model(r, prog(t), t.cancel, ep, file)
            return install.install(r, prog(t), t.cancel, file, fmt)
        return start_task("release-install", f"Install {r['name']} {r['version']}", run).view()

    # ---------------------------------------------------------------- installed
    @app.get("/api/releases")
    def releases():
        return {"installed": install.installed(), "fgmpn": fgmpn_local.runtime_info()}

    @app.post("/api/releases/verify")
    def releases_verify(body: dict):
        k, i, v, fm = body.get("kind"), body.get("id"), body.get("version"), body.get("format")
        return start_task("release-verify", f"Verify {i} {v}", lambda t: install.verify_installed(k, i, v, prog(t), fm)).view()

    @app.delete("/api/releases/{kind}/{rid}/{version}")
    def releases_remove(kind: str, rid: str, version: str, format: str | None = None):
        return install.uninstall(kind, rid, version, format)

    @app.post("/api/releases/fgmpn")
    def releases_add_fgmpn(body: dict):
        p, name, copy = _abs(body.get("path"), "FG-MPN folder"), body.get("name"), bool(body.get("copy"))
        return start_task("release-install", f"Add {Path(p).name}", lambda t: install.install_local_fgmpn(p, name, copy, prog(t), t.cancel)).view()

    @app.post("/api/releases/{rid}/{version}/open")
    def releases_open(rid: str, version: str):
        """Explore an installed FG-MPN network: a linked, protected project whose source is the store folder on this computer."""
        rec = install.get_installed("mpn", rid, version, "fgmpn")
        if not rec:
            raise HTTPException(404, "That is not an installed FG-MPN network.")
        return {"path": str(install.store_dir(rec)), "name": rec["name"]}

    @app.post("/api/library/networks/import-mpn")
    def networks_import_mpn(body: dict):
        p = _abs(body.get("path"), ".mpn file")
        return start_task("network-import", f"Import the network of {Path(p).name}", lambda t: networks.import_mpn_network(p, body.get("name"), prog(t))).view()

    # ---------------------------------------------------------------- core servers
    @app.get("/api/core-servers")
    def servers():
        return {"servers": coreconf.list_servers()}

    @app.post("/api/core-servers")
    def servers_add(body: dict):
        return coreconf.save(body)

    @app.patch("/api/core-servers/{cid}")
    def servers_edit(cid: str, body: dict):
        return coreconf.save({**coreconf.get(cid), **body}, cid)

    @app.delete("/api/core-servers/{cid}")
    def servers_remove(cid: str):
        return coreconf.remove(cid)

    @app.post("/api/core-servers/test")
    def servers_test(body: dict):
        """Test settings that are not saved yet. With `id`, the stored key of that core is used when `api_key` is not given."""
        key = body.get("api_key") or (coreconf.api_key(body["id"]) if body.get("id") else None)
        if not key and body.get("api_key_env"):
            import os
            key = os.environ.get(body["api_key_env"])
        t = float(body.get("timeout_s") or 20)
        return coreconf.test(body.get("base_url", ""), (body.get("model_id") or "").strip() or "model", key, timeout=min(max(t, 3.0), 60.0))

    @app.post("/api/core-servers/{cid}/test")
    def servers_test_saved(cid: str):
        return coreconf.test_saved(cid)

    # ---------------------------------------------------------------- change a pairing's core without a rebuild
    @app.post("/api/library/models/{mid}/rebind")
    def model_rebind(mid: str, body: dict | None = None):
        dev = (body or {}).get("device", "auto")
        from ..library import runner
        runner.RUNS.stop(mid)
        return start_task("build", f"Apply the core to {models.get(mid)['name']}", lambda t: models.rebind(mid, log=lambda s: t.log.append(s), device=dev)).view()
