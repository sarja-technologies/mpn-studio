#!/usr/bin/env python3
"""Start the local runtime from Settings > Compute for real, in a throw-away workspace, and report what happens.

    .venv-build/bin/python scripts/runtime-smoke.py --python /path/to/env/bin/python [--models /path/to/models]

It drives the same desktop API the Settings window uses: create a project, point Compute at the given Python,
start the runtime, read its status through the app, stop it, and check that nothing is left running.
Without --models it makes an EMPTY models folder, so the runtime starts but cannot load a model; the report says so.
Nothing under your real ~/.local/share/mpn-studio is touched (XDG dirs point into a temp folder).
"""
import argparse
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--python", required=True)
ap.add_argument("--models")
ap.add_argument("--cpu-worker", action="store_true",
                help="treat training as available even without CUDA, to check the worker and queue with the built-in 'sleep' job (CPU-only machines)")
ap.add_argument("--wait", type=float, default=20.0, help="seconds to let the runtime try to load its model")
a = ap.parse_args()

tmp = Path(tempfile.mkdtemp(prefix="mpn-runtime-smoke-"))
for k, d in (("CONFIG", "c"), ("DATA", "d"), ("CACHE", "x"), ("STATE", "s")):
    os.environ[f"MPN_STUDIO_{k}_DIR"] = str(tmp / d)
os.environ["MPN_DESKTOP"] = "1"
os.environ["MPN_SESSION_TOKEN"] = "smoke"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient      # noqa: E402
from mpnstudio.api.app import create_app       # noqa: E402
from mpnstudio.desktop import compute          # noqa: E402

if a.cpu_worker:
    _real = compute.capabilities

    def _caps(models_dir=None):
        out = _real(models_dir)
        for f in out["features"]:
            if f["id"] == "local_training":
                f.update(available=True, reason=None)
        return out
    compute.capabilities = _caps

models = Path(a.models) if a.models else tmp / "models"
if not a.models:
    for n in (compute.CORE_DIR, compute.ENCODER_DIR):
        (models / n).mkdir(parents=True)
H = {"x-mpn-token": "smoke"}
ok = True


def check(label, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail else ""))


with TestClient(create_app(), headers=H) as c:
    pid = c.post("/api/desktop/projects", json={"name": "Runtime smoke"}).json()["id"]
    c.post(f"/api/desktop/projects/{pid}/open")
    c.put("/api/desktop/settings", json={"compute": {"python": a.python}, "storage": {"models_dir": str(models)}})
    caps = c.get("/api/desktop/compute").json()
    print("python probe:", caps["python"])
    feats = {f["id"]: f for f in caps["features"]}
    check("python probe finds the model stack", not caps["python"].get("missing") and not caps["python"].get("error"), str(caps["python"].get("missing") or caps["python"].get("error") or ""))
    check("local inference offered", feats["local_inference"]["available"], feats["local_inference"]["reason"] or "")
    print("training:", "offered" if feats["local_training"]["available"] else f"not offered ({feats['local_training']['reason']})")
    r = c.post("/api/desktop/runtime/start")
    check("start answers 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        st = r.json()
        check("runtime process is up", st["runtime"])
        port = compute.local.port
        t0 = time.time()
        eng = {}
        while time.time() - t0 < a.wait:
            eng = c.get("/api/desktop/runtime").json().get("engine") or {}
            if (eng.get("core") or {}).get("loaded") or eng.get("startup_error"):
                break
            time.sleep(1)
        print("engine status:", {k: eng.get(k) for k in ("core", "loading", "startup_error") if k in eng} or eng)
        if not a.models:
            check("an empty models folder is reported, not hidden", bool(eng.get("startup_error")), str(eng.get("startup_error"))[:140])
        via = c.get("/api/runtime").json()
        check("the app reaches it (api/runtime)", via.get("available") is not False or "reason" not in via, str(via)[:160])
        job = c.post("/api/jobs", json={"kind": "sleep", "params": {"steps": 3, "step_s": 0.3, "gpu": False, "memory_estimate_gib": 0.1}})
        if a.cpu_worker:
            check("worker process is up", st["worker"])
            check("job accepted while a worker runs", job.status_code == 200, job.text[:160])
            jid, state = job.json().get("id"), None
            for _ in range(60):
                state = c.get(f"/api/jobs/{jid}").json()["state"]
                if state in ("succeeded", "failed", "cancelled", "interrupted"):
                    break
                time.sleep(0.5)
            check("the worker ran the job to the end", state == "succeeded", str(state))
        else:
            check("jobs are refused with the reason while no worker runs", job.status_code == 409, job.text[:120])
        pids = [p.pid for p in (compute.local.rt, compute.local.worker) if p]
        c.post("/api/desktop/runtime/stop")
        time.sleep(0.5)
        check("port closed after stop", not any(socket.socket().connect_ex(("127.0.0.1", port)) == 0 for _ in [0]))
        gone = True
        for p in pids:
            try:
                os.kill(p, 0)
                gone = False
            except ProcessLookupError:
                pass
        check("no leftover processes", gone)
    print("log tail:", (compute._tail(compute.local._logs() / "runtime.log", 6) or "(empty)")[:600])
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
