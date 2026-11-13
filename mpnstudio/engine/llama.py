"""Local llama.cpp servers that hold the cognitive core and the frozen encoder.

Yaob runs two private llama-server processes bound to 127.0.0.1 on free ports: one for the core (with its multimodal
projector) and one for the embedding model. The Yaob backend is the only client; neither is exposed.

Device choice (YAOB_DEVICE=auto|cuda|vulkan|cpu, default auto): each packaged build is asked to list its devices
(`llama-server --list-devices`); the first of cuda, vulkan, cpu that reports a usable device is used. A software
Vulkan device (llvmpipe) is not counted as a GPU. If the chosen GPU build fails to start, the next one is tried.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

VARIANTS = ("cuda", "vulkan", "cpu")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(bindir: Path) -> dict:
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(bindir) + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    return env


def list_devices(bindir: Path) -> list[dict]:
    """Devices a llama.cpp build can use, parsed from `llama-server --list-devices`."""
    exe = bindir / "llama-server"
    if not exe.exists():
        return []
    try:
        r = subprocess.run([str(exe), "--list-devices"], capture_output=True, text=True, timeout=60, env=_env(bindir))
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in (r.stdout + "\n" + r.stderr).splitlines():
        m = re.match(r"\s*(\w+\d*):\s*(.+?)\s*\((\d+)\s*MiB,\s*(\d+)\s*MiB free\)", line)
        if m:
            out.append({"id": m.group(1), "name": m.group(2), "total_mib": int(m.group(3)), "free_mib": int(m.group(4))})
    return out


def choose_variant(runtime_dir: Path, want: str = "auto") -> tuple[str, Path, list[dict]]:
    """(variant, bin dir, devices). CPU is always possible."""
    order = VARIANTS if want == "auto" else (want,) + tuple(v for v in VARIANTS if v != want and v == "cpu")
    for v in order:
        d = Path(runtime_dir) / v
        if not (d / "llama-server").exists():
            continue
        if v == "cpu":
            return v, d, []
        devs = [x for x in list_devices(d) if "llvmpipe" not in x["name"].lower()]
        if devs:
            return v, d, devs
    raise RuntimeError(f"no usable llama.cpp build under {runtime_dir}")


class LlamaServer:
    def __init__(self, name: str, bindir: Path, args: list[str], log_path: Path, variant: str):
        self.name, self.bindir, self.args, self.log_path, self.variant = name, Path(bindir), list(args), Path(log_path), variant
        self.port = free_port()
        self.key = secrets.token_hex(16)      # private to this process: browsers and other local users cannot drive the server
        self.proc: subprocess.Popen | None = None
        self.started_at = None
        self.load_s = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 600) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.bindir / "llama-server"), *self.args, "--host", "127.0.0.1", "--port", str(self.port), "--no-ui"]
        t0 = time.perf_counter()
        self._log = open(self.log_path, "ab")
        self._log.write(("\n==== " + time.strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(cmd) + "\n").encode())
        self._log.flush()
        env = _env(self.bindir)
        env["LLAMA_API_KEY"] = self.key        # passed by environment, not argv, so it does not show in the process list
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        while time.perf_counter() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.name} server exited during start (code {self.proc.returncode}); see {self.log_path}: {self.tail(6)}")
            try:
                if self.get("/health", timeout=2).get("status") == "ok":
                    self.load_s = round(time.perf_counter() - t0, 1)
                    self.started_at = time.time()
                    return
            except (OSError, http.client.HTTPException, ValueError):
                pass
            time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"{self.name} server did not become ready in {timeout:.0f} s")

    def tail(self, n: int = 20) -> str:
        try:
            return " | ".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=20)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.proc = None

    # ---------------------------------------------------------------- HTTP
    def _conn(self, timeout: float):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)

    def _h(self) -> dict:
        return {"Content-Type": "application/json", "Authorization": "Bearer " + self.key}

    def get(self, path: str, timeout: float = 30):
        c = self._conn(timeout)
        try:
            c.request("GET", path, headers=self._h())
            r = c.getresponse()
            body = r.read()
            return json.loads(body) if body else {}
        finally:
            c.close()

    def post(self, path: str, body: dict, timeout: float = 3600) -> dict:
        c = self._conn(timeout)
        try:
            c.request("POST", path, body=json.dumps(body), headers=self._h())
            r = c.getresponse()
            data = r.read()
            if r.status >= 400:
                raise LlamaError(r.status, data.decode(errors="replace")[:2000])
            return json.loads(data)
        finally:
            c.close()

    def stream(self, path: str, body: dict, timeout: float = 3600, cancel: threading.Event | None = None):
        """Yield the JSON objects of an SSE stream. Closing early (cancel) stops generation in llama-server."""
        c = self._conn(timeout)
        try:
            c.request("POST", path, body=json.dumps({**body, "stream": True}), headers={**self._h(), "Accept": "text/event-stream"})
            r = c.getresponse()
            if r.status >= 400:
                raise LlamaError(r.status, r.read().decode(errors="replace")[:2000])
            while True:
                if cancel is not None and cancel.is_set():
                    return
                line = r.readline()
                if not line:
                    return
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    return
                yield json.loads(data)
        finally:
            c.close()

    # ---------------------------------------------------------------- helpers
    def tokenize(self, text: str, special: bool = True) -> list[int]:
        return self.post("/tokenize", {"content": text, "add_special": special, "parse_special": True}, timeout=60)["tokens"]

    def detokenize(self, ids: list[int]) -> str:
        return self.post("/detokenize", {"tokens": ids}, timeout=60)["content"]

    def apply_template(self, messages: list[dict], **kw) -> str:
        return self.post("/apply-template", {"messages": messages, **kw}, timeout=60)["prompt"]

    def props(self) -> dict:
        return self.get("/props")


class LlamaError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"llama-server HTTP {status}: {text}")
        self.status, self.text = status, text


def gpu_snapshot() -> list[dict]:
    """NVIDIA GPU memory from nvidia-smi when available (informational)."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        out = []
        for line in r.stdout.strip().splitlines():
            n, t, u, d = [x.strip() for x in line.split(",")]
            out.append({"name": n, "total_mib": int(t), "used_mib": int(u), "driver": d})
        return out
    except Exception:
        return []
