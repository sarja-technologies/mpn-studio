"""Launcher for the optional local runtime and worker (Settings, Compute).

These tests exercise the process handling (start, readiness, crash report, stop, no leftover process) with a tiny
stand-in server module. They say nothing about model quality; the real runtime is covered by
scripts/runtime-smoke.sh, which needs a Python environment with torch.
"""
import os
import socket
import sys
import textwrap
import time

import pytest

from mpnstudio.desktop import compute, settings

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="process groups")


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k, d in (("CONFIG", "c"), ("DATA", "d"), ("CACHE", "x"), ("STATE", "s")):
        monkeypatch.setenv(f"MPN_STUDIO_{k}_DIR", str(tmp_path / d))
    compute._probe_cache.clear()
    return tmp_path


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _fake_caps(py, models):
    def caps(models_dir=None):
        return {"python": {"path": py}, "models_dir": str(models), "features": [
            {"id": "local_inference", "available": True, "reason": None},
            {"id": "local_training", "available": False, "reason": "no CUDA"}]}
    return caps


def test_unconfigured_python_is_reported_not_started(home):
    caps = compute.capabilities()
    feats = {f["id"]: f for f in caps["features"]}
    assert not feats["local_inference"]["available"] and "Python" in feats["local_inference"]["reason"]
    assert not feats["local_training"]["available"]
    with pytest.raises(ValueError):
        compute.LocalRuntime().start()


def test_python_without_the_model_stack_is_refused(home, monkeypatch):
    monkeypatch.setenv("MPN_DB", str(home / "x.sqlite"))
    settings.update({"compute": {"python": sys.executable}, "storage": {"models_dir": str(home)}})
    probe = compute.capabilities()["python"]
    if not probe.get("missing"):
        pytest.skip("this interpreter has the full model stack")
    reason = {f["id"]: f for f in compute.capabilities()["features"]}["local_inference"]["reason"]
    assert "lacks" in reason and "torch" in reason
    with pytest.raises(ValueError, match="lacks"):
        compute.LocalRuntime().start()


def test_start_serves_then_stop_leaves_nothing(home, monkeypatch):
    src = home / "src" / "stubpkg"
    src.mkdir(parents=True)
    (src.parent / "mpnstudio" / "runtime").mkdir(parents=True)
    (src.parent / "mpnstudio" / "runtime" / "server.py").write_text(textwrap.dedent('''
        import http.server, json, os
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"available": True, "db": os.environ["MPN_DB"], "models": os.environ["MPN_MODELS"],
                                   "offline": os.environ.get("HF_HUB_OFFLINE")}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        http.server.HTTPServer(("127.0.0.1", int(os.environ["MPN_RUNTIME_PORT"])), H).serve_forever()
    '''))
    (src.parent / "mpnstudio" / "__init__.py").write_text("")
    (src.parent / "mpnstudio" / "runtime" / "__init__.py").write_text("")
    monkeypatch.setattr(compute, "source_dir", lambda: src.parent)
    monkeypatch.setattr(compute, "capabilities", _fake_caps(sys.executable, home / "models"))
    monkeypatch.setenv("MPN_DB", str(home / "p" / "store.sqlite"))
    rt = compute.LocalRuntime()
    st = rt.start()
    pid, port = rt.rt.pid, rt.port
    assert st["runtime"] and not st["worker"] and rt.runtime_url == f"http://127.0.0.1:{port}"
    assert st["engine"]["db"].endswith("p/store.sqlite") and st["engine"]["offline"] == "1"       # the open project, offline hub
    assert st["engine"]["models"] == str(home / "models")
    assert rt.start()["runtime"]                                      # starting twice does not start two
    out = rt.stop()
    assert not out["runtime"] and rt.runtime_url is None
    assert not _port_open(port)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_runtime_that_dies_at_start_reports_its_own_words(home, monkeypatch):
    pkg = home / "src" / "mpnstudio" / "runtime"
    pkg.mkdir(parents=True)
    (home / "src" / "mpnstudio" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "server.py").write_text("raise SystemExit('torch: CUDA driver mismatch')\n")
    monkeypatch.setattr(compute, "source_dir", lambda: home / "src")
    monkeypatch.setattr(compute, "capabilities", _fake_caps(sys.executable, home))
    monkeypatch.setenv("MPN_DB", str(home / "s.sqlite"))
    rt = compute.LocalRuntime()
    with pytest.raises(ValueError, match="CUDA driver mismatch"):
        rt.start()
    assert not rt.runtime_alive and rt.status()["error"]


def test_a_project_is_required(home, monkeypatch):
    monkeypatch.delenv("MPN_DB", raising=False)
    monkeypatch.setattr(compute, "capabilities", _fake_caps(sys.executable, home))
    with pytest.raises(ValueError, match="project"):
        compute.LocalRuntime().start()
