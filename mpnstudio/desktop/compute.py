"""Compute capabilities and the optional remote link.

Local mode is the default and needs nothing. Features that need a model runtime (local inference, local
training) are reported as unavailable, with the reason, unless a compatible Python environment and weights are
actually present. Nothing is simulated.

Remote mode is an SSH tunnel to a machine that runs the MPN Studio API/runtime (for example the DGX Spark).
Authentication is whatever the user's ssh configuration already does (keys/agent); no password or key is ever
stored or prompted for here (BatchMode). Nothing connects until the user presses Connect.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import settings as S

CORE_DIR = "Qwen3-4B-Instruct-2507"
ENCODER_DIR = "Qwen3-Embedding-0.6B"


def _gpu() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            name, mem, drv = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
            return {"name": name, "memory_mib": int(mem), "driver": drv}
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


# What the Yaob runtime, the job worker and the training code import. The bundled backend carries none of the model stack.
REQUIRED_MODULES = ("torch", "transformers", "safetensors", "peft", "fastapi", "uvicorn", "httpx", "numpy", "psutil")


def source_dir() -> Path | None:
    """Plain .py sources of the mpnstudio package, for an external Python to run the runtime and worker from.
    Packaged app: <backend>/pysrc (copied there by scripts/build-backend.sh). From a source checkout: the repository."""
    cands = []
    if getattr(sys, "frozen", False):
        cands.append(Path(sys.executable).resolve().parent / "pysrc")
    cands.append(Path(__file__).resolve().parents[2])
    return next((c for c in cands if (c / "mpnstudio" / "runtime" / "server.py").is_file()), None)


_probe_cache: dict[str, tuple[float, dict]] = {}


def _probe_python(py: str) -> dict:
    """Importing torch takes seconds and the Settings window asks every few seconds, so the answer is kept for 30 s."""
    hit = _probe_cache.get(py)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    out = _probe_python_now(py)
    _probe_cache[py] = (time.time(), out)
    return out


def _probe_python_now(py: str) -> dict:
    code = ("import json,importlib.util as u,sys;mods=%r;o={'version':'%%d.%%d.%%d'%%sys.version_info[:3],"
            "'missing':[m for m in mods if u.find_spec(m) is None]}\n"
            "if 'torch' not in o['missing']:\n"
            " import torch;o['cuda']=bool(torch.cuda.is_available());o['torch_version']=torch.__version__\n"
            "print(json.dumps(o))") % (list(REQUIRED_MODULES),)
    try:
        r = subprocess.run([py, "-I", "-c", code], capture_output=True, text=True, timeout=60)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        return json.loads(last) if last.startswith("{") else {"error": (r.stderr.strip().splitlines() or ["did not run"])[-1]}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"error": str(e)}


def capabilities(models_dir: str | None = None) -> dict:
    s = S.load()
    st, comp = s["storage"], s["compute"]
    models = Path(st["models_dir"]).expanduser() if st.get("models_dir") else None
    gpu = _gpu()
    py = comp.get("python")
    probe = _probe_python(py) if py else None
    have_core = bool(models and (models / CORE_DIR).is_dir())
    have_enc = bool(models and (models / ENCODER_DIR).is_dir())

    def reason_runtime() -> str | None:
        if not py:
            return "No Python environment with the Yaob runtime is configured (Settings, Compute). Not needed for editing."
        if probe and probe.get("error"):
            return f"The configured Python does not run: {probe['error']}"
        if probe and probe.get("missing"):
            return "The configured Python lacks: " + ", ".join(probe["missing"]) + "."
        if source_dir() is None:
            return "The runtime sources are missing from this installation."
        if not have_core or not have_enc:
            miss = [n for n, ok in ((CORE_DIR, have_core), (ENCODER_DIR, have_enc)) if not ok]
            return "Model weights not found in the models folder: " + ", ".join(miss) if models else "No models folder is set (Settings, Storage)."
        return None

    inf = reason_runtime()
    train = inf or (None if (probe and probe.get("cuda")) else "Training needs a CUDA GPU; none is usable by the configured Python.")
    caps = [
        {"id": "edit", "label": "Network editing, search, inspection", "available": True, "reason": None},
        {"id": "files", "label": "Projects, import, export, backup", "available": True, "reason": None},
        {"id": "local_inference", "label": "Local Yaob inference", "available": inf is None, "reason": inf},
        {"id": "local_training", "label": "Local training and evaluation", "available": train is None, "reason": train},
    ]
    return {"mode": comp["mode"], "gpu": gpu, "python": {"path": py, **(probe or {})} if py else None,
            "models_dir": str(models) if models else None, "models_found": {CORE_DIR: have_core, ENCODER_DIR: have_enc},
            "features": caps, "remote": remote.status(), "local": local.status()}


# ---------------------------------------------------------------------------- remote link
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_json(url: str, timeout: float = 4.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:    # loopback tunnel ends only
        return json.loads(r.read())


class RemoteLink:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.api_port = self.rt_port = 0
        self.host = ""
        self.started = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def runtime_url(self) -> str | None:
        return f"http://127.0.0.1:{self.rt_port}" if self.connected else None

    def connect(self, cfg: dict) -> dict:
        with self._lock:
            self.disconnect()
            host = (cfg.get("ssh_host") or "").strip()
            if not host or host.startswith("-"):
                raise ValueError("Enter the SSH host (an alias from your ssh config, or user@host).")
            ssh = shutil.which("ssh")
            if not ssh:
                raise ValueError("The ssh client is not installed.")
            self.api_port, self.rt_port, self.host, self.error = _free_port(), _free_port(), host, None
            cmd = [ssh, "-N", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=8",
                   "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                   "-L", f"127.0.0.1:{self.api_port}:127.0.0.1:{int(cfg.get('api_port', 8840))}",
                   "-L", f"127.0.0.1:{self.rt_port}:127.0.0.1:{int(cfg.get('runtime_port', 8841))}"]
            if cfg.get("ssh_config"):
                cmd += ["-F", str(Path(cfg["ssh_config"]).expanduser())]
            cmd += ["--", host]
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
            self.started = time.time()
            for _ in range(40):
                if self.proc.poll() is not None:
                    err = (self.proc.stderr.read() or b"").decode(errors="replace").strip().splitlines()
                    self.error = err[-1] if err else "ssh exited"
                    self.proc = None
                    raise ValueError(f"Could not connect: {self.error}")
                try:
                    with socket.create_connection(("127.0.0.1", self.api_port), timeout=0.3):
                        break
                except OSError:
                    time.sleep(0.25)
            return self.status()

    def disconnect(self) -> None:
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
        self.api_port = self.rt_port = 0

    def status(self) -> dict:
        if not self.connected:
            return {"connected": False, "host": self.host, "error": self.error}
        out: dict = {"connected": True, "host": self.host, "since": self.started, "api": None, "runtime": None}
        try:
            out["api"] = _get_json(f"http://127.0.0.1:{self.api_port}/api/health")
        except Exception as e:
            out["api"] = {"ok": False, "error": str(e)}
        try:
            out["runtime"] = _get_json(f"http://127.0.0.1:{self.api_port}/api/runtime")
        except Exception as e:
            out["runtime"] = {"available": False, "reason": str(e)}
        return out

    def remote_jobs(self) -> dict:
        if not self.connected:
            raise ValueError("Not connected to remote compute.")
        return _get_json(f"http://127.0.0.1:{self.api_port}/api/jobs?limit=30", 8.0)


remote = RemoteLink()


# ---------------------------------------------------------------------------- local runtime and worker
def _tail(path: Path, n: int = 4) -> str:
    try:
        return " | ".join(path.read_text(errors="replace").strip().splitlines()[-n:])
    except OSError:
        return ""


def _alive(p: subprocess.Popen | None) -> bool:
    return p is not None and p.poll() is None


def _terminate(p: subprocess.Popen | None, grace: float) -> None:
    if not _alive(p):
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(timeout=grace)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass


class LocalRuntime:
    """The Yaob runtime and the job worker, run from the user's own Python environment (Settings, Compute).

    Nothing starts until the user presses Start, and only when that environment really has the model stack, the
    weights are present, and (for the worker) a CUDA GPU is usable. Both processes work on the open project's own
    store and data folder, bind to loopback only, and run with the Hugging Face hub in offline mode.
    A training or evaluation job that is running is its own detached process and is not killed by Stop.
    """
    RUNTIME_MODULE = "mpnstudio.runtime.server"
    WORKER_MODULE = "mpnstudio.train.jobs"

    def __init__(self):
        self.rt: subprocess.Popen | None = None
        self.worker: subprocess.Popen | None = None
        self.port = 0
        self.started = 0.0
        self.error: str | None = None
        self._lock = threading.Lock()

    @property
    def runtime_alive(self) -> bool:
        return _alive(self.rt)

    @property
    def worker_alive(self) -> bool:
        return _alive(self.worker)

    @property
    def runtime_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.runtime_alive else None

    def _logs(self) -> Path:
        d = S.paths.state_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def start(self) -> dict:
        with self._lock:
            if self.runtime_alive:
                return self.status()
            self.error = None
            caps = capabilities()
            feat = {f["id"]: f for f in caps["features"]}
            if not feat["local_inference"]["available"]:
                raise ValueError(feat["local_inference"]["reason"] or "The local runtime is not available.")
            if not os.environ.get("MPN_DB"):
                raise ValueError("Open a project first. The runtime works on the open project.")
            py, src = caps["python"]["path"], source_dir()
            self.port = _free_port()
            env = {k: v for k, v in os.environ.items() if k.startswith(("MPN_DATA", "MPN_DB", "MPN_CHECKPOINT_ROOTS")) or k in ("PATH", "HOME", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "CUDA_VISIBLE_DEVICES")}
            env.update(MPN_MODELS=str(Path(caps["models_dir"])), MPN_RUNTIME_PORT=str(self.port), MPN_NO_SYSTEMD="1",
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUNBUFFERED="1")

            def spawn(module: str, args: list[str], log: Path) -> subprocess.Popen:
                with open(log, "ab") as f:
                    return subprocess.Popen([py, "-s", "-m", module, *args], cwd=str(src), env=env, stdin=subprocess.DEVNULL,
                                            stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
            rt_log = self._logs() / "runtime.log"
            self.rt = spawn(self.RUNTIME_MODULE, [], rt_log)
            self.started = time.time()
            for _ in range(240):                                  # up to ~60 s for the server socket
                if self.rt.poll() is not None:
                    self.error = f"The runtime stopped at start: {_tail(rt_log) or 'no output'}"
                    self.rt = None
                    raise ValueError(self.error)
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                        break
                except OSError:
                    time.sleep(0.25)
            else:
                _terminate(self.rt, 5)
                self.rt = None
                self.error = "The runtime did not open its port within a minute."
                raise ValueError(self.error)
            if feat["local_training"]["available"]:
                self.worker = spawn(self.WORKER_MODULE, ["worker"], self._logs() / "worker.log")
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            w, r = self.worker, self.rt
            self.worker = self.rt = None
            _terminate(w, 10)
            _terminate(r, 20)
            self.port = 0
            return self.status()

    def status(self) -> dict:
        out = {"runtime": self.runtime_alive, "worker": self.worker_alive, "since": self.started if self.runtime_alive else None,
               "error": self.error, "log": str(self._logs() / "runtime.log") if self.runtime_alive or self.error else None}
        if self.rt is not None and not self.runtime_alive:         # died after a good start
            out["error"] = self.error = f"The runtime stopped: {_tail(self._logs() / 'runtime.log') or 'no output'}"
            self.rt = None
        if self.runtime_alive:
            try:
                out["engine"] = _get_json(f"http://127.0.0.1:{self.port}/status", 2.0)
            except Exception as e:
                out["engine"] = {"available": False, "reason": f"not answering yet: {e}"}
        return out


local = LocalRuntime()
