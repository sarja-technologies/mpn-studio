"""FG-MPN stores on this computer: checked without the fgmpn runtime, explored through it when it is installed."""
import subprocess
import sys

import pytest

import relhost
from mpnstudio.mpnnet import fgmpn_local as fl


def write_store(root):
    for p, b in relhost.fg_store().items():
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_bytes(b)
    return root


def test_inspect_reads_counts_from_the_stores_own_manifest(tmp_path):
    info = fl.inspect_store(write_store(tmp_path / "s"))
    assert info["ok"] and info["nodes"] == 42 and info["segments"] == 2 and info["generation"] == 1 and info["bytes"] > 390_000


def test_inspect_names_what_is_wrong(tmp_path):
    root = write_store(tmp_path / "s")
    (root / "segments" / "seg-000002.fgs").unlink()
    (root / "segments" / "seg-000001.fgs").write_bytes(b"short")
    info = fl.inspect_store(root)
    assert not info["ok"] and info["missing"] == ["seg-000002.fgs"] and info["wrong_size"] == ["seg-000001.fgs"]
    with pytest.raises(fl.FgError) as e:
        fl.inspect_store(tmp_path)
    assert "not an FG-MPN store" in str(e.value)
    (root / "manifests" / "gen-000001.json").write_text('{"format": "fgmpn-manifest/7"}')
    with pytest.raises(fl.FgError) as e:
        fl.inspect_store(root)
    assert "fgmpn-manifest/7" in str(e.value)


def test_verify_finds_a_flipped_byte_and_reports_progress(tmp_path):
    root = write_store(tmp_path / "s")
    seen = []
    assert fl.verify_store(root, lambda l, d, t: seen.append((d, t)))["ok"] and seen[-1][0] == seen[-1][1]
    seg = root / "segments" / "seg-000001.fgs"
    b = bytearray(seg.read_bytes())
    b[5] ^= 1
    seg.write_bytes(bytes(b))
    v = fl.verify_store(root)
    assert not v["ok"] and "seg-000001.fgs: SHA-256 differs" in v["problems"][0]


def test_runtime_info_says_what_is_missing_or_present(monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a: None if n == "fgmpn" else real(n, *a))
    r = fl.runtime_info()
    assert not r["available"] and "not installed" in r["reason"] and r["supported"] == ["0.2"]
    with pytest.raises(fl.FgError) as e:
        fl.serve("/nonexistent")
    assert "not an FG-MPN store" in str(e.value)


def test_serve_refuses_an_incomplete_store(tmp_path):
    root = write_store(tmp_path / "s")
    (root / "segments" / "seg-000002.fgs").unlink()
    pytest.importorskip("fgmpn")
    with pytest.raises(fl.FgError) as e:
        fl.serve(root)
    assert e.value.status == 409 and "incomplete" in str(e.value)


