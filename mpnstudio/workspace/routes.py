"""HTTP API of the project workspace.

/api/projects/*   the home screen: list, create (blank, duplicate, link a network, import), recent, rename, trash
/api/ws/*         the open linked network or fork: sources, overview, search, browse, node, relationships, graph,
                  edits, undo/redo, history, versions, compare, export
/api/versions/*   named versions and comparisons of an open project with its own store

Everything that reads a large network is bounded and paged by the reader underneath; no route loads a network.
"""
from __future__ import annotations

import os
import resource
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..desktop import paths as dpaths
from ..desktop import projects as P
from . import props
from ..desktop import settings as dsettings
from ..mpnnet.service import Local, NetError, Remote
from . import forkfile, versions
from .network import KNOWN, Workspace, describe_source, identify, known_for, probe_source
from .overlay import OverlayError


def _abs(p: str | None, what: str = "file or folder") -> str:
    p = (p or "").strip()
    if not p or not os.path.isabs(os.path.expanduser(p)):
        raise P.ProjectError(f"Choose a {what} with a full path.")
    return os.path.expanduser(p)


def _expand(ids: list[str]) -> set[str]:
    """Pack ids plus the other ids of the same known network (a pack and the live store it was made from)."""
    k = known_for(ids)
    return set(ids) | set(k[1]["pack_ids"] if k else [])


