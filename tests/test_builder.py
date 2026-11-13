"""The model builder: bulk node operations (more than 60 nodes, one revision, undo, cancel), network extraction, merge,
compare and statistics, the .mpn single-file format (integrity, corruption, lightweight files), GGUF headers, and the
library HTTP API. Everything runs on small synthetic data in temporary folders; no model weights are needed."""
from __future__ import annotations

import json
import struct
import time

import pytest

from mpnstudio.library import gguf, mpnfile
from mpnstudio.store import netops
from mpnstudio.store.db import Store

N = 400


def seed(path, n=N, prefix="Node") -> Store:
    s = Store(path)
    with s.edit(kind="import", message="seed") as tx:
        a = tx.put_mpn("rivers", "Rivers")
        b = tx.put_mpn("cities", "Cities")
        ids = []
        for i in range(n):
            nid = tx.put_node(title=f"{prefix} {i}", summary=("A river." if i % 2 else "A city.") + f" Number {i}.", origin="reference",
                              cells={"knowledge": {"statements": [{"text": f"{prefix} {i} is {'a river' if i % 2 else 'a city'}."}]}})
            tx.set_membership(nid, a if i % 2 else b)
            ids.append(nid)
        for i in range(1, n):
            tx.put_edge(ids[i - 1], ids[i], "related_to")
    s.ids = ids
    return s


def live(s: Store) -> int:
    return s.q1("SELECT COUNT(*) FROM node_v WHERE rev_to IS NULL AND status='live'")[0]


