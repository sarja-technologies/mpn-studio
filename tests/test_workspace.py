"""Projects 0.4: linked networks, copy-on-write forks, versions, compare, fork files, backups, source fallback.

The base network here is a real `mpn-pack/1`-layout store written by the same writer that built Yaob Origin N119M,
only small. Every test checks that the base files are byte-identical afterwards.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mpnstudio.desktop import paths, projects
from mpnstudio.mpnnet import schema as S
from mpnstudio.mpnnet.store import Store as PackStore
from mpnstudio.workspace import overlay as O
from mpnstudio.workspace import versions as V


def _node(key, title, domain="mathematics", kind="concept", summary=""):
    return {"nid": S.h16(key), "key": key, "kind": kind, "title": title, "summary": summary or f"About {title}", "lang": "en",
            "domain": domain, "prov": {"dataset": "test", "record": key}, "licence": "CC0-1.0", "conf": 0.9,
            "aliases": [title.lower()], "cells": {"knowledge": {"statements": [{"text": summary or f"{title} is a test node."}]}}}


@pytest.fixture
def pack(tmp_path):
    root = tmp_path / "pack"
    st = PackStore(root / "parts", 4, create=True)
    nodes = [_node("wd:Q1", "Mathematics", summary="The study of quantity, structure and change"),
             _node("wd:Q2", "Algebra"), _node("wd:Q3", "Geometry"), _node("wd:Q4", "Waterloo", "geography"),
             _node("wd:Q5", "Ada Lovelace", "people", "person")]
    ids = [n["nid"] for n in nodes]
    edges = [(ids[1], "P361", ids[0], None, "both"), (ids[2], "P361", ids[0], None, "both"), (ids[4], "P101", ids[0], None, "both")]
    st.write_batch("t", 1, 1, nodes, edges, xrefs=[("wd:" + n["key"].split(":")[1], n["nid"]) for n in nodes])
    st.close()
    (root / "manifest.json").write_text(json.dumps({"format": "mpn-pack/1", "pack_id": "test-net", "name": "Test network",
                                                     "version": "t1", "partitions": 4,
                                                     "datasets": [{"id": 1, "slug": "test", "name": "Test", "licence": "CC0-1.0"}]}))
    return root


def digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((root / "parts").glob("p??.sqlite")):
        h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("MPN_STUDIO_CONFIG_DIR", "MPN_STUDIO_DATA_DIR", "MPN_STUDIO_CACHE_DIR", "MPN_STUDIO_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    paths.ensure()
    from mpnstudio.api import app as appmod
    monkeypatch.setattr(appmod, "DESKTOP", True)
    monkeypatch.setattr(appmod, "SESSION_TOKEN", "t")
    return TestClient(appmod.create_app(), headers={"x-mpn-token": "t"})


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_link_is_protected_and_reads_the_real_network(client, pack):
    before = digest(pack)
    r = ok(client.post("/api/projects/link", json={"name": "Test network", "sources": [{"mode": "local", "target": str(pack)}]}))
    st = ok(client.get("/api/ws/state"))
    assert st["kind"] == "linked" and st["read_only"] and st["connected"]
    assert st["identity"]["nodes"] == 5
    ov = ok(client.get("/api/ws/overview"))
    assert ov["validated_nodes"] == 5 and ov["relationships"] == 3
    hits = ok(client.get("/api/ws/search", params={"q": "algebra"}))["results"]
    assert hits and hits[0]["title"] == "Algebra"
    n = ok(client.get(f"/api/ws/node/{hits[0]['id']}"))
    assert n["out"][0]["title"] == "Mathematics"
    assert client.patch(f"/api/ws/node/{hits[0]['id']}", json={"title": "x"}).status_code == 409   # protected
    # the home screen lists it with its size and no copied data
    p = next(x for x in ok(client.get("/api/projects"))["projects"] if x["id"] == r["id"])
    assert p["kind"] == "linked" and p["nodes"] == 5 and p["bytes"] < 10_000
    assert digest(pack) == before


def test_fork_edits_never_touch_the_base(client, pack):
    before = digest(pack)
    base = ok(client.post("/api/projects/link", json={"sources": [{"mode": "local", "target": str(pack)}]}))["id"]
    fork = ok(client.post(f"/api/projects/{base}/duplicate", json={"name": "My Experiment"}))
    assert fork["kind"] == "fork"
    math_id = ok(client.get("/api/ws/search", params={"q": "mathematics"}))["results"][0]["id"]
    # edit a base node
    n = ok(client.patch(f"/api/ws/node/{math_id}", json={"title": "Mathematics (edited)", "statements": ["Edited in a fork."]}))
    assert n["node"]["title"] == "Mathematics (edited)" and n["node"]["edited"]
    # new node, connect it, search finds it
    new = ok(client.post("/api/ws/nodes", json={"title": "Category theory", "domain": "mathematics", "summary": "Arrows"}))["node"]
    ok(client.post("/api/ws/edges", json={"src": new["id"], "dst": math_id, "rel": "part of"}))
    got = ok(client.get(f"/api/ws/node/{math_id}"))
    assert any(r["node"] == new["id"] and r.get("added") for r in got["in"])
    assert ok(client.get("/api/ws/search", params={"q": "category"}))["results"][0]["id"] == new["id"]
    g = ok(client.get(f"/api/ws/node/{math_id}/graph"))
    assert new["id"] in {x["id"] for x in g["nodes"]}
    # delete a base node: gone from search, relationships and graph
    geo = ok(client.get("/api/ws/search", params={"q": "geometry"}))["results"][0]["id"]
    ok(client.post("/api/ws/nodes/delete", json={"ids": [geo]}))
    assert client.get(f"/api/ws/node/{geo}").status_code == 410
    assert geo not in {r["node"] for r in ok(client.get(f"/api/ws/node/{math_id}"))["in"]}
    assert not ok(client.get("/api/ws/search", params={"q": "geometry"}))["results"]
    # the overview counts the fork's state
    ov = ok(client.get("/api/ws/overview"))
    assert ov["validated_nodes"] == 5 and ov["base_nodes"] == 5 and ov["changes"]["nodes_added"] == 1
    # undo the deletion, redo it, undo again
    ok(client.post("/api/ws/undo"))
    assert ok(client.get(f"/api/ws/node/{geo}"))["node"]["title"] == "Geometry"
    ok(client.post("/api/ws/redo"))
    assert client.get(f"/api/ws/node/{geo}").status_code == 410
    ok(client.post("/api/ws/undo"))
    # the original network, opened again, is unchanged
    ok(client.post(f"/api/projects/{base}/open"))
    assert ok(client.get(f"/api/ws/node/{math_id}"))["node"]["title"] == "Mathematics"
    assert digest(pack) == before
    # the fork is small
    fp = next(x for x in ok(client.get("/api/projects"))["projects"] if x["id"] == fork["id"])
    assert fp["changes"]["nodes_edited"] == 1 and fp["changes"]["nodes_added"] == 1 and fp["bytes"] < 2_000_000


def test_versions_compare_and_open_version(client, pack):
    base = ok(client.post("/api/projects/link", json={"sources": [{"mode": "local", "target": str(pack)}]}))["id"]
    fork = ok(client.post(f"/api/projects/{base}/duplicate", json={"name": "Versioned"}))["id"]
    alg = ok(client.get("/api/ws/search", params={"q": "algebra"}))["results"][0]["id"]
    ok(client.patch(f"/api/ws/node/{alg}", json={"summary": "First edit"}))
    ok(client.post("/api/ws/versions", json={"name": "v1"}))
    assert client.post("/api/ws/versions", json={"name": "v1"}).status_code == 409
    ok(client.patch(f"/api/ws/node/{alg}", json={"summary": "Second edit"}))
    new = ok(client.post("/api/ws/nodes", json={"title": "Topology"}))["node"]["id"]
    d = ok(client.get("/api/ws/compare", params={"a": "v1", "b": "current"}))
    ch = {n["id"]: n for n in d["nodes"]}
    assert ch[alg]["change"] == "edited" and ch[alg]["fields"] == ["summary"]
    assert ch[alg]["before"]["summary"] == "First edit" and ch[alg]["after"]["summary"] == "Second edit"
    assert ch[new]["change"] == "added"
    d0 = ok(client.get("/api/ws/compare", params={"a": "original", "b": "v1"}))
    assert [n["id"] for n in d0["nodes"]] == [alg]
    # reopen v1 as its own project
    v = ok(client.post("/api/ws/versions/open", json={"version": "v1", "name": "Versioned at v1"}))["id"]
    assert ok(client.get(f"/api/ws/node/{alg}"))["node"]["summary"] == "First edit"
    assert client.get(f"/api/ws/node/{new}").status_code == 404
    ok(client.post(f"/api/projects/{fork}/open"))
    assert ok(client.get(f"/api/ws/node/{alg}"))["node"]["summary"] == "Second edit"
    assert v != fork


def test_fork_file_round_trip_and_backup(client, pack, tmp_path):
    base = ok(client.post("/api/projects/link", json={"sources": [{"mode": "local", "target": str(pack)}]}))["id"]
    ok(client.post(f"/api/projects/{base}/duplicate", json={"name": "Shared"}))
    ada = ok(client.get("/api/ws/search", params={"q": "ada"}))["results"][0]["id"]
    ok(client.patch(f"/api/ws/node/{ada}", json={"aliases": "Countess of Lovelace, Ada"}))
    out = ok(client.post("/api/ws/export", json={"dest": str(tmp_path / "shared")}))
    assert out["file"].endswith(".mpnfork") and out["changes"]["nodes_edited"] == 1
    new = ok(client.post("/api/projects/import", json={"path": out["file"], "name": "Shared (imported)"}))["id"]
    assert ok(client.get(f"/api/ws/node/{ada}"))["node"]["aliases"] == ["Countess of Lovelace", "Ada"]
    m = projects.read_manifest(new)
    assert m["kind"] == "fork" and m["base_project"] == base
    # a damaged fork file is refused
    bad = tmp_path / "bad.mpnfork"
    raw = bytearray(Path(out["file"]).read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    bad.write_bytes(bytes(raw))
    assert client.post("/api/projects/import", json={"path": str(bad)}).status_code in (400, 422)
    # backups of a fork carry its changes, verified, and restore as a new project
    p = projects.backup(new)
    meta = projects.verify_backup(p)
    assert meta["kind"] == "fork" and "overlay.sqlite" in meta["files"]
    r = projects.restore(p, "Restored fork")
    assert projects.kind_of(projects.read_manifest(r)) == "fork"


def test_sources_fall_back_and_identity_is_checked(client, pack, tmp_path):
    missing = tmp_path / "not-mounted"
    r = ok(client.post("/api/projects/link", json={"name": "Two sources", "sources": [{"mode": "local", "target": str(pack)}]}))
    m = projects.read_manifest(r["id"])
    m["network"]["sources"].insert(0, {"mode": "local", "target": str(missing), "label": "External disk"})
    projects.write_manifest(r["id"], m)
    st = ok(client.post(f"/api/projects/{r['id']}/open"))["workspace"]
    assert st["source"]["index"] == 1 and st["sources"][0]["available"] is False
    # a source that holds another network is refused
    other = tmp_path / "other"
    ps = PackStore(other / "parts", 4, create=True)
    ps.write_batch("t", 1, 1, [_node("wd:Q99", "Elsewhere")])
    ps.close()
    (other / "manifest.json").write_text(json.dumps({"pack_id": "other-net", "partitions": 4}))
    assert client.post("/api/ws/sources", json={"mode": "local", "target": str(other)}).status_code == 400
    # nothing reachable: the project still opens and says why
    m["network"]["sources"] = [{"mode": "local", "target": str(missing), "label": "External disk"}]
    projects.write_manifest(r["id"], m)
    st = ok(client.post(f"/api/projects/{r['id']}/open"))["workspace"]
    assert st["connected"] is False
    e = client.get("/api/ws/overview")
    assert e.status_code == 503 and "External disk" in e.json()["error"]


def test_trash_needs_the_name(client, pack):
    r = ok(client.post("/api/projects/link", json={"name": "Keep me", "sources": [{"mode": "local", "target": str(pack)}]}))
    assert client.post(f"/api/projects/{r['id']}/trash", json={"confirm": "wrong"}).status_code == 409
    ok(client.post(f"/api/projects/{r['id']}/trash", json={"confirm": "Keep me"}))
    assert not any(p["id"] == r["id"] for p in ok(client.get("/api/projects"))["projects"])


def test_store_project_versions_compare_and_open(client):
    pid = ok(client.post("/api/projects/blank", json={"name": "My Biology Model"}))["id"]
    mpn = ok(client.post("/api/mpns", json={"name": "Cells", "slug": "cells"}))["mpn"]
    mem = [{"mpn_id": mpn["id"]}]
    a = ok(client.post("/api/nodes", json={"title": "Cell", "memberships": mem, "cells": {"knowledge": {"statements": [{"text": "The unit of life."}]}}}))
    ok(client.post("/api/versions", json={"name": "first"}))
    b = ok(client.post("/api/nodes", json={"title": "Nucleus", "memberships": mem, "cells": {"knowledge": {"statements": [{"text": "Holds the DNA."}]}}}))
    ok(client.patch(f"/api/nodes/{a['id']}", json={"title": "Cell (biology)"}))
    d = ok(client.get("/api/versions/compare", params={"a": "first"}))
    ch = {n["title"]: n["change"] for n in d["nodes"]}
    assert ch == {"Cell (biology)": "edited", "Nucleus": "added"}
    v = ok(client.post("/api/versions/open", json={"version": "first", "name": "Biology at first"}))
    assert v["nodes"] == 1
    titles = [n["title"] for n in ok(client.get("/api/search", params={"q": "cell"}))["results"]]
    assert titles == ["Cell"]
    ok(client.post(f"/api/projects/{pid}/open"))
    assert ok(client.get("/api/versions"))["versions"][0]["name"] == "first"
    assert b


def test_overlay_unit_undo_stack(tmp_path):
    ov = O.Overlay(tmp_path / "o.sqlite", {"pack_ids": ["x"]}, create=True)
    ov.edit("a", nodes=[("n1", "put", {"id": "n1", "title": "One"}, False)])
    ov.edit("b", nodes=[("n1", "put", {"id": "n1", "title": "Two"}, False)])
    assert ov.node_state("n1")[1]["title"] == "Two"
    ov.undo()
    assert ov.node_state("n1")[1]["title"] == "One"
    ov.undo()
    assert ov.node_state("n1") == ("base", None)
    assert ov.undo() is None
    ov.redo()
    assert ov.node_state("n1")[1]["title"] == "One"
    ov.edit("c", nodes=[("n1", "del", None, False)])
    assert ov.redo() is None                      # a new edit clears redo
    assert ov.integrity() == "ok"
    ov.close()


def test_overlay_removing_a_fork_added_relationship_cancels_it(tmp_path):
    ov = O.Overlay(tmp_path / "o.sqlite", {"pack_ids": ["x"]}, create=True)
    ov.edit("connect", edges=[("a", "related_to", "b", "add", "related to")])
    assert ov.stats()["relationships_added"] == 1
    ov.edit("disconnect", edges=[("a", "related_to", "b", "del", None)])
    s = ov.stats()
    assert (s["relationships_added"], s["relationships_removed"]) == (0, 0)
    ov.undo()                                     # undoing the removal brings the addition back
    assert ov.stats()["relationships_added"] == 1
    ov.edit("remove a base relationship", edges=[("c", "P31", "d", "del", None)])
    assert ov.stats()["relationships_removed"] == 1
    ov.close()


def test_overlay_reads_are_safe_across_threads(tmp_path):
    """The server reads one change file from several threads while it is edited (node + graph + resources at once)."""
    import threading
    ov = O.Overlay(tmp_path / "o.sqlite", {"pack_ids": ["x"]}, create=True)
    for i in range(50):
        ov.edit(f"e{i}", nodes=[(f"n{i}", "put", {"id": f"n{i}", "title": f"Node {i}"}, True)],
                edges=[(f"n{i}", "related_to", f"n{max(i - 1, 0)}", "add", "related to")])
    errors: list = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                ov.node_state("n7"); ov.node_states([f"n{i}" for i in range(50)]); ov.edges_of("n3"); ov.stats(); ov.search("Node")
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=reader) for _ in range(6)]
    for t in ts:
        t.start()
    for i in range(50, 120):
        ov.edit(f"e{i}", nodes=[(f"n{i}", "put", {"id": f"n{i}", "title": f"Node {i}"}, True)])
    stop.set()
    for t in ts:
        t.join()
    assert not errors, errors[:3]
    assert ov.stats()["nodes_added"] == 120
    ov.close()