def _rss() -> int | None:
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:  # noqa: BLE001
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def register(app: FastAPI, ctx: dict, get_store: Callable, open_project: Callable[[str], dict], close_project: Callable[[], None],
             start_task: Callable) -> None:

    @app.exception_handler(OverlayError)
    async def _overlay_error(_: Request, e: OverlayError):
        return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.exception_handler(versions.VersionError)
    async def _version_error(_: Request, e: versions.VersionError):
        return JSONResponse({"error": str(e)}, status_code=e.status)

    def ws() -> Workspace:
        w = ctx.get("ws")
        if w is None:
            raise P.ProjectError("No linked network or fork is open.", 409)
        return w

    # ================================================================== home screen
    def recent() -> list[dict]:
        rec = dsettings.load().get("recent_projects") or []
        have = {p["id"]: p for p in P.list_projects()}
        return [{**have[r["id"]], "opened": r["opened"]} for r in rec if r.get("id") in have][:10]

    def _models_by_network() -> dict:
        try:
            from ..library import models
            out = {}
            for m in models.list_models():
                nid = (m.get("network") or {}).get("id")
                if nid:
                    out.setdefault(nid, []).append({"id": m["id"], "name": m["name"], "state": (m.get("status") or {}).get("state"),
                                                    "core": (m.get("core") or {}).get("name")})
            return out
        except Exception:  # noqa: BLE001 - the home screen still lists projects if the model library is unreadable
            return {}

    @app.get("/api/projects")
    def projects_list():
        rec = {r["id"]: r["opened"] for r in dsettings.load().get("recent_projects") or []}
        mods = _models_by_network()
        items = [{**p, "opened": rec.get(p["id"]), "models": mods.get(p["id"], [])} for p in P.list_projects()]
        return {"projects": items, "recent": recent(), "current": ctx.get("project"), "location": str(dpaths.projects_dir())}

    @app.post("/api/projects/blank")
    def projects_blank(body: dict):
        name = (body.get("name") or "").strip()
        if not name:
            raise P.ProjectError("Give the project a name.")
        slug = P.create(name, body.get("description", ""))
        core = body.get("core")
        if core:                      # a model = this network + that core; nothing is trained or copied
            from ..library import models
            models.create(name, "", core, slug, None)
        if body.get("open", True):
            open_project(slug)
        return {"id": slug}

    @app.post("/api/projects/{slug}/duplicate")
    def projects_duplicate(slug: str, body: dict):
        """Duplicate any project. A linked network or a fork becomes a new fork (the network is referenced, not copied);
        a project with its own store is copied whole, history included."""
        m = P.read_manifest(slug)
        name = (body.get("name") or "").strip()
        if not name:
            raise P.ProjectError("Give the new project a name.")
        kind = P.kind_of(m)
        if kind == "linked":
            new = P.create_fork(slug, name)
        elif kind == "fork":
            w = ctx.get("ws")
            if w is not None and ctx.get("project") == slug and w.overlay:
                w.overlay.checkpoint()
            new = P.create_fork(slug, name, overlay_from=P.overlay_path(slug))
        else:
            from ..library import networks
            st = get_store() if ctx.get("project") == slug else None
            new = networks.duplicate(slug, name, st)["id"]
        if body.get("open", True):
            open_project(new)
        return {"id": new, "kind": P.kind_of(P.read_manifest(new))}

    @app.post("/api/projects/link/probe")
    def projects_link_probe(body: dict):
        """Check a folder or address before linking it: what network is there, how big, how fast it answered."""
        mode, target = body.get("mode"), (body.get("target") or "").strip()
        if mode == "local":
            target = _abs(target, "folder")
        elif mode != "remote":
            raise P.ProjectError("mode must be local or remote.")
        t = time.perf_counter()
        c = Remote(target) if mode == "remote" else Local(target)
        try:
            ident = identify(c)
        finally:
            c.close()
        k = known_for(ident["pack_ids"])
        return {"mode": mode, "target": target, "identity": ident, "official_name": k[1]["name"] if k else None,
                "ms": round((time.perf_counter() - t) * 1000, 1)}

    @app.post("/api/projects/link")
    def projects_link(body: dict):
        """Link a large network as a protected project. body: {name?, sources: [{mode, target, label?}]}. The first
        source that answers defines the network's identity; the others must hold the same network."""
        sources = body.get("sources") or []
        if not sources:
            raise P.ProjectError("Give at least one folder or address.")
        ident, errors = None, []
        for s in sources:
            if s.get("mode") == "local":
                s["target"] = _abs(s.get("target"), "folder")
            try:
                c = Remote(s["target"]) if s.get("mode") == "remote" else Local(s["target"])
            except NetError as e:
                errors.append(f"{describe_source(s)}: {e}")
                continue
            try:
                i = identify(c)
            finally:
                c.close()
            if ident is None:
                ident = i
            elif not (_expand(i["pack_ids"]) & _expand(ident["pack_ids"])):
                raise P.ProjectError(f"{describe_source(s)} holds a different network ({i.get('name')}) from the first source.")
        if ident is None:
            raise P.ProjectError("None of the sources could be opened. " + " ".join(errors))
        k = known_for(ident["pack_ids"])
        identity = {**ident, "pack_ids": sorted(set(ident["pack_ids"]) | set(k[1]["pack_ids"] if k else [])),
                    "name": k[1]["name"] if k else ident.get("name")}
        if k:
            identity["known"] = k[0]
        name = (body.get("name") or "").strip() or identity["name"]
        slug = P.create_linked(name, sources, identity, k[1]["description"] if k else "")
        if body.get("open", True):
            open_project(slug)
        return {"id": slug, "identity": identity, "unreachable": errors}

    @app.post("/api/projects/import")
    def projects_import(body: dict):
        """Open anything MPN Studio can read as a project: a fork file, a backup, a Yaob release or bundle folder, a
        store file, an installed network folder (linked, not copied) or a .mpn model (opened as a model)."""
        path = Path(_abs(body.get("path")))
        name = (body.get("name") or "").strip() or None
        from ..library import networks
        if path.suffix == ".mpnfork":
            slug = forkfile.import_file(path, name)
        elif path.suffix == ".mpnbak":
            slug = P.restore(path, name)
        elif path.is_dir() and ((path / "parts" / "p00.sqlite").exists() or (path / "p00.sqlite").exists()):
            return projects_link({"name": name, "sources": [{"mode": "local", "target": str(path)}], "open": body.get("open", True)})
        elif path.is_dir() and (path / "network" / "yaob-n10k.sqlite").exists():
            slug = networks.import_yaob_release(path, name or "Yaob N10K")["id"]
        elif path.is_dir():
            slug = P.import_bundle_dir(path, name)
        elif path.suffix in (".sqlite", ".db"):
            slug = P.import_store_file(path, name or path.stem)
        elif path.suffix == ".mpn":
            raise P.ProjectError("A .mpn file is a finished model: open it with Open model file, which runs it without importing.", 400)
        else:
            raise P.ProjectError(f"MPN Studio cannot open {path.name}. It opens fork files (.mpnfork), backups (.mpnbak), "
                                 "network folders, Yaob releases, bundle folders and store files.")
        if body.get("open", True):
            open_project(slug)
        return {"id": slug}

    @app.get("/api/projects/{slug}/availability")
    def projects_availability(slug: str):
        """Can this linked network or fork be read right now? Checks each source cheaply (folder present, one request)."""
        m = P.read_manifest(slug)
        srcs = (m.get("network") or {}).get("sources") or []
        out = [{"index": i, "label": describe_source(s), "mode": s["mode"], **probe_source(s, timeout=3.0)} for i, s in enumerate(srcs)]
        return {"sources": out, "available": any(s["available"] for s in out)}

    @app.post("/api/projects/{slug}/open")
    def projects_open(slug: str):
        m = open_project(slug)
        return {"id": slug, "kind": P.kind_of(m), "workspace": ctx["ws"].state() if ctx.get("ws") else None}

    @app.post("/api/projects/{slug}/rename")
    def projects_rename(slug: str, body: dict):
        P.rename(slug, body.get("name", ""))
        return {"ok": True}

    @app.post("/api/projects/{slug}/trash")
    def projects_trash(slug: str, body: dict):
        m = P.read_manifest(slug)
        if (body.get("confirm") or "") != m.get("name"):
            raise P.ProjectError("Type the project's name to confirm.", 409)
        forks = [p["name"] for p in P.list_projects() if p.get("base") == slug]
        if forks and not body.get("keep_forks_ok"):
            raise P.ProjectError(f"{', '.join(forks)} use this network. They keep working (they name the network's sources "
                                 "themselves); confirm to continue.", 409)
        if ctx.get("project") == slug:
            close_project()
        return {"moved_to": str(P.trash(slug))}

    # ================================================================== the open linked network / fork
    @app.get("/api/ws/state")
    def ws_state():
        w = ws()
        m = P.read_manifest(w.slug)
        return {**w.state(), "id": w.slug, "name": m.get("name"), "description": m.get("description"), "base_project": m.get("base_project"),
                "project_bytes": sum(p.stat().st_size for p in P.project_dir(w.slug).rglob("*") if p.is_file()),
                "process_rss": _rss()}

    @app.post("/api/ws/connect")
    def ws_connect(body: dict):
        w = ws()
        prefer = body.get("source")
        st = w.connect(int(prefer) if prefer is not None else None)
        if prefer is not None and st["source"] and st["source"]["index"] == int(prefer):
            m = P.read_manifest(w.slug)
            m["network"]["preferred"] = int(prefer)
            P.write_manifest(w.slug, m)
            w.net["preferred"] = int(prefer)
        return st

    @app.post("/api/ws/sources")
    def ws_sources_add(body: dict):
        """Add a place to read this network from (another computer or a local copy). It must hold the same network."""
        w = ws()
        mode, target = body.get("mode"), (body.get("target") or "").strip()
        if mode == "local":
            target = _abs(target, "folder")
        c = Remote(target) if mode == "remote" else Local(target)
        try:
            i = identify(c)
        finally:
            c.close()
        want = set((w.net.get("identity") or {}).get("pack_ids") or [])
        if want and not (want & set(i["pack_ids"])):
            raise P.ProjectError(f"That holds a different network ({i.get('name')}).")
        m = P.read_manifest(w.slug)
        m["network"]["sources"].append({"mode": mode, "target": target, "label": (body.get("label") or "").strip() or None})
        P.write_manifest(w.slug, m)
        w.net["sources"] = m["network"]["sources"]
        return w.connect()

    @app.post("/api/ws/save")
    def ws_save():
        """Edits are written as they are made; Save folds the change file's log into it and reports what is stored."""
        w = ws()
        if w.overlay:
            w.overlay.checkpoint()
        m = P.read_manifest(w.slug)
        m["summary"] = {"saved": P.now(), **({"revision": w.overlay.head()} if w.overlay else {})}
        P.write_manifest(w.slug, m)
        return {"saved": m["summary"]["saved"], "changes": w.overlay.stats() if w.overlay else None}

    @app.get("/api/ws/probe")
    def ws_probe():
        return {"sources": [{"index": i, "label": describe_source(s), "mode": s["mode"], "target": s["target"], **probe_source(s)}
                            for i, s in enumerate(ws().sources)]}

    @app.get("/api/ws/overview")
    def ws_overview():
        return ws().overview()

    @app.get("/api/ws/search")
    def ws_search(request: Request):
        q = dict(request.query_params)
        return ws().search(str(q.pop("q", ""))[:300], q)

    @app.get("/api/ws/browse")
    def ws_browse(request: Request):
        return ws().browse(dict(request.query_params))

    def _nid(n: str) -> str:
        if not n or len(n) > 80 or "/" in n:
            raise HTTPException(400, "Not a node id.")
        return n

    @app.get("/api/ws/node/{nid}")
    def ws_node(nid: str):
        return props.named(ws().node(_nid(nid)))

    @app.get("/api/ws/node/{nid}/relationships")
    def ws_rels(nid: str, request: Request):
        return props.named(ws().relationships(_nid(nid), dict(request.query_params)))

    @app.get("/api/ws/node/{nid}/graph")
    def ws_graph(nid: str, limit: int = 40):
        return props.named(ws().graph(_nid(nid), max(4, min(limit, 120))))

    # ------------------------------------------------------------------ edits (forks)
    @app.post("/api/ws/nodes")
    def ws_create(body: dict):
        return props.named(ws().create_node(body))

    @app.patch("/api/ws/node/{nid}")
    def ws_update(nid: str, body: dict):
        return props.named(ws().update_node(_nid(nid), body))

    @app.post("/api/ws/nodes/delete")
    def ws_delete(body: dict):
        ids = [_nid(str(x)) for x in body.get("ids") or []]
        if len(ids) > 25 and body.get("confirm") != len(ids):
            raise P.ProjectError(f"Confirm deleting {len(ids):,} nodes.", 409)
        return ws().delete_nodes(ids)

    @app.post("/api/ws/node/{nid}/duplicate")
    def ws_dup(nid: str, body: dict):
        return props.named(ws().duplicate_node(_nid(nid), body.get("title")))

    @app.post("/api/ws/edges")
    def ws_edge_add(body: dict):
        return props.named(ws().add_edge(_nid(body.get("src", "")), _nid(body.get("dst", "")), body.get("rel", "")))

    @app.post("/api/ws/edges/remove")
    def ws_edge_remove(body: dict):
        return props.named(ws().remove_edge(_nid(body.get("src", "")), str(body.get("rel", ""))[:120], _nid(body.get("dst", ""))))

    def _ov():
        w = ws()
        if not w.overlay:
            raise OverlayError("This network is protected. Duplicate it to make an editable fork.", 409)
        return w.overlay

    @app.post("/api/ws/undo")
    def ws_undo():
        r = _ov().undo()
        return {"revision": r, "changes": _ov().stats()}

    @app.post("/api/ws/redo")
    def ws_redo():
        r = _ov().redo()
        return {"revision": r, "changes": _ov().stats()}

    @app.get("/api/ws/history")
    def ws_history(limit: int = 100):
        return {"history": _ov().history(limit)}

    @app.get("/api/ws/versions")
    def ws_versions():
        ov = _ov()
        return {"versions": ov.versions(), "head": ov.head()}

    @app.post("/api/ws/versions")
    def ws_version_save(body: dict):
        return _ov().save_version(body.get("name", ""), body.get("note", ""))

    def _titles(ids: list[str]) -> dict:
        w = ws()
        out = {}
        for i in ids[:200]:
            try:
                out[i] = w._base_node(i)["node"]["title"]
            except Exception:  # noqa: BLE001
                out[i] = None
        return out

    @app.get("/api/ws/compare")
    def ws_compare(a: str = "original", b: str = "current"):
        ov = _ov()
        ra, rb = ov.version_rev(a), ov.version_rev(b)
        d = ov.compare(ra, rb)
        need = [n["id"] for n in d["nodes"] if not (n["before_doc"] or n["after_doc"])]
        need += [x for e in d["relationships"] for x in (e["src"], e["dst"])]
        names = {}
        for n in d["nodes"]:
            doc = n["after_doc"] or n["before_doc"]
            if doc:
                names[n["id"]] = doc.get("title")
        missing = [i for i in dict.fromkeys(need) if i not in names]
        names.update(_titles(missing))
        out_nodes = []
        w = ws()
        for n in d["nodes"][:300]:        # an edit of a base node: compare with the base itself
            for side, other in (("before", "after"), ("after", "before")):
                if n[side] == "base" and n[other + "_doc"] and not n[other + "_doc"].get("created_in_fork"):
                    try:
                        b = w._base_node(n["id"])
                    except Exception:  # noqa: BLE001 - the base may be unreachable; then fields stay unknown
                        b = None
                    if b:
                        n[side + "_doc"] = b["node"]
        for n in d["nodes"]:
            b4, af = n["before"], n["after"]
            created = bool((n["after_doc"] or n["before_doc"] or {}).get("created_in_fork"))
            if af == "del":
                change = "deleted"
            elif b4 == "del":
                change = "restored"
            elif created and b4 == "base":
                change = "added"
            elif created and af == "base":
                change = "removed"
            else:
                change = "edited"
            fields = []
            if n["before_doc"] and n["after_doc"]:
                fields = [k for k in ("title", "summary", "aliases", "domain", "kind", "notes") if n["before_doc"].get(k) != n["after_doc"].get(k)]
                if _brief(n["before_doc"])["statements"] != _brief(n["after_doc"])["statements"]:
                    fields.append("statements")
            out_nodes.append({"id": n["id"], "change": change, "title": names.get(n["id"]), "fields": fields,
                              "before": _brief(n["before_doc"]), "after": _brief(n["after_doc"])})
        rels = [{**e, "src_title": names.get(e["src"]), "dst_title": names.get(e["dst"]),
                 "change": "added" if e["after"] == "add" else "removed" if e["after"] == "del" else
                 ("removal undone" if e["before"] == "del" else "addition undone")} for e in d["relationships"]]
        return {"from": a, "to": b, "from_rev": ra, "to_rev": rb, "nodes": out_nodes, "relationships": rels, "truncated": d["truncated"]}

    @app.post("/api/ws/versions/open")
    def ws_version_open(body: dict):
        """A saved version of a fork as its own new fork project (the original fork is not changed)."""
        w = ws()
        ov = _ov()
        rev = ov.version_rev(body.get("version"))
        name = (body.get("name") or "").strip() or f"{P.read_manifest(w.slug).get('name')} ({body.get('version')})"
        ov.checkpoint()
        new = P.create_fork(w.slug, name, overlay_from=P.overlay_path(w.slug), upto_rev=rev,
                            origin={"kind": "version", "of": w.slug, "version": body.get("version"), "rev": rev, "at": P.now()})
        if body.get("open", True):
            open_project(new)
        return {"id": new}

    @app.post("/api/ws/export")
    def ws_export(body: dict):
        w = ws()
        if w.overlay:
            w.overlay.checkpoint()
        return forkfile.export(w.slug, _abs(body.get("dest"), "file name"))

    @app.get("/api/ws/resources")
    def ws_resources():
        """Measured: this process's memory, the project folder's size, the base network's storage, the last query time."""
        w = ws()
        ident = w.net.get("identity") or {}
        return {"process_rss": _rss(), "project_bytes": sum(p.stat().st_size for p in P.project_dir(w.slug).rglob("*") if p.is_file()),
                "base_storage_bytes": ident.get("storage_bytes"), "last_query_ms": w.last_ms,
                "source": w.state().get("source")}

    # ================================================================== versions of projects with their own store
    def _store():
        st = get_store()
        if st is None:
            raise P.ProjectError("No project with its own network is open.", 409)
        return st

    @app.get("/api/versions")
    def v_list():
        st = _store()
        return {"versions": versions.list_versions(st), "head": st.head()}

    @app.post("/api/versions")
    def v_save(body: dict):
        return versions.save_version(_store(), body.get("name", ""), body.get("note", ""))

    @app.get("/api/versions/compare")
    def v_compare(a: str = "", b: str = "current"):
        st = _store()
        ra = versions.version_rev(st, a) if a not in ("", "original") else 0
        return {"from": a or "original", "to": b, **versions.compare(st, ra, versions.version_rev(st, b))}

    @app.post("/api/versions/open")
    def v_open(body: dict):
        """A saved version as a new project: a copy of the store whose history ends at that version."""
        slug = ctx.get("project")
        st = _store()
        rev = versions.version_rev(st, body.get("version"))
        name = (body.get("name") or "").strip() or f"{P.read_manifest(slug).get('name')} ({body.get('version')})"
        st.checkpoint_wal()
        new = P.unique_slug(name)
        d = P.project_dir(new)
        d.mkdir(parents=True)
        try:
            P._copy_store(P.store_path(slug), d / P.STORE_FILE)
            info = versions.cut_history(d / P.STORE_FILE, rev)
            m = P._new_manifest(name, new, {"kind": "version", "of": slug, "version": body.get("version"), "rev": rev, "at": P.now()})
            P.write_manifest(new, m)
        except Exception:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
            raise
        if body.get("open", True):
            open_project(new)
        return {"id": new, **info}


def _brief(doc: dict | None) -> dict | None:
    if not doc:
        return None
    k = ((doc.get("cells") or {}).get("knowledge") or {}).get("statements") or []
    return {"title": doc.get("title"), "summary": doc.get("summary"), "domain": doc.get("domain"), "kind": doc.get("kind"),
            "aliases": doc.get("aliases"), "notes": doc.get("notes"),
            "statements": [s.get("text") for s in k if isinstance(s, dict)][:20]}