# ------------------------------------------------------------------------------------------------ bulk delete
def test_bulk_delete_far_more_than_sixty_nodes_in_one_revision(tmp_path):
    s = seed(tmp_path / "a.sqlite")
    rivers = netops.select_ids(s, {"q": "river"})
    assert len(rivers) == N // 2 > 60
    head = s.head()
    seen = []
    r = netops.bulk_delete(s, rivers, progress=lambda d, n: seen.append((d, n)))
    assert r["deleted"] == N // 2 and r["missing"] == 0
    assert s.head() == head + 1 == r["rev"]                 # one revision, not one per node
    assert seen and seen[-1] == (N // 2, N // 2)
    assert live(s) == N // 2
    assert netops.select_ids(s, {"q": "river"}) == []
    assert s.search_lexical("river") == []                   # gone from search at once
    # no relation may point at a deleted node
    assert s.q1("SELECT COUNT(*) FROM edge_v e JOIN node_v n ON n.node_id IN (e.src, e.dst) AND n.rev_to IS NULL "
                "WHERE e.rev_to IS NULL AND n.status='tombstone'")[0] == 0
    # one undo restores every node and relation
    s.revert(r["rev"], kind="undo")
    assert live(s) == N
    assert s.q1("SELECT COUNT(*) FROM edge_v WHERE rev_to IS NULL")[0] == N - 1


def test_bulk_delete_by_mpn_and_twice_is_harmless(tmp_path):
    s = seed(tmp_path / "a.sqlite")
    mpn = s.q1("SELECT mpn_id FROM mpn_v WHERE rev_to IS NULL AND slug='cities'")[0]
    ids = netops.select_ids(s, {"mpn": mpn})
    assert len(ids) == N // 2
    netops.bulk_delete(s, ids)
    again = netops.bulk_delete(s, ids)
    assert again["deleted"] == 0 and again["already"] == N // 2


def test_cancelled_bulk_delete_changes_nothing(tmp_path):
    s = seed(tmp_path / "a.sqlite")
    head = s.head()
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 1
    with pytest.raises(netops.Cancelled):
        netops.bulk_delete(s, netops.select_ids(s, {}), cancelled=cancelled)
    assert s.head() == head and live(s) == N


# ------------------------------------------------------------------------------------------------ networks
def test_extract_merge_compare(tmp_path):
    s = seed(tmp_path / "a.sqlite")
    rivers = netops.select_ids(s, {"q": "river"})
    s.checkpoint_wal()
    out = netops.extract(tmp_path / "a.sqlite", tmp_path / "part.sqlite", rivers, message="rivers only")
    part = Store(tmp_path / "part.sqlite")
    assert live(part) == N // 2
    assert part.q1("SELECT COUNT(*) FROM node_v WHERE rev_to IS NULL AND title LIKE 'Node %' AND summary LIKE 'A city%'")[0] == 0
    assert out
    # merging the part back into a network that has it: everything is a conflict and is skipped
    r = netops.merge_into(s, tmp_path / "part.sqlite")
    assert r["copied"] == 0 and r["skipped"] == N // 2
    # into a different network: copied, with relations among the copied nodes
    other = seed(tmp_path / "b.sqlite", n=10, prefix="Other")
    r = netops.merge_into(other, tmp_path / "part.sqlite")
    assert r["copied"] == N // 2 and live(other) == 10 + N // 2
    part.close()
    other.checkpoint_wal()
    c = netops.compare(tmp_path / "part.sqlite", tmp_path / "b.sqlite")
    assert c["a_nodes"] == N // 2 and c["b_nodes"] == 10 + N // 2
    assert c["added"] >= 10


def test_duplicates_and_stats(tmp_path):
    s = seed(tmp_path / "a.sqlite", n=50)
    with s.edit(message="dup") as tx:
        tx.put_node(title="Node 3", summary="Again.", origin="user", cells={"knowledge": {"statements": []}})
    d = netops.duplicates(s)
    assert d["groups"] == 1 and d["sample"][0]["count"] == 2
    s.checkpoint_wal()
    t0 = time.perf_counter()
    st = netops.stats(tmp_path / "a.sqlite")
    assert time.perf_counter() - t0 < 5
    assert st["nodes"] == 51 and st["relationships"] == 49 and st["invalid_relationships"] == 0
    assert st["orphan_nodes"] == 1                         # the duplicate has no relation
    assert sorted(x["name"] for x in st["domains"]) == ["Cities", "Rivers"]


# ------------------------------------------------------------------------------------------------ .mpn files
def _write(tmp_path, light=False, blob=b"x" * 100_000):
    p = tmp_path / "m.mpn"
    members = [{"name": "network/network.sqlite", "kind": "network", "data": b"net" * 1000},
               {"name": "model.json", "kind": "config", "data": json.dumps({"name": "T"}).encode()}]
    if not light:
        members.insert(0, {"name": "core/model.gguf", "kind": "core", "data": blob})
    mpnfile.write(p, {"name": "T", "model_id": "t", "core": {"name": "Tiny", "sha256": "ab" * 32}}, members, lightweight=light)
    return p


def test_mpn_roundtrip_and_alignment(tmp_path):
    p = _write(tmp_path)
    f = mpnfile.MpnFile(p)
    assert f.manifest["name"] == "T" and not f.lightweight
    assert all(m["offset"] % mpnfile.ALIGN == 0 for m in f.index["members"])
    assert f.read("core/model.gguf") == b"x" * 100_000
    assert f.verify()["ok"]
    out = f.extract("core/model.gguf", tmp_path / "out" / "core.gguf")
    assert out.read_bytes() == b"x" * 100_000
    assert f.summary()["mode"] == "complete"


def test_mpn_corruption_is_detected(tmp_path):
    p = _write(tmp_path)
    f = mpnfile.MpnFile(p)
    off = f.member("core/model.gguf")["offset"] + 10
    b = bytearray(p.read_bytes())
    b[off] ^= 0xFF
    p.write_bytes(bytes(b))
    f = mpnfile.MpnFile(p)                                   # the index is intact
    v = f.verify()
    assert not v["ok"] and [m["name"] for m in v["members"] if not m["ok"]] == ["core/model.gguf"]
    with pytest.raises(mpnfile.MpnFileError):
        f.extract("core/model.gguf", tmp_path / "bad.gguf")
    assert not (tmp_path / "bad.gguf").exists()


def test_mpn_damaged_index_and_wrong_files(tmp_path):
    p = _write(tmp_path)
    b = bytearray(p.read_bytes())
    b[-5] ^= 0xFF                                            # inside the JSON index
    p.write_bytes(bytes(b))
    with pytest.raises(mpnfile.MpnFileError, match="damaged"):
        mpnfile.MpnFile(p)
    p.write_bytes(p.read_bytes()[: len(b) // 2])             # truncated
    with pytest.raises(mpnfile.MpnFileError):
        mpnfile.MpnFile(p)
    g = tmp_path / "x.mpn"
    g.write_bytes(b"GGUF" + b"\0" * 100)
    with pytest.raises(mpnfile.MpnFileError, match="GGUF"):
        mpnfile.MpnFile(g)


def _forge(path, names):
    """A well-formed file whose index names the given members (a crafted file, not one MPN Studio writes)."""
    import hashlib
    members = [{"name": n, "kind": "x", "offset": 64, "size": 0, "sha256": hashlib.sha256(b"").hexdigest()} for n in names]
    raw = json.dumps({"format": mpnfile.FORMAT, "manifest": {}, "members": members}).encode()
    hdr = mpnfile.HEADER.pack(mpnfile.MAGIC, 1, 0, 0, 64, len(raw), hashlib.sha256(raw).digest())
    path.write_bytes(hdr + raw)
    return path


@pytest.mark.parametrize("name", ["../evil", "/etc/passwd", "a/../../b", "a\\b", "", "./x", "a//b"])
def test_mpn_member_names_cannot_leave_the_model(tmp_path, name):
    with pytest.raises(mpnfile.MpnFileError, match="outside the model"):
        mpnfile.MpnFile(_forge(tmp_path / "bad.mpn", [name]))
    with pytest.raises(mpnfile.MpnFileError):
        mpnfile.write(tmp_path / "w.mpn", {}, [{"name": name, "kind": "x", "data": b"1"}])


def test_mpn_repeated_member_is_refused(tmp_path):
    with pytest.raises(mpnfile.MpnFileError, match="repeats"):
        mpnfile.MpnFile(_forge(tmp_path / "dup.mpn", ["a", "a"]))


def test_lightweight_file_has_no_core(tmp_path):
    p = _write(tmp_path, light=True)
    f = mpnfile.MpnFile(p)
    assert f.lightweight and "core/model.gguf" not in f.members
    with pytest.raises(mpnfile.MpnFileError):
        f.member("core/model.gguf")


def test_lightweight_run_refuses_without_the_core(tmp_path, monkeypatch):
    for k in ("DATA", "CACHE", "CONFIG", "STATE"):
        monkeypatch.setenv(f"MPN_STUDIO_{k}_DIR", str(tmp_path / k.lower()))
    from mpnstudio.library import runner
    p = _write(tmp_path, light=True)
    with pytest.raises(runner.RunError, match="core"):
        runner.prepare_mpn(p)


# ------------------------------------------------------------------------------------------------ GGUF headers
def _gguf(path, arch="llama", extra=None):
    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    kv = [("general.architecture", 8, s(arch)), ("general.name", 8, s("Tiny")), ("general.file_type", 4, struct.pack("<I", 7)),
          (f"{arch}.context_length", 4, struct.pack("<I", 4096)), ("tokenizer.chat_template", 8, s("{{x}}"))] + (extra or [])
    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 2) + struct.pack("<Q", len(kv))
    for k, t, v in kv:
        body += s(k) + struct.pack("<I", t) + v
    for name, shape in (("tok_embd.weight", (64, 32)), ("output.weight", (64, 32))):
        body += s(name) + struct.pack("<I", len(shape)) + b"".join(struct.pack("<Q", d) for d in shape) + struct.pack("<I", 8) + struct.pack("<Q", 0)
    path.write_bytes(body + b"\0" * 64)
    return path


def test_gguf_header_is_read_without_loading_weights(tmp_path):
    d = gguf.describe(_gguf(tmp_path / "t.gguf"))
    assert d["architecture"] == "llama" and d["kind"] == "core" and d["parameters"] == 2 * 64 * 32
    assert d["quantization"] == "Q8_0" and d["context_length"] == 4096 and d["chat_template"]
    e = gguf.describe(_gguf(tmp_path / "e.gguf", arch="bert", extra=[("bert.pooling_type", 4, struct.pack("<I", 1))]))
    assert e["kind"] == "encoder"


def test_gguf_rejects_other_files(tmp_path):
    p = tmp_path / "x.gguf"
    p.write_bytes(b"PK\x03\x04" + b"\0" * 40)
    with pytest.raises(gguf.GGUFError):
        gguf.describe(p)
    t = _gguf(tmp_path / "t.gguf")
    t.write_bytes(t.read_bytes()[:30])
    with pytest.raises(gguf.GGUFError):
        gguf.describe(t)


def test_core_compatibility_from_header(tmp_path):
    from mpnstudio.library import cores
    ok = cores.compatibility(gguf.describe(_gguf(tmp_path / "t.gguf")))
    assert ok["runnable"]
    bad = cores.compatibility(gguf.describe(_gguf(tmp_path / "u.gguf", arch="not-a-real-architecture")))
    assert not bad["runnable"] and "not known" in bad["notes"][0]


# ------------------------------------------------------------------------------------------------ HTTP API
@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("DATA", "CACHE", "CONFIG", "STATE"):
        monkeypatch.setenv(f"MPN_STUDIO_{k}_DIR", str(tmp_path / k.lower()))
    monkeypatch.setenv("MPN_DESKTOP", "1")
    import importlib
    import mpnstudio.api.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    from mpnstudio.desktop import projects
    seed(tmp_path / "seed.sqlite").close()
    projects.import_store_file(tmp_path / "seed.sqlite", "Seed")
    app = appmod.create_app()
    app.state.tmp = tmp_path
    with TestClient(app, client=("127.0.0.1", 5000)) as c:
        yield c
    monkeypatch.delenv("MPN_DESKTOP")
    importlib.reload(appmod)


def _wait(c, t):
    for _ in range(200):
        t = c.get(f"/api/library/tasks/{t['id']}").json()
        if t["state"] != "running":
            return t
        time.sleep(0.05)
    raise AssertionError("task did not finish")


def test_api_library_works_without_an_open_network(client):
    ov = client.get("/api/library/overview").json()
    assert [n["name"] for n in ov["networks"]] == ["Seed"] and ov["networks"][0]["nodes"] == N
    assert client.post("/api/nodes/select", json={}).status_code == 409


def test_api_bulk_delete_by_filter_and_undo(client):
    slug = client.get("/api/library/networks").json()["networks"][0]["id"]
    t = _wait(client, client.post(f"/api/library/networks/{slug}/duplicate", json={"name": "Copy"}).json())
    assert t["state"] == "done"
    copy = t["result"]["id"]
    client.post(f"/api/library/networks/{copy}/open")
    p = client.post("/api/nodes/bulk-preview", json={"filter": {"q": "river"}}).json()
    assert p["count"] == p["live"] == N // 2 and p["relations"] > 0
    t = _wait(client, client.post("/api/nodes/bulk-delete", json={"filter": {"q": "river"}}).json())
    assert t["state"] == "done" and t["result"]["deleted"] == N // 2
    assert client.post("/api/nodes/select", json={"filter": {"q": "river"}}).json()["count"] == 0
    client.post("/api/nodes/bulk-restore", json={"rev": t["result"]["rev"]})
    assert client.post("/api/nodes/select", json={"filter": {"q": "river"}}).json()["count"] == N // 2
    # the original network was never touched
    assert client.get(f"/api/library/networks/{slug}").json()["nodes"] == N
    assert client.get(f"/api/library/networks/{slug}/compare/{copy}").json()["unchanged"] == N


def test_api_specialise_creates_a_smaller_network(client):
    slug = client.get("/api/library/networks").json()["networks"][0]["id"]
    t = _wait(client, client.post(f"/api/library/networks/{slug}/specialise", json={"name": "Cities", "filter": {"q": "city"}}).json())
    assert t["state"] == "done" and t["result"]["nodes"] == N // 2
    st = client.get(f"/api/library/networks/{t['result']['id']}/stats").json()
    assert st["nodes"] == N // 2 and st["invalid_relationships"] == 0


def test_api_model_needs_core_and_network(client):
    m = client.post("/api/library/models", json={"name": "Test model"}).json()
    assert m["status"]["state"] == "incomplete"
    t = _wait(client, client.post(f"/api/library/models/{m['id']}/export", json={"path": str(client.app.state.tmp), "mode": "complete"}).json())
    assert t["state"] == "failed" and "Build the model" in t["error"]
    r = client.post("/api/library/bench", json={"models": [m["id"]], "questions": [{"question": "q", "expect": ["a"]}]})
    assert r.status_code == 409


def test_engine_uses_the_running_apps_runtime_not_a_recorded_path(tmp_path, monkeypatch):
    """A model built or opened under one install (or one AppImage mount) must still run after the app moves."""
    from mpnstudio.engine.engine import Engine
    live = tmp_path / "app" / "runtime"
    (live / "llama" / "cpu").mkdir(parents=True)
    (live / "llama" / "cpu" / "llama-server").write_text("")
    root = tmp_path / "model"
    root.mkdir()
    (root / "MANIFEST.json").write_text(json.dumps({"paths": {"runtime": str(tmp_path / "gone" / "llama")}}))
    monkeypatch.setenv("MPN_RUNTIME_DIR", str(live))
    assert Engine(root, root / "data").p("runtime") == live / "llama"
    monkeypatch.setenv("MPN_RUNTIME_DIR", str(tmp_path / "empty"))      # no usable live runtime: the recorded path is kept
    assert Engine(root, root / "data").p("runtime") == tmp_path / "gone" / "llama"
