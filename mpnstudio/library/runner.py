"""Turn a built model or a .mpn file into a running MPN runtime.

prepare_mpn(path)   checks the file, finds or extracts each member once, and writes a runtime root:
                      <cache>/run/<file id>/MANIFEST.json, network.sqlite (a private working copy), router/, data/
                    GGUF members whose hash is already in the core library are used from there (no copy). Others are
                    extracted to <cache>/blobs/<sha256>.gguf once, verified while extracting, and shared by every model
                    that contains the same file. Nothing is loaded into memory to do this; llama.cpp memory-maps the
                    GGUF files and the network is read from SQLite page by page.
export(mid, dest)   writes a built model as a .mpn file (complete or lightweight)
Runs                one runtime process per running model (`mpn serve-root`), on a loopback port with a random key
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

from . import cores, models, mpnfile
from ..desktop import paths

Progress = Callable[[str, int, int], None]


class RunError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


# ------------------------------------------------------------------------------------------------ export
def _notices(man: dict, core: dict, enc: dict | None, net_stats: dict) -> str:
    def lic(c):
        i = c["info"]
        return f"{c['name']} ({i['architecture']}, {i['parameters']:,} parameters, {i['quantization']}): licence {i.get('license') or 'not stated in the file'}" + \
               (f", {i['license_link']}" if i.get("license_link") else "") + ". Not trained by the maker of this model; used unchanged."
    lines = [f"{man['name']} (.mpn model, format {mpnfile.FORMAT})", "",
             "This file combines independently licensed parts. Each keeps its own licence.", "",
             "Cognitive core: " + lic(core)]
    if enc:
        lines.append("Retrieval encoder: " + lic(enc))
    lic_counts = net_stats.get("source_licences") or {}
    if lic_counts:
        lines.append("Node network sources: " + "; ".join(f"{k or 'unstated'}: {v} sources" for k, v in lic_counts.items()))
    lines += ["", "Built with MPN Studio (Apache-2.0). The MPN runtime that reads this file is part of MPN Studio.",
              "Removing a node from the network removes it from retrieval; it does not change the core's weights."]
    if (man.get("licence") or {}).get("model"):
        lines += ["", f"Licence chosen by the model's author for their own contributions: {man['licence']['model']}"]
    return "\n".join(lines) + "\n"


def _source_licences(net_path: Path) -> dict:
    import sqlite3
    c = sqlite3.connect(f"file:{net_path}?mode=ro", uri=True)
    try:
        return {r[0]: r[1] for r in c.execute("SELECT license, COUNT(*) FROM sources GROUP BY license ORDER BY 2 DESC")}
    finally:
        c.close()


def export(mid: str, dest: str | Path, mode: str = "complete", progress: Progress | None = None) -> dict:
    if mode not in ("complete", "lightweight"):
        raise RunError("mode must be complete or lightweight")
    m = models.get(mid)
    if m["status"]["state"] != "ready":
        raise RunError("Build the model (and let its test pass) before exporting. " + " ".join(m["status"]["problems"]), 409)
    root = models.build_root(mid)
    rm = json.loads((root / "MANIFEST.json").read_text())
    build = json.loads((root / "build.json").read_text())
    core = cores.get(m["core"]["id"])
    enc = cores.get(m["encoder"]["id"]) if m.get("encoder") else None
    net = root / "network.sqlite"
    light = mode == "lightweight"
    members = []
    if not light:
        members.append({"name": "core/model.gguf", "kind": "core", "path": core["path"]})
        if (core.get("projector") or {}).get("path"):
            members.append({"name": "core/projector.gguf", "kind": "projector", "path": core["projector"]["path"]})
        if enc:
            members.append({"name": "encoder/model.gguf", "kind": "encoder", "path": enc["path"]})
    members.append({"name": "network/network.sqlite", "kind": "network", "path": str(net)})
    router_files = []
    ck = rm["paths"].get("checkpoint")
    if ck and ck != "none" and Path(ck).exists():
        for p in sorted(Path(ck).rglob("*")):
            if p.is_file():
                n = "router/" + p.relative_to(ck).as_posix()
                router_files.append(n)
                members.append({"name": n, "kind": "router", "path": str(p)})

    def comp(c):
        if not c:
            return None
        i = c["info"]
        return {"name": c["name"], "sha256": c["sha256"], "bytes": i["bytes"], "architecture": i["architecture"], "parameters": i["parameters"],
                "quantization": i["quantization"], "context_length": i["context_length"], "license": i.get("license"), "license_link": i.get("license_link"),
                "base_models": i.get("base_models")}
    manifest = {
        "name": m["name"], "model_id": m.get("model_id") or mid, "description": m.get("description", ""), "version": m.get("version"),
        "identity": rm["identity"], "created": models.now(),
        "core": {**comp(core), "included": not light, "member": None if light else "core/model.gguf",
                 "projector": ({"sha256": core["projector"]["sha256"], "bytes": core["projector"]["bytes"], "included": not light,
                                "member": None if light else "core/projector.gguf"} if core.get("projector") else None)},
        "encoder": ({**comp(enc), "included": not light, "member": None if light else "encoder/model.gguf"} if enc else None),
        "network": {"member": "network/network.sqlite", **build["network"]},
        "router": {"members": router_files, "id": Path(ck).name} if router_files else None,
        "retrieval": m.get("retrieval"), "context": m.get("context"),
        "requirements": {"runtime": "MPN runtime (MPN Studio) reading mpn-model/1", "llama_cpp_architecture": core["info"]["architecture"],
                         "llama_cpp": cores.ARCHS["llama_cpp"]},
        "licence": m.get("licence"),
        "provenance": {"built_with": "MPN Studio", "build": {k: build.get(k) for k in ("finished", "seconds", "routing", "device")},
                       "test": build.get("test")},
    }
    manifest["notices_member"] = "NOTICE.txt"
    members.append({"name": "model.json", "kind": "config", "data": json.dumps(manifest, indent=1, ensure_ascii=False).encode()})
    members.append({"name": "NOTICE.txt", "kind": "notice", "data": _notices(manifest, core, enc, {"source_licences": _source_licences(net)}).encode()})
    dest = Path(dest).expanduser()
    if dest.is_dir():
        dest = dest / (re_name(m["name"]) + ("-lightweight" if light else "") + ".mpn")
    t0 = time.perf_counter()
    idx = mpnfile.write(dest, manifest, members, lightweight=light, progress=progress)
    f = mpnfile.MpnFile(dest)
    return {"file": str(dest), "bytes": dest.stat().st_size, "mode": mode, "id": f.id, "members": len(idx["members"]),
            "seconds": round(time.perf_counter() - t0, 1)}


def re_name(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "model"


# ------------------------------------------------------------------------------------------------ load a .mpn file
def _blob(f: mpnfile.MpnFile, member: str | None, sha: str, what: str, progress: Progress | None, core_override: str | None = None) -> str:
    lib = cores.find_by_hash(sha)
    if lib and lib["present"]:
        return lib["path"]
    if core_override:
        p = Path(core_override).expanduser()
        if not p.exists():
            raise RunError(f"No file at {p}.")
        got = cores.sha256_file(p)
        if got != sha:
            raise RunError(f"{p.name} is not the {what} this model needs (SHA-256 {got[:16]}…, needed {sha[:16]}…).")
        return str(p)
    if member:
        d = paths.cache_dir() / "blobs"
        dst = d / f"{sha}.gguf"
        if not dst.exists():
            f.extract(member, dst, progress)
        return str(dst)
    raise RunError(f"This is a lightweight model: it needs the {what} with SHA-256 {sha[:16]}… installed. Import that GGUF file as a core "
                   f"in MPN Studio (or `mpn core import FILE`), or pass it with --core.", 424)


def prepare_mpn(path: str | Path, progress: Progress | None = None, core_file: str | None = None, encoder_file: str | None = None) -> Path:
    f = mpnfile.MpnFile(path)
    man = f.manifest
    root = paths.cache_dir() / "run" / f.id
    ready = root / ".ready"
    if ready.exists() and (root / "MANIFEST.json").exists() and not core_file and not encoder_file:   # a file passed by hand is always checked
        rm = json.loads((root / "MANIFEST.json").read_text())
        if all(Path(rm["paths"][k]).exists() for k in ("core", "network") if rm["paths"].get(k) not in (None, "none")):
            return root
    root.mkdir(parents=True, exist_ok=True)
    core = man["core"]
    core_path = _blob(f, core.get("member"), core["sha256"], f"cognitive core {core['name']}", progress, core_file)
    proj = core.get("projector")
    proj_path = "none"
    if proj:
        try:
            proj_path = _blob(f, proj.get("member"), proj["sha256"], "vision projector", progress)
        except RunError:
            proj_path = "none"           # optional: without it the model reads text only
    enc = man.get("encoder")
    enc_path = _blob(f, enc.get("member"), enc["sha256"], f"retrieval encoder {enc['name']}", progress, encoder_file) if enc else "none"
    net = root / "network.sqlite"
    if not net.exists():
        f.extract(man["network"]["member"], net, progress)
    ck = "none"
    if man.get("router"):
        rd = root / "router"
        for n in man["router"]["members"]:
            t = rd / n[len("router/"):]
            if not t.exists():
                f.extract(n, t, progress)
        ck = str(rd)
    manifest = {"name": man["name"], "model_id": man["model_id"], "identity": man.get("identity"), "owned_by": "local", "version": man.get("version"),
                "network_writable": True, "source_file": str(Path(path).resolve()), "core": {k: core.get(k) for k in ("name", "sha256", "quantization", "parameters", "architecture")},
                "encoder": {"name": enc["name"], "sha256": enc["sha256"]} if enc else None,
                "paths": {"core": core_path, "mmproj": proj_path, "encoder": enc_path, "network": str(net), "checkpoint": ck}}
    (root / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    r, c = man.get("retrieval") or {}, man.get("context") or {}
    budget = {k: int(r[k]) for k in ("text_nodes", "retrieval_candidates", "max_active_mpns", "tool_rounds", "selected_nodes") if k in r}
    if c.get("parallel"):
        budget["concurrent_requests"] = int(c["parallel"])
    if c.get("output_tokens"):
        budget["output_tokens"] = int(c["output_tokens"])
    if not (root / "data" / "budget.json").exists():
        (root / "data" / "budget.json").write_text(json.dumps(budget))
    ready.write_text(f.id)
    return root


def context_of(root: Path) -> int | None:
    src = json.loads((root / "MANIFEST.json").read_text()).get("source_file")
    if src and Path(src).exists():
        return int(((mpnfile.MpnFile(src).manifest.get("context") or {}).get("tokens") or 0)) or None
    return None


# ------------------------------------------------------------------------------------------------ runtime processes
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def runtime_cmd() -> list[str]:
    """How to start the MPN runtime: the frozen backend's own subcommand, or this Python with the package."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "serve-root"]
    return [sys.executable, "-m", "mpnstudio.cli", "serve-root"]


