"""An FG-MPN network server (fgmpn.netserve) opened as a remote source: the network API works as for any network, and the
FG-MPN routes (derived statements, resolve, storage, compare) are passed through read-only. A local pack folder has none."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mpnstudio.mpnnet.service import NetError, Remote
from mpnstudio.workspace.network import Workspace

NID = "0123456789abcdef0123456789abcdef"
SEEN: list[str] = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append(self.path)
        p = self.path.split("?")[0]
        body = None
        if p == "/api/network/v1/overview":
            body = {"api": "mpn-network/1", "name": "FG-MPN test", "engine": "fgmpn", "validated_nodes": 3, "relationships": 2, "packs": []}
        elif p == f"/api/fgmpn/v1/node/{NID}":
            body = {"status": "STORED", "derived": [{"label": "located in", "proof": {"steps": [{"s": "wd:Q1", "p": "P131", "o": "wd:Q2"}]}}]}
        elif p == "/api/fgmpn/v1/resolve":
            body = {"status": "UNKNOWN", "address": self.path}
        elif p == "/api/fgmpn/v1/storage":
            body = {"fgmpn": {"deployed_bytes": 10}}
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        b = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture(scope="module")
def url():
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def test_remote_passes_fgmpn_routes_through(url):
    r = Remote(url)
    assert r.fgmpn(f"node/{NID}", {})["status"] == "STORED"
    assert r.fgmpn("resolve", {"address": "fg://infer/closure.P131@2/wd:Q1/wd:Q2", "other": "x"})["status"] == "UNKNOWN"
    assert "other=" not in SEEN[-1] and "address=" in SEEN[-1]
    with pytest.raises(NetError) as e:
        r.fgmpn("about", {})
    assert e.value.status == 404
    r.close()


def test_workspace_accepts_only_known_fgmpn_paths(url, tmp_path):
    w = Workspace("t", {"kind": "linked", "network": {"sources": [{"mode": "remote", "target": url}]}}, tmp_path)
    w.connect()
    assert w.overview()["engine"] == "fgmpn"
    assert w.fgmpn("storage")["fgmpn"]["deployed_bytes"] == 10
    assert w.fgmpn(f"node/{NID}")["derived"][0]["label"] == "located in"
    for bad in ("node/../../etc", "node/xyz", "anything", f"node/{NID}/edit"):
        with pytest.raises(NetError):
            w.fgmpn(bad)
    w.close()


def test_routes_through_a_linked_project(url, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from mpnstudio.desktop import paths
    for k in ("MPN_STUDIO_CONFIG_DIR", "MPN_STUDIO_DATA_DIR", "MPN_STUDIO_CACHE_DIR", "MPN_STUDIO_STATE_DIR",
              "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths.ensure()
    from mpnstudio.api import app as appmod
    monkeypatch.setattr(appmod, "DESKTOP", True)
    monkeypatch.setattr(appmod, "SESSION_TOKEN", "t")
    c = TestClient(appmod.create_app(), headers={"x-mpn-token": "t"})
    r = c.post("/api/projects/link", json={"name": "FG test", "sources": [{"mode": "remote", "target": url}]})
    assert r.status_code == 200, r.text
    assert c.get("/api/ws/overview").json()["engine"] == "fgmpn"
    r = c.get(f"/api/ws/fgmpn/node/{NID}")
    assert r.status_code == 200, r.text
    step = r.json()["derived"][0]["proof"]["steps"][0]
    assert step["p"] == "P131" and step["p_label"] and step["p_label"] != "P131"
    assert c.get("/api/ws/fgmpn/storage").json()["fgmpn"]["deployed_bytes"] == 10
    assert c.get("/api/ws/fgmpn/node/NOTHEX").status_code == 400
    assert c.get("/api/ws/fgmpn/resolve", params={"address": "http://x"}).status_code == 400
    assert c.get("/api/ws/fgmpn/about").status_code == 404           # the stub has no about route
