"""HTTP API for the model builder: cognitive cores, node networks, models, builds, exports, runs, tests, and bulk
node operations on the open network. Long operations run as tasks with progress and, where it is safe, cancellation.

Everything under /api/library/ works with no network open.
"""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from ..desktop import paths as dpaths
from ..desktop import projects as dprojects
from ..library import coreconf, cores, models, mpnfile, networks, runner
from ..store import netops
from ..store.db import sid


# ------------------------------------------------------------------------------------------------ tasks
class Task:
    def __init__(self, kind: str, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind, self.title = kind, title
        self.state = "running"
        self.progress = {"label": "", "done": 0, "total": 0}
        self.log: list[str] = []
        self.result = None
        self.error = None
        self.cancel = threading.Event()
        self.started, self.finished = time.time(), None

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state, "progress": self.progress,
                "log": self.log[-40:], "result": self.result, "error": self.error, "started": self.started, "finished": self.finished,
                "cancellable": self.kind in ("bulk-delete", "build", "bench", "network-install", "release-install")}


TASKS: dict[str, Task] = {}


def start_task(kind: str, title: str, fn: Callable[[Task], object]) -> Task:
    t = Task(kind, title)
    TASKS[t.id] = t

    def run():
        try:
            t.result = fn(t)
            t.state = "cancelled" if t.cancel.is_set() and t.result is None else "done"
        except netops.Cancelled:
            t.state, t.error = "cancelled", "Cancelled. Nothing was changed."
        except Exception as e:
            t.state, t.error = "failed", str(e) or type(e).__name__
            t.log.append(traceback.format_exc()[-2000:])
        t.finished = time.time()
    threading.Thread(target=run, daemon=True, name=f"task-{kind}").start()
    return t


def _abs(p: str | None, what: str = "file or folder") -> str:
    if not p or not os.path.isabs(p):
        raise HTTPException(400, f"Choose a {what} with a full path.")
    return p


