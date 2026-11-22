"""FG-MPN networks on this computer, read-only.

An FG-MPN store is a folder: `CURRENT` (the name of the live manifest), `manifests/gen-*.json` and `segments/*`. Studio
checks and describes one without any FG-MPN code (sizes and SHA-256 come from the store's own manifest). To explore it,
Studio starts its own reader (`mpnstudio.mpnnet.fgmpn_net`, on the public FG-MPN runtime) on a loopback port and opens it like
a network on another computer, so search, node inspector and canvas are the ones every network uses. The runtime is the
`fgmpn` package (https://github.com/sarja-technologies/fgmpn, Fylume Source-Available Terms 1.0):

  * the packaged app carries it (the wheel for the machine: native reader on Linux x86-64 and ARM64, Python engine otherwise);
  * from source or a pip install, any `fgmpn` 0.2.x in the Python that runs Studio.

The engine is chosen by FGMPN_ENGINE=auto|native|python; with auto, the native reader is used when it imports. Without the
package Studio still installs, verifies and lists FG-MPN networks; opening one says what is missing.
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ..catalogue.manifest import FGMPN_MANIFESTS, FGMPN_VERSIONS

Progress = Callable[[str, int, int], None]


class FgError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _interpreter() -> tuple[list[str], dict, str | None]:
    """(command prefix, extra environment, reason it cannot be used). The packaged app runs its own executable in the
    `fgmpn-serve` role (the runtime is inside the bundle); from source it is this Python."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "fgmpn-serve"], {}, None
    return [sys.executable, "-m", "mpnstudio.mpnnet.fgmpn_serve"], {}, None


def runtime_info() -> dict:
    """Is the fgmpn package usable, which version and engine."""
    base = {"available": False, "version": None, "engine": None, "supported": list(FGMPN_VERSIONS), "bundled": bool(getattr(sys, "frozen", False))}
    if importlib.util.find_spec("fgmpn") is None:
        return {**base, "reason": "The FG-MPN runtime (the fgmpn package) is not installed. Studio can install, verify and list FG-MPN networks, but cannot open one "
                                  "until it is. Install fgmpn 0.2.x from https://github.com/sarja-technologies/fgmpn/releases (pure-Python wheel on any platform; "
                                  "native readers for Linux x86-64 and ARM64)."}
    try:
        import fgmpn
        ver = getattr(fgmpn, "__version__", None)
    except Exception as e:                                  # a broken install must not break the catalogue screen
        return {**base, "reason": f"The fgmpn package could not be loaded: {e}"}
    native = importlib.util.find_spec("fgmpn._native") is not None
    return _verdict(base, ver, "native (Python fallback)" if native else "python")


def _verdict(base: dict, ver: str | None, eng: str | None) -> dict:
    fam = ".".join((ver or "").split(".")[:2])
    ok = fam in FGMPN_VERSIONS
    return {**base, "available": ok, "version": ver, "engine": eng,
            "reason": None if ok else f"The installed fgmpn is {ver}; this Studio reads FG-MPN {', '.join(v + '.x' for v in FGMPN_VERSIONS)}."}


def is_store(root: str | Path) -> bool:
    r = Path(root)
    return (r / "CURRENT").is_file() and (r / "manifests").is_dir()


def inspect_store(root: str | Path) -> dict:
    """What a store says about itself, from its manifest alone. Raises FgError with a reason when it cannot be used."""
    r = Path(root)
    if not is_store(r):
        raise FgError(f"{r} is not an FG-MPN store (CURRENT and manifests/ are missing).")
    try:
        cur = (r / "CURRENT").read_text().strip()
        m = json.loads((r / "manifests" / cur).read_text())
    except (OSError, ValueError) as e:
        raise FgError(f"The store's manifest cannot be read: {e}")
    if m.get("format") not in FGMPN_MANIFESTS:
        raise FgError(f"The store's manifest format {m.get('format')!r} is not one this Studio reads ({', '.join(FGMPN_MANIFESTS)}).")
    files = list(m.get("segments") or []) + list(m.get("sedges") or [])
    missing = [e["file"] for e in files if not (r / "segments" / e["file"]).is_file()]
    wrong = [e["file"] for e in files if (r / "segments" / e["file"]).is_file() and (r / "segments" / e["file"]).stat().st_size != e["bytes"]]
    disk = sum(p.stat().st_size for p in r.rglob("*") if p.is_file())
    return {"format": m["format"], "generation": m.get("generation"), "manifest": cur, "segments": len(m.get("segments") or []),
            "nodes": sum(int(e.get("nodes") or 0) for e in m.get("segments") or []), "bytes": disk, "missing": missing, "wrong_size": wrong,
            "ok": not missing and not wrong}