class Run:
    def __init__(self, key: str, root: Path, context: int | None, device: str = "auto"):
        self.key, self.root = key, root
        self.port = _free_port()
        self.api_key = secrets.token_urlsafe(24)
        log = root / "data" / "logs" / "server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        self.log_path = log
        cmd = runtime_cmd() + ["--root", str(root), "--data", str(root / "data"), "--port", str(self.port), "--device", device]
        if context:
            cmd += ["--context", str(context)]
        env = dict(os.environ, MPN_API_KEY=self.api_key)
        self.proc = subprocess.Popen(cmd, stdout=log.open("ab"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        self.started = time.time()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.proc.poll() is None

    def health(self) -> str:
        if not self.alive():
            return "stopped"
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=3) as r:
                return json.loads(r.read()).get("status", "?")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read()).get("status", "error")
            except Exception:
                return "error"
        except Exception:
            return "starting"

    def stop(self, grace: float = 20.0) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def tail(self, n: int = 12) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""


class Runs:
    def __init__(self):
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()

    def start(self, key: str, root: Path, context: int | None, device: str = "auto") -> Run:
        with self.lock:
            r = self.runs.get(key)
            if r and r.alive():
                return r
            r = Run(key, root, context, device)
            self.runs[key] = r
            return r

    def get(self, key: str) -> Run | None:
        r = self.runs.get(key)
        return r if r and r.alive() else None

    def stop(self, key: str) -> bool:
        with self.lock:
            r = self.runs.pop(key, None)
        if r:
            r.stop()
            return True
        return False

    def stop_all(self) -> None:
        for k in list(self.runs):
            self.stop(k)

    def status(self) -> list[dict]:
        return [{"key": k, "health": r.health(), "port": r.port, "started": r.started, "root": str(r.root)} for k, r in self.runs.items()]


RUNS = Runs()


def wait_ready(run: Run, timeout: float = 900) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = run.health()
        if h == "ok":
            return
        if h == "stopped" or (h == "unavailable" and time.time() - t0 > 5):
            raise RunError("The model did not start:\n" + run.tail(), 500)
        time.sleep(0.5)
    raise RunError("The model did not become ready in time.\n" + run.tail(), 504)