def register(app: FastAPI, get_store: Callable, open_project: Callable[[str], None], current_project: Callable[[], str | None]) -> None:
    client = httpx.AsyncClient(timeout=httpx.Timeout(3600.0, connect=3.0))

    for exc in (cores.CoreError, models.ModelError, runner.RunError):
        @app.exception_handler(exc)
        async def _lib_error(_: Request, e: Exception):
            return JSONResponse({"error": str(e)}, status_code=getattr(e, "status", 400))

    @app.exception_handler(mpnfile.MpnFileError)
    async def _mpn_error(_: Request, e: Exception):
        return JSONResponse({"error": str(e)}, status_code=422)

    # -------------------------------------------------------------------------------- overview
    @app.get("/api/library/overview")
    def overview():
        from ..engine.runtime import available
        from ..catalogue import install
        from . import catalogue_routes
        return {"models": models.list_models(), "cores": cores.list_cores(), "networks": networks.list_networks(), "runtime": available(),
                "runs": runner.RUNS.status(), "opened": _opened_list(), "open_network": current_project(),
                "core_servers": coreconf.list_servers(), "releases": [{k: v for k, v in r.items() if k != "hosting"} for r in install.installed()]}

    # -------------------------------------------------------------------------------- tasks
    @app.get("/api/library/tasks")
    def tasks():
        return {"tasks": [t.view() for t in sorted(TASKS.values(), key=lambda t: -t.started)][:50]}

    @app.get("/api/library/tasks/{tid}")
    def task(tid: str):
        t = TASKS.get(tid)
        if not t:
            raise HTTPException(404, "No such task.")
        return t.view()

    @app.post("/api/library/tasks/{tid}/cancel")
    def task_cancel(tid: str):
        t = TASKS.get(tid)
        if not t:
            raise HTTPException(404, "No such task.")
        t.cancel.set()
        return t.view()

    def prog(t: Task):
        def p(label, done, total):
            t.progress = {"label": label, "done": done, "total": total}
        return p

    # -------------------------------------------------------------------------------- cores
    @app.get("/api/library/cores")
    def core_list():
        return {"cores": cores.list_cores()}

    @app.post("/api/library/cores/inspect")
    def core_inspect(body: dict):
        p = _abs(body.get("path"))
        if os.path.isdir(p):
            return {"kind": "hf", **cores.inspect_hf(p), "converter": cores.converter()}
        from ..library import gguf
        try:
            info = gguf.describe(p)
        except gguf.GGUFError as e:
            raise cores.CoreError(str(e))
        return {"kind": "gguf", "info": info, "compatibility": cores.compatibility(info)}

    @app.post("/api/library/cores/import")
    def core_import(body: dict):
        p = _abs(body.get("path"))
        mode = body.get("mode", "copy")
        if os.path.isdir(p):
            t = start_task("core-import", f"Convert {Path(p).name}", lambda t: cores.import_hf(p, body.get("outtype", "q8_0"), body.get("name"),
                                                                                                 log=lambda s: t.log.append(s)))
        else:
            t = start_task("core-import", f"Import {Path(p).name}", lambda t: cores.import_gguf(p, mode=mode, name=body.get("name"), progress=prog(t),
                                                                                                attach_to=body.get("attach_to")))
        return t.view()

    @app.get("/api/library/cores/{cid}")
    def core_get(cid: str):
        return {**cores.get(cid), "used_by": cores.users_of(cid)}

    @app.patch("/api/library/cores/{cid}")
    def core_rename(cid: str, body: dict):
        m = json.loads(cores._meta_path(cid).read_text())
        if body.get("name"):
            m["name"] = str(body["name"]).strip()[:120]
        cores._write_meta(cid, m)
        return cores.get(cid)

    @app.delete("/api/library/cores/{cid}")
    def core_remove(cid: str):
        return cores.remove(cid)

    # -------------------------------------------------------------------------------- networks
    @app.get("/api/library/networks")
    def net_list():
        return {"networks": networks.list_networks(), "open": current_project()}

    @app.post("/api/library/networks")
    def net_create(body: dict):
        return networks.create(body.get("name", ""), body.get("description", ""), body.get("tags"))

    @app.post("/api/library/networks/import")
    def net_import(body: dict):
        kind, p = body.get("kind"), _abs(body.get("path"))
        if kind == "yaob":
            return networks.import_yaob_release(p, body.get("name") or "Yaob N10K-C4B")
        if kind == "store":
            return networks.import_store(p, body.get("name") or Path(p).stem)
        if kind == "bundle":
            return networks.info(dprojects.import_bundle_dir(p, body.get("name")))
        if kind == "auto":
            pp = Path(p)
            if pp.is_dir() and (pp / "network" / "yaob-n10k.sqlite").exists():
                return networks.import_yaob_release(p, body.get("name") or "Yaob N10K-C4B")
            if pp.is_dir():
                return networks.info(dprojects.import_bundle_dir(p, body.get("name")))
            return networks.import_store(p, body.get("name") or pp.stem)
        raise HTTPException(400, "kind must be yaob, store, bundle or auto")

    def _store_if_open(slug: str):
        return get_store() if current_project() == slug else None

    @app.get("/api/library/networks/{slug}")
    def net_get(slug: str):
        return networks.info(slug)

    @app.get("/api/library/networks/{slug}/stats")
    def net_stats(slug: str):
        st = _store_if_open(slug)
        if st is not None:
            st.checkpoint_wal()
        return networks.stats(slug)

    @app.patch("/api/library/networks/{slug}")
    def net_update(slug: str, body: dict):
        m = dprojects.read_manifest(slug)
        if "name" in body:
            dprojects.rename(slug, body["name"])
            m = dprojects.read_manifest(slug)
        for k in ("description", "tags"):
            if k in body:
                m[k] = body[k]
        dprojects.write_manifest(slug, m)
        return networks.info(slug)

    @app.post("/api/library/networks/{slug}/duplicate")
    def net_duplicate(slug: str, body: dict):
        name = (body.get("name") or "").strip() or f"{dprojects.read_manifest(slug).get('name')} copy"
        return start_task("network", f"Duplicate into {name}", lambda t: networks.duplicate(slug, name, _store_if_open(slug))).view()

    @app.post("/api/library/networks/{slug}/specialise")
    def net_specialise(slug: str, body: dict):
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Give the new network a name.")
        filt = body.get("filter") or {}
        ids = [int(x) for x in body["ids"]] if body.get("ids") else None
        st = _store_if_open(slug)
        if st is not None:
            st.checkpoint_wal()
        return start_task("network", f"Create {name}", lambda t: networks.specialise(slug, name, filt, ids, body.get("description", ""))).view()

    @app.post("/api/library/networks/{slug}/merge")
    def net_merge(slug: str, body: dict):
        src = body.get("source")
        if not src:
            raise HTTPException(400, "Choose the network to copy nodes from.")
        return start_task("network", f"Merge into {slug}", lambda t: networks.merge(slug, src, body.get("filter") or None, body.get("on_conflict", "skip"),
                                                                                  _store_if_open(slug))).view()

    @app.get("/api/library/networks/{a}/compare/{b}")
    def net_compare(a: str, b: str):
        for s in (a, b):
            st = _store_if_open(s)
            if st is not None:
                st.checkpoint_wal()
        return networks.compare(a, b)

    @app.get("/api/library/networks/{slug}/duplicates")
    def net_dups(slug: str):
        from ..store.db import Store
        st = _store_if_open(slug)
        own = st is None
        st = st or Store(dprojects.store_path(slug))
        try:
            return netops.duplicates(st)
        finally:
            if own:
                st.close()

    @app.post("/api/library/networks/{slug}/open")
    def net_open(slug: str):
        open_project(slug)
        return {"open": slug}

    @app.delete("/api/library/networks/{slug}")
    def net_delete(slug: str):
        users = [m["name"] for m in models.list_models() if (m.get("network") or {}).get("id") == slug]
        if users:
            raise HTTPException(409, f"Used by {', '.join(users)}. Change those models first.")
        if current_project() == slug:
            raise HTTPException(409, "Close this network before deleting it.")
        return {"moved_to": str(dprojects.trash(slug))}

    # -------------------------------------------------------------------------------- bulk node operations (open network)
    def need_store():
        st = get_store()
        if st is None:
            raise HTTPException(409, "Open a network first.")
        return st

    def _ids_or_filter(body: dict) -> list[int]:
        st = need_store()
        if body.get("ids") is not None:
            return [int(x) for x in body["ids"]]
        return netops.select_ids(st, body.get("filter") or {})

    @app.post("/api/nodes/select")
    def nodes_select(body: dict):
        ids = netops.select_ids(need_store(), body.get("filter") or {})
        return {"count": len(ids), "ids": [sid(i) for i in ids]}

    @app.post("/api/nodes/bulk-preview")
    def nodes_bulk_preview(body: dict):
        st = need_store()
        ids = _ids_or_filter(body)
        n = len(ids)
        if not n:
            return {"count": 0}
        st.conn().execute("CREATE TEMP TABLE IF NOT EXISTS bulk_sel(node_id INTEGER PRIMARY KEY)")
        c = st.conn()
        c.execute("DELETE FROM bulk_sel")
        c.executemany("INSERT OR IGNORE INTO bulk_sel VALUES(?)", [(i,) for i in ids])
        live = c.execute("SELECT COUNT(*) FROM node_v WHERE rev_to IS NULL AND status<>'tombstone' AND node_id IN (SELECT node_id FROM bulk_sel)").fetchone()[0]
        edges = c.execute("SELECT COUNT(*) FROM edge_v WHERE rev_to IS NULL AND (src IN (SELECT node_id FROM bulk_sel) OR dst IN (SELECT node_id FROM bulk_sel))").fetchone()[0]
        by_mpn = [{"mpn": r[0], "nodes": r[1]} for r in c.execute(
            "SELECT p.name, COUNT(*) FROM member_v m JOIN mpn_v p ON p.mpn_id=m.mpn_id AND p.rev_to IS NULL WHERE m.rev_to IS NULL AND m.role='primary' "
            "AND m.node_id IN (SELECT node_id FROM bulk_sel) GROUP BY p.name ORDER BY 2 DESC LIMIT 12")]
        sample = [r[0] for r in c.execute("SELECT title FROM node_v WHERE rev_to IS NULL AND node_id IN (SELECT node_id FROM bulk_sel) ORDER BY title LIMIT 6")]
        c.execute("DELETE FROM bulk_sel")
        return {"count": n, "live": live, "relations": edges, "by_mpn": by_mpn, "sample": sample,
                "note": "Deletion is one revision: Undo restores every node and relation. Deleted nodes leave search and retrieval at once."}

    @app.post("/api/nodes/bulk-delete")
    def nodes_bulk_delete(body: dict):
        st = need_store()
        ids = _ids_or_filter(body)
        if not ids:
            raise HTTPException(400, "Nothing selected.")

        def run(t: Task):
            t.progress = {"label": "deleting", "done": 0, "total": len(ids)}
            r = netops.bulk_delete(st, ids, progress=lambda d, n: t.progress.update(done=d, total=n), cancelled=t.cancel.is_set)
            t.progress = {"label": "done", "done": len(ids), "total": len(ids)}
            return r
        return start_task("bulk-delete", f"Delete {len(ids)} nodes", run).view()

    @app.post("/api/nodes/bulk-restore")
    def nodes_bulk_restore(body: dict):
        st = need_store()
        rev = int(body.get("rev") or 0)
        if not rev:
            raise HTTPException(400, "Give the revision of the deletion.")
        new = st.revert(rev, kind="undo")
        return {"rev": new, "head": st.head()}

    # -------------------------------------------------------------------------------- models
    @app.get("/api/library/models")
    def model_list():
        return {"models": models.list_models()}

    @app.post("/api/library/models")
    def model_create(body: dict):
        return models.create(body.get("name", ""), body.get("description", ""), body.get("core_id"), body.get("network_id"), body.get("encoder_id"))

    @app.get("/api/library/models/{mid}")
    def model_get(mid: str):
        m = models.get(mid)
        r = runner.RUNS.get(mid)
        m["run"] = {"health": r.health(), "port": r.port} if r else None
        try:
            m["build_report"] = json.loads((models.model_dir(mid) / "build" / "build.json").read_text())
        except (OSError, ValueError):
            m["build_report"] = None
        return m

    @app.patch("/api/library/models/{mid}")
    def model_update(mid: str, body: dict):
        return models.update(mid, body)

    @app.delete("/api/library/models/{mid}")
    def model_delete(mid: str):
        runner.RUNS.stop(mid)
        return models.remove(mid)

    @app.post("/api/library/models/{mid}/build")
    def model_build(mid: str, body: dict | None = None):
        runner.RUNS.stop(mid)
        dev = (body or {}).get("device", "auto")
        return start_task("build", f"Build {models.get(mid)['name']}",
                          lambda t: models.build(mid, log=lambda s: t.log.append(s), device=dev, cancelled=t.cancel.is_set)).view()

    @app.post("/api/library/models/{mid}/export")
    def model_export(mid: str, body: dict):
        dest = _abs(body.get("path"), "destination")
        mode = body.get("mode", "complete")
        return start_task("export", f"Export {models.get(mid)['name']}", lambda t: runner.export(mid, dest, mode, prog(t))).view()

    # -------------------------------------------------------------------------------- running models (built or opened .mpn)
    OPENED: dict[str, dict] = {}

    def _opened_list():
        return [dict(v, run=(lambda r: {"health": r.health()} if r else None)(runner.RUNS.get(k))) for k, v in OPENED.items()]

    @app.post("/api/library/mpn/inspect")
    def mpn_inspect(body: dict):
        return mpnfile.MpnFile(_abs(body.get("path"), ".mpn file")).summary()

    @app.post("/api/library/mpn/verify")
    def mpn_verify(body: dict):
        p = _abs(body.get("path"), ".mpn file")
        return start_task("verify", f"Verify {Path(p).name}", lambda t: mpnfile.MpnFile(p).verify(prog(t))).view()

    @app.post("/api/library/mpn/open")
    def mpn_open(body: dict):
        p = _abs(body.get("path"), ".mpn file")
        f = mpnfile.MpnFile(p)
        key = "mpn-" + f.id

        def run(t: Task):
            root = runner.prepare_mpn(p, prog(t))
            OPENED[key] = {"key": key, "file": p, "name": f.manifest.get("name"), "summary": f.summary(), "root": str(root)}
            return OPENED[key]
        return start_task("open-mpn", f"Open {Path(p).name}", run).view()

    def _root_for(key: str) -> tuple[Path, int | None]:
        if key in OPENED:
            root = Path(OPENED[key]["root"])
            return root, runner.context_of(root)
        m = models.get(key)
        return models.build_root(key), (m.get("context") or {}).get("tokens")

    @app.post("/api/library/run/{key}/start")
    def run_start(key: str, body: dict | None = None):
        root, ctxn = _root_for(key)
        r = runner.RUNS.start(key, root, ctxn, (body or {}).get("device", "auto"))
        return {"key": key, "health": r.health(), "port": r.port}

    @app.post("/api/library/run/{key}/stop")
    def run_stop(key: str):
        return {"stopped": runner.RUNS.stop(key)}

    @app.get("/api/library/run/{key}")
    async def run_status(key: str):
        r = runner.RUNS.get(key)
        if not r:
            return {"key": key, "health": "stopped"}
        out = {"key": key, "health": r.health(), "port": r.port, "log": r.tail(6), "memory": _tree_memory(r.proc.pid)}
        if out["health"] == "ok":
            try:
                s = await client.get(r.url + "/status", headers={"Authorization": f"Bearer {r.api_key}"}, timeout=5)
                out["status"] = s.json()
            except Exception:
                pass
        return out

    @app.post("/api/library/run/{key}/chat")
    async def run_chat(key: str, body: dict):
        """Stream one answer with its evidence (route, tools, validation, timing) from the running model."""
        r = runner.RUNS.get(key)
        if not r:
            raise HTTPException(409, "Start the model first.")
        payload = {"messages": body.get("messages") or [], "mode": body.get("mode", "yaob"), "think": bool(body.get("think", False))}

        async def gen():
            try:
                async with client.stream("POST", r.url + "/run", json=payload, headers={"Authorization": f"Bearer {r.api_key}"}) as resp:
                    async for chunk in resp.aiter_raw():
                        yield chunk
            except httpx.HTTPError as e:
                yield f"event: error\ndata: {json.dumps({'error': f'The model stopped answering: {e}'})}\n\n".encode()
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/library/run/{key}/connection")
    def run_connection(key: str):
        """How an external OpenAI-compatible client reaches the running model."""
        r = runner.RUNS.get(key)
        if not r:
            raise HTTPException(409, "Start the model first.")
        st = json.loads((r.root / "MANIFEST.json").read_text())
        return {"base_url": r.url + "/v1", "model": st.get("model_id"), "api_key": r.api_key,
                "note": "Loopback only, for the lifetime of this run. For a permanent service use `mpn run FILE.mpn --host ... --api-key ...`."}

    # -------------------------------------------------------------------------------- benchmarks and comparisons
    @app.post("/api/library/bench")
    def bench(body: dict):
        """Run a question set against one or more models. Each question: {question, expect: [accepted answers]}.
        A reply is correct when it contains one of the accepted answers (case-insensitive)."""
        keys = body.get("models") or []
        qs = body.get("questions") or []
        if not keys or not qs:
            raise HTTPException(400, "Choose at least one model and one question.")
        for key in keys:
            if key not in OPENED and models.get(key)["status"]["state"] != "ready":
                raise HTTPException(409, f"{models.get(key)['name']} is not built. Build it before testing it.")

        def run(t: Task):
            out = {"models": {}, "questions": len(qs), "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            total = len(keys) * len(qs)
            done = 0
            for key in keys:
                root, ctxn = _root_for(key)
                was_running = runner.RUNS.get(key) is not None
                rr = runner.RUNS.start(key, root, ctxn)
                runner.wait_ready(rr)
                rows = []
                for q in qs:
                    if t.cancel.is_set():
                        raise netops.Cancelled()
                    res = _ask(rr, q["question"])
                    exp = [e.lower() for e in (q.get("expect") or [])]
                    res["correct"] = bool(exp) and any(e in res["answer"].lower() for e in exp)
                    res.update(question=q["question"], expect=q.get("expect"))
                    rows.append(res)
                    done += 1
                    t.progress = {"label": f"{key}: {done}/{total}", "done": done, "total": total}
                if not was_running:
                    runner.RUNS.stop(key)
                scored = [x for x in rows if x["expect"]]
                ok = sum(x["correct"] for x in scored)
                out["models"][key] = {"correct": ok, "scored": len(scored), "accuracy": round(ok / len(scored), 4) if scored else None,
                                      "median_total_ms": sorted(x["total_ms"] for x in rows)[len(rows) // 2],
                                      "median_route_ms": sorted((x["route_ms"] or 0) for x in rows)[len(rows) // 2], "rows": rows}
            path = dpaths.data_dir() / "bench" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
            path.with_suffix(".csv").write_text(_bench_csv(out))
            out["saved"] = str(path)
            out["saved_csv"] = str(path.with_suffix(".csv"))
            return out
        return start_task("bench", f"Test {len(keys)} model(s) on {len(qs)} questions", run).view()

    @app.get("/api/library/bench/{tid}.csv")
    def bench_csv(tid: str):
        t = TASKS.get(tid)
        if not t or not t.result:
            raise HTTPException(404, "No finished test run with that id.")
        return PlainTextResponse(_bench_csv(t.result), media_type="text/csv")

    # -------------------------------------------------------------------------------- MPN network (read-only explorer)
    from ..desktop import settings as dsettings
    from ..mpnnet.service import NetError, Service
    net = Service()

    @app.exception_handler(NetError)
    async def _net_error(_: Request, e: NetError):
        return JSONResponse({"error": str(e)}, status_code=e.status)

    def _recent(mode: str, target: str, name: str):
        s = dsettings.load()
        rec = [r for r in s["networks"]["recent"] if not (r.get("mode") == mode and r.get("target") == target)]
        rec.insert(0, {"mode": mode, "target": target, "name": name, "opened": time.time()})
        dsettings.update({"networks": {"recent": rec[:8]}})

    @app.get("/api/library/network")
    def network_state():
        return {**net.state(), "recent": dsettings.load()["networks"]["recent"]}

    @app.post("/api/library/network/open")
    def network_open(body: dict):
        mode, target = body.get("mode"), (body.get("target") or "").strip()
        if not target:
            raise HTTPException(400, "Give an address or choose a folder.")
        if mode == "local":
            _abs(target, "folder")
        st = net.open(mode, target)
        _recent(mode, st["target"], st["name"])
        return {**st, "recent": dsettings.load()["networks"]["recent"]}

    @app.post("/api/library/network/close")
    def network_close():
        net.close()
        return net.state()

    @app.post("/api/library/network/forget")
    def network_forget(body: dict):
        s = dsettings.load()
        rec = [r for r in s["networks"]["recent"] if not (r.get("mode") == body.get("mode") and r.get("target") == body.get("target"))]
        dsettings.update({"networks": {"recent": rec}})
        return {"recent": rec}

    @app.get("/api/library/network/v1/{op}")
    def network_query(op: str, request: Request):
        if op not in ("overview", "search", "browse"):
            raise HTTPException(404, "Unknown query.")
        return net.call(op, None, dict(request.query_params))

    @app.get("/api/library/network/v1/node/{nid}")
    def network_node(nid: str):
        return net.call("node", nid)

    @app.get("/api/library/network/v1/node/{nid}/{what}")
    def network_node_more(nid: str, what: str, request: Request):
        if what not in ("relationships", "graph"):
            raise HTTPException(404, "Unknown query.")
        return net.call(what, nid, dict(request.query_params))

    @app.post("/api/library/network/install")
    def network_install(body: dict):
        """Install a downloaded (compressed) pack into a folder so it opens offline. Never writes into the source folder."""
        src, dst = Path(_abs(body.get("source"), "downloaded pack folder")), Path(_abs(body.get("dest"), "destination folder"))
        if not (src / "manifest.json").exists():
            raise HTTPException(400, "The downloaded pack folder needs manifest.json.")
        if dst.resolve() == src.resolve() or src.resolve() in dst.resolve().parents:
            raise HTTPException(400, "Install into a different folder from the download, not inside it.")
        from ..mpnnet import install as inst

        def run(t: Task):
            m = json.loads((src / "manifest.json").read_text())
            total = len(m["shards"])
            done = [0]

            def progress(msg):
                done[0] += 1
                t.log.append(msg)
                t.progress = {"label": msg, "done": done[0], "total": total}
                if t.cancel.is_set():
                    raise netops.Cancelled()
            try:
                return inst.install(src, dst, overlay=False, progress=progress)
            except SystemExit as e:
                raise RuntimeError(str(e))
        return start_task("network-install", f"Install {src.name}", run).view()


def _tree_memory(pid: int) -> dict:
    """Memory of a running model: its runtime process and every child (the llama.cpp servers), measured now.
    GPU memory comes from nvidia-smi's per-process accounting where the driver reports it."""
    import shutil
    import subprocess
    import psutil
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except psutil.Error:
        return {}
    pids, rss = set(), 0
    for p in procs:
        try:
            rss += p.memory_info().rss
            pids.add(p.pid)
        except psutil.Error:
            pass
    out = {"ram_bytes": rss, "processes": len(pids)}
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            used = [int(b) for a, b in (l.split(",") for l in r.stdout.strip().splitlines() if "," in l) if int(a) in pids and b.strip().isdigit()]
            if used:
                out["gpu_bytes"] = sum(used) * 1024 * 1024
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return out


def _bench_csv(result: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["model", "question", "expected", "answer", "correct", "cited", "nodes", "route_ms", "total_ms", "error"])
    for key, m in result["models"].items():
        for x in m["rows"]:
            w.writerow([key, x["question"], " | ".join(x["expect"] or []), x["answer"], x["correct"], " | ".join(x["cited"]), " | ".join(x["nodes"]),
                        x["route_ms"], x["total_ms"], x["error"] or ""])
    return buf.getvalue()


def _ask(r: runner.Run, question: str) -> dict:
    """One question to a running model through its /run stream; returns the answer and its evidence."""
    import urllib.request
    req = urllib.request.Request(r.url + "/run", data=json.dumps({"messages": [{"role": "user", "content": question}], "mode": "yaob"}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {r.api_key}"})
    t0 = time.perf_counter()
    out = {"answer": "", "nodes": [], "cited": [], "route_ms": None, "error": None}
    ev = None
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = json.loads(line[5:].strip() or "{}")
                if ev == "token":
                    out["answer"] += d.get("text", "")
                elif ev == "route":
                    out["nodes"] = [n["title"] for n in d.get("nodes", []) if "text" in (n.get("read_as") or [])]
                    out["route_ms"] = d.get("route_ms") or d.get("ms")
                elif ev == "validation":
                    out["cited"] = [c["title"] for c in d.get("citations", []) if c.get("cited")]
                elif ev == "error":
                    out["error"] = d.get("error")
    out["total_ms"] = round((time.perf_counter() - t0) * 1000)
    return out
