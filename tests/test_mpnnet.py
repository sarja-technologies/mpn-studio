"""Open MPN Network: folder checks, remote API validation and query forwarding, and a real pack when one is present."""
import json
import os
from pathlib import Path

import httpx
import pytest

from mpnstudio.mpnnet import service as S

PACK = os.environ.get("MPN_TEST_PACK", "")


def test_local_refuses_folders_without_a_network(tmp_path):
    with pytest.raises(S.NetError, match="No MPN network here"):
        S.Local(str(tmp_path))
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "p00.sqlite.zst").write_bytes(b"")
    with pytest.raises(S.NetError, match="still compressed"):
        S.Local(str(tmp_path))


def test_local_refuses_a_partly_installed_pack(tmp_path):
    (tmp_path / "parts").mkdir()
    for i in range(2):
        (tmp_path / "parts" / f"p{i:02d}.sqlite").write_bytes(b"")
    (tmp_path / "manifest.json").write_text(json.dumps({"partitions": 64}))
    with pytest.raises(S.NetError, match="64 partitions but 2"):
        S.Local(str(tmp_path))


def _remote(handler):
    r = S.Remote.__new__(S.Remote)
    r.base = "http://spark:8844"
    r.client = httpx.Client(transport=httpx.MockTransport(handler))
    return r


def test_remote_forwards_only_known_parameters_and_paths():
    seen = []

    def h(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={"results": []})
    r = _remote(h)
    r.call("search", None, {"q": "Ada", "limit": 5, "evil": "x"})
    r.call("relationships", "abc", {"direction": "in"})
    assert seen[0] == "http://spark:8844/api/network/v1/search?q=Ada&limit=5"
    assert seen[1] == "http://spark:8844/api/network/v1/node/abc/relationships?direction=in"


def test_remote_errors_are_explained():
    r = _remote(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(S.NetError, match="HTTP 500"):
        r.call("overview", None, {})
    r = _remote(lambda req: httpx.Response(404))
    with pytest.raises(S.NetError, match="No such node"):
        r.call("node", "abc", {})


def test_remote_rejects_non_http_addresses():
    with pytest.raises(S.NetError, match="dashboard address"):
        S.Remote("file:///etc/passwd")


def test_service_rejects_odd_node_ids():
    s = S.Service()
    s.conn = _remote(lambda req: httpx.Response(200, json={}))
    with pytest.raises(S.NetError, match="Not a node id"):
        s.call("node", "../../x")


@pytest.mark.skipif(not PACK or not Path(PACK, "parts", "p00.sqlite").exists(), reason="set MPN_TEST_PACK to an installed pack")
def test_real_pack_search_node_graph():
    s = S.Service()
    st = s.open("local", PACK)
    assert st["open"] and st["mode"] == "local"
    ov = s.call("overview")
    assert ov["api"] == "mpn-network/1" and ov["validated_nodes"] > 0 and ov["read_only"]
    hits = s.call("search", params={"q": "Ada Lovelace", "limit": 5})["results"]
    assert hits and hits[0]["title"] == "Ada Lovelace"
    n = s.call("node", hits[0]["id"])
    assert n["node"]["id"] == hits[0]["id"] and "cells" in n["node"]
    g = s.call("graph", hits[0]["id"])
    assert g["center"] == hits[0]["id"] and len(g["nodes"]) > 1
    b = s.call("browse", params={"limit": 5})
    assert len(b["results"]) == 5 and b["cursor"]
    s.close()