@pytest.fixture
def demo_store(tmp_path):
    pytest.importorskip("fgmpn")
    out = tmp_path / "demo-store"
    r = subprocess.run([sys.executable, "-m", "fgmpn", "demo", str(out)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    yield out
    fl.stop_all()


def test_explore_a_real_store_through_the_runtime(demo_store):
    import httpx
    assert fl.verify_store(demo_store)["ok"]
    url = fl.serve(demo_store, "Demo")
    assert fl.serve(demo_store) == url                       # one server per store
    ov = httpx.get(url + "/api/network/v1/overview").json()
    assert ov["engine"] == "fgmpn" and ov["api"] == "mpn-network/1"
    hits = httpx.get(url + "/api/network/v1/search", params={"q": "Douglas"}).json()["results"]
    assert hits and hits[0]["title"] == "Douglas Adams"


def test_linking_a_store_folder_opens_it_in_the_workspace(demo_store, studio_home, monkeypatch):
    monkeypatch.setenv("MPN_DESKTOP", "1")
    import importlib
    import mpnstudio.api.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.create_app(), client=("127.0.0.1", 5000)) as c:
        r = c.post("/api/projects/link", json={"name": "Demo FG", "sources": [{"mode": "fgmpn", "target": str(demo_store)}]})
        assert r.status_code == 200, r.text
        assert c.get("/api/ws/overview").json()["engine"] == "fgmpn"
        assert c.get("/api/ws/fgmpn/storage").json()["fgmpn"]
        imp = c.post("/api/projects/import", json={"path": str(demo_store), "name": "Again"})       # a store folder is recognised on import
        assert imp.status_code == 200, imp.text
        bad = c.post("/api/projects/link/probe", json={"mode": "fgmpn", "target": str(demo_store.parent)})
        assert bad.status_code >= 400 and "not an FG-MPN store" in bad.text
    monkeypatch.delenv("MPN_DESKTOP")
    importlib.reload(appmod)


def test_a_store_studio_installed_is_counted_once_but_a_folder_the_user_keeps_is_never_written(demo_store, studio_home):
    import shutil
    from mpnstudio.desktop import paths
    info = fl.inspect_store(demo_store)
    assert fl.ensure_stats(demo_store.resolve(), info) is False                      # the user's own folder: no stats/ added
    assert not (demo_store / "stats").exists()
    owned = paths.data_dir() / "releases" / "mpn" / "x" / "1" / "store"
    owned.parent.mkdir(parents=True)
    shutil.copytree(demo_store, owned)
    assert fl.ensure_stats(owned.resolve(), fl.inspect_store(owned)) is True
    assert list((owned / "stats").glob("gen-*.json"))


def test_packaged_app_runs_the_runtime_it_carries(demo_store, studio_home, monkeypatch):
    """The packaged backend carries the public fgmpn wheel: no Python of the user's is needed, and its own executable plays the
    reader's role (`mpn-backend fgmpn-serve`)."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd, env = fl.serve_cmd()
    assert cmd == [sys.executable, "fgmpn-serve"] and env == {}
    r = fl.runtime_info()
    assert r["available"] and r["bundled"] and r["version"].startswith("0.2.")
    monkeypatch.delattr(sys, "frozen")
    # the entry point of the packaged executable, run from source: the same role
    import httpx, pathlib, socket, time
    entry = pathlib.Path(__file__).resolve().parent.parent / "packaging" / "backend" / "entry.py"
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        port = k.getsockname()[1]
    env = {**__import__("os").environ, "PYTHONPATH": str(entry.parent.parent.parent)}
    p = subprocess.Popen([sys.executable, str(entry), "fgmpn-serve", str(demo_store), "--port", str(port), "--name", "Packaged"],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                ov = httpx.get(f"http://127.0.0.1:{port}/api/network/v1/overview", timeout=2).json()
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        assert ov["engine"] == "fgmpn" and ov["name"] == "Packaged"
    finally:
        p.terminate()
        p.wait(10)


def test_the_reader_answers_the_network_routes(demo_store):
    """Counts, search, browsing in pages, a node with its relationships and the canvas graph, and the FG-MPN routes."""
    import httpx
    url = fl.serve(demo_store, "Demo")
    g = lambda path, **q: httpx.get(url + path, params=q).json()
    ov = g("/api/network/v1/overview")
    assert ov["validated_nodes"] > 0 and ov["domains"] and ov["datasets"] and sum(d["nodes"] for d in ov["domains"]) == ov["validated_nodes"]
    first = g("/api/network/v1/browse", limit=5)
    assert len(first["results"]) == 5 and first["cursor"]
    second = g("/api/network/v1/browse", limit=5, cursor=first["cursor"])
    assert second["results"] and not {r["id"] for r in first["results"]} & {r["id"] for r in second["results"]}
    dom = first["results"][0]["domain"]
    assert all(r["domain"] == dom for r in g("/api/network/v1/browse", domain=dom, limit=10)["results"])
    hit = g("/api/network/v1/search", q="Douglas")["results"][0]
    n = g(f"/api/network/v1/node/{hit['id']}")
    assert n["node"]["title"] == "Douglas Adams" and n["node"]["cells"]["knowledge"]
    assert g(f"/api/network/v1/node/{hit['id']}/relationships", direction="out")["results"] is not None
    gr = g(f"/api/network/v1/node/{hit['id']}/graph", limit=10)
    assert gr["center"] == hit["id"] and gr["nodes"]
    assert httpx.get(url + "/api/network/v1/node/" + "0" * 32).status_code == 404
    fgn = g(f"/api/fgmpn/v1/node/{hit['id']}")
    assert fgn["status"] == "STORED" and fgn["provenance"][0]["dataset"] and fgn["derived_listed"] is False and fgn["compare"] is False
    st = g("/api/fgmpn/v1/storage")
    assert st["fgmpn"]["deployed_bytes"] > 0 and st["fgmpn"]["segments"] == 1
    assert g("/api/fgmpn/v1/resolve", address="fg://nope")["status"] == "INVALID"
    assert httpx.get(url + "/api/fgmpn/v1/nothing").status_code == 404


def test_engines_agree(demo_store, monkeypatch):
    """The native reader and the Python engine give the same search, node and counts."""
    from mpnstudio.mpnnet.fgmpn_net import FgReader
    out = {}
    for eng in ("python", "native"):
        monkeypatch.setenv("FGMPN_ENGINE", eng)
        r = FgReader(demo_store)
        h = r.search("Douglas")["results"]
        out[eng] = (r.overview()["validated_nodes"], [x["id"] for x in h], r.node(h[0]["id"])["node"]["title"], r.browse(limit=3)["results"])
    assert out["python"] == out["native"]


def test_a_reader_does_not_outlive_the_service_that_started_it(demo_store, tmp_path):
    """The service runs a reader in its own session. If the service is killed, the reader goes with it within seconds."""
    import os, time
    pidfile = tmp_path / "pid"
    code = (
        "import subprocess, sys, os\n"
        f"p = subprocess.Popen([sys.executable, '-m', 'mpnstudio.mpnnet.fgmpn_serve', {str(demo_store)!r}, '--port', '0'], start_new_session=True,"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "import time; time.sleep(4); os._exit(0)\n")
    env = {**os.environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parent.parent)}
    subprocess.run([sys.executable, "-c", code], env=env, timeout=60)
    pid = int(pidfile.read_text())

    def alive():
        try:
            os.kill(pid, 0)
            return open(f"/proc/{pid}/stat").read().split()[2] != "Z"
        except (ProcessLookupError, OSError):
            return False
    for _ in range(40):
        if not alive():
            break
        time.sleep(0.25)
    leaked = alive()
    if leaked:
        os.kill(pid, 9)
    assert not leaked, "the reader was still running 10 s after its parent died"