def verify_store(root: str | Path, progress: Progress | None = None) -> dict:
    """Size and SHA-256 of every segment against the store's manifest."""
    from ..catalogue.download import sha256_file
    r = Path(root)
    info = inspect_store(r)
    m = json.loads((r / "manifests" / info["manifest"]).read_text())
    files = list(m.get("segments") or []) + list(m.get("sedges") or [])
    total, done, bad = sum(int(e["bytes"]) for e in files), 0, []
    for e in files:
        p = r / "segments" / e["file"]
        if not p.is_file():
            bad.append(f"{e['file']}: missing")
            continue
        if p.stat().st_size != e["bytes"]:
            bad.append(f"{e['file']}: {p.stat().st_size} bytes, the manifest says {e['bytes']}")
            done += e["bytes"]
            continue
        base = done

        def step(n, _b=[0]):
            nonlocal base
            base += n
            if progress:
                progress("checking " + e["file"], base, total)
        if sha256_file(p, step) != e["sha256"]:
            bad.append(f"{e['file']}: SHA-256 differs from the manifest")
        done += e["bytes"]
    return {"ok": not bad, "problems": bad, "files": len(files), "bytes": total}


# ------------------------------------------------------------------------------------------------ explore: a loopback netserve
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_cmd() -> tuple[list[str], dict]:
    cmd, env, why = _interpreter()
    if why:
        raise FgError(why, 501)
    return cmd, env


class _Server:
    def __init__(self, root: Path, name: str | None):
        self.root, self.port = root, _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        cmd, env = serve_cmd()
        cmd = cmd + [str(root), "--host", "127.0.0.1", "--port", str(self.port)] + (["--name", name] if name else [])
        log = Path(os.environ.get("MPN_STUDIO_STATE_DIR") or Path.home() / ".local/state/mpn-studio")
        log.mkdir(parents=True, exist_ok=True)
        self.log_path = log / f"fgmpn-netserve-{self.port}.log"
        self.proc = subprocess.Popen(cmd, stdout=self.log_path.open("ab"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                     env={**os.environ, **env})

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.alive():
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def tail(self, n: int = 6) -> str:
        try:
            return " | ".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""


_SERVERS: dict[str, _Server] = {}
_LOCK = threading.Lock()


STATS_MAX_NODES = 2_000_000          # counting a bigger store is a job for the machine that built it; it ships its own statistics


def ensure_stats(root: Path, info: dict) -> bool:
    """The network explorer's counts by domain, kind and dataset come from `stats/gen-N.json`. A store without it is counted once,
    but only a store Studio itself installed (it never writes into a folder the user keeps, and never counts a huge one)."""
    from ..desktop import paths
    gen = info.get("generation")
    if gen is None or (root / "stats" / f"gen-{int(gen):06d}.json").exists():
        return True
    try:
        root.relative_to((paths.data_dir() / "releases").resolve())
    except ValueError:
        return False
    if info["nodes"] > STATS_MAX_NODES:
        return False
    try:
        cmd, env = serve_cmd()
        subprocess.run(cmd + ["stats", str(root)], capture_output=True, timeout=900, check=True, stdin=subprocess.DEVNULL, env={**os.environ, **env})
    except (subprocess.SubprocessError, OSError, FgError):
        return False
    return True


def serve(root: str | Path, name: str | None = None, timeout: float = 60) -> str:
    """Start (or reuse) a read-only netserve for the store; returns its loopback address once it answers."""
    import httpx
    r = Path(root).resolve()
    info = inspect_store(r)
    if not info["ok"]:
        raise FgError("The store is incomplete: " + "; ".join((info["missing"] + info["wrong_size"])[:3]) + ". Verify it, or install it again.", 409)
    rt = runtime_info()
    if not rt["available"]:
        raise FgError(rt["reason"], 501)
    with _LOCK:
        s = _SERVERS.get(str(r))
        if s and s.alive():
            return s.url
        ensure_stats(r, info)
        s = _Server(r, name)
        _SERVERS[str(r)] = s
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not s.alive():
            raise FgError(f"The FG-MPN server stopped while starting: {s.tail()}", 500)
        try:
            if httpx.get(s.url + "/api/network/v1/overview", timeout=3).status_code == 200:
                return s.url
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    s.stop()
    raise FgError("The FG-MPN server did not answer in time.", 504)


def stop_all() -> None:
    for s in list(_SERVERS.values()):
        s.stop()
    _SERVERS.clear()


atexit.register(stop_all)
