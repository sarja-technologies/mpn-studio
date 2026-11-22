"""The HTTP API behind the Cognitive cores, Networks and Pairings screens, driven the way the web UI drives it."""
import json
import time

import pytest

import relhost
from fakecore import FakeCore

KEY = "sk-api-SECRET-456"


@pytest.fixture
def client(studio_home, monkeypatch):
    monkeypatch.setenv("MPN_DESKTOP", "1")
    import importlib
    import mpnstudio.api.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    app = appmod.create_app()
    with TestClient(app, client=("127.0.0.1", 5000)) as c:
        yield c
    monkeypatch.delenv("MPN_DESKTOP")
    importlib.reload(appmod)


def wait(c, t, secs=60):
    end = time.time() + secs
    while time.time() < end:
        t = c.get(f"/api/library/tasks/{t['id']}").json()
        if t["state"] != "running":
            return t
        time.sleep(0.05)
    raise AssertionError("task did not finish")


@pytest.fixture
def fake():
    f = FakeCore(key=KEY)
    yield f
    f.close()


def test_core_server_crud_and_the_key_never_comes_back(client, fake):
    r = client.post("/api/core-servers", json={"name": "Hosted", "base_url": fake.url, "model_id": "fake-model", "api_key": KEY})
    assert r.status_code == 200 and r.json()["has_key"] and KEY not in r.text
    cid = r.json()["id"]
    assert KEY not in client.get("/api/core-servers").text
    assert KEY not in client.get("/api/library/overview").text
    r = client.patch(f"/api/core-servers/{cid}", json={"model_id": "other"})
    assert r.json()["model_id"] == "other" and r.json()["has_key"]                       # editing keeps the stored key
    t = client.post(f"/api/core-servers/{cid}/test").json()
    assert t["ok"] and KEY not in json.dumps(t)
    assert client.get("/api/core-servers").json()["servers"][0]["last_test"]["ok"]
    assert client.delete(f"/api/core-servers/{cid}").json() == {"removed": cid}


def test_test_before_saving_uses_the_typed_key_and_explains_failures(client, fake):
    r = client.post("/api/core-servers/test", json={"base_url": fake.url, "model_id": "fake-model"}).json()
    assert r["code"] == "auth"
    r = client.post("/api/core-servers/test", json={"base_url": fake.url, "model_id": "fake-model", "api_key": KEY}).json()
    assert r["ok"]
    saved = client.post("/api/core-servers", json={"name": "S", "base_url": fake.url, "model_id": "fake-model", "api_key": KEY}).json()
    r = client.post("/api/core-servers/test", json={"id": saved["id"], "base_url": fake.url, "model_id": "fake-model"}).json()
    assert r["ok"]                                                                           # editing: the stored key is used when none is typed
    bad = client.post("/api/core-servers", json={"name": "Bad", "base_url": "nope", "model_id": "m"})
    assert bad.status_code == 400 and "http://" in bad.json()["error"]


def test_catalogue_flow_import_install_verify_remove(client, host, studio_home):
    mpn, core = relhost.mpn_release(host), relhost.core_release(host)
    idx = studio_home / "index.json"
    idx.write_text(json.dumps({"schema": "fylume-catalogue/1", "releases": [mpn, core]}))
    cur = client.put("/api/desktop/settings", json={"catalogue": {"url": str(idx)}})
    assert cur.status_code == 200 and cur.json()["catalogue"]["url"] == str(idx)
    r = client.post("/api/catalogue/refresh").json()
    assert r["fetched"] == 2 and r["skipped"] == [] and {x["kind"] for x in r["releases"]} == {"mpn", "core"}
    cat = client.get("/api/catalogue").json()
    by = {x["kind"]: x for x in cat["releases"]}
    assert by["mpn"]["mpn"]["nodes"] == 42 and by["mpn"]["plan"]["enough_space"] and by["mpn"]["compat"]["ok"] and by["mpn"]["installed"] is None
    assert by["core"]["core"]["formats"][0]["bytes"] > 0 and cat["default_url"].startswith("https://") and "fgmpn" in cat
    t = client.post("/api/catalogue/install", json={"kind": "mpn", "id": mpn["id"], "version": "1.0.0"}).json()
    t = wait(client, t)
    assert t["state"] == "done" and t["result"]["store"]["nodes"] == 42, t
    assert t["progress"]["done"] == t["progress"]["total"] > 0
    inst = client.get("/api/releases").json()["installed"]
    assert [x["id"] for x in inst] == [mpn["id"]]
    assert {x["kind"]: bool(x["installed"]) for x in client.get("/api/catalogue").json()["releases"]} == {"mpn": True, "core": False}
    v = wait(client, client.post("/api/releases/verify", json={"kind": "mpn", "id": mpn["id"], "version": "1.0.0"}).json())
    assert v["state"] == "done" and v["result"]["ok"]
    t = wait(client, client.post("/api/catalogue/install", json={"kind": "core", "id": core["id"], "version": "1.0.0"}).json())
    assert t["state"] == "done"
    cores = client.get("/api/library/overview").json()["cores"]
    assert len(cores) == 1 and cores[0]["release"]["id"] == core["id"]
    assert client.delete(f"/api/releases/mpn/{mpn['id']}/1.0.0").status_code == 200
    assert client.get("/api/releases").json()["installed"][0]["kind"] == "core"


def test_install_errors_arrive_as_readable_task_errors(client, host, studio_home):
    mpn = relhost.mpn_release(host)
    host.corrupt.add(host.path("yaob-test-fg", mpn["hosting"]["primary"]["revision"], "segments/seg-000002.fgs"))
    f = studio_home / "m.json"
    f.write_text(json.dumps(mpn))
    assert client.post("/api/catalogue/import", json={"path": str(f)}).json()["added"][0]["id"] == mpn["id"]
    t = wait(client, client.post("/api/catalogue/install", json={"kind": "mpn", "id": mpn["id"], "version": "1.0.0"}).json())
    assert t["state"] == "failed" and "SHA-256" in t["error"] and "seg-000002.fgs" in t["error"]
    assert client.get("/api/releases").json()["installed"] == []
    assert client.post("/api/catalogue/install", json={"kind": "mpn", "id": "nope", "version": "1"}).status_code == 404
    bad = client.post("/api/catalogue/import", json={"path": "relative.json"})
    assert bad.status_code == 400


def test_incompatible_release_is_listed_with_its_reason_and_refused(client, host, studio_home):
    mpn = relhost.mpn_release(host, detail={"studio_min": "7.0.0"})
    f = studio_home / "m.json"
    f.write_text(json.dumps(mpn))
    client.post("/api/catalogue/import", json={"path": str(f)})
    r = client.get("/api/catalogue").json()["releases"][0]
    assert not r["compat"]["ok"] and "7.0.0" in r["compat"]["reasons"][0]
    t = wait(client, client.post("/api/catalogue/install", json={"kind": "mpn", "id": mpn["id"], "version": "1.0.0"}).json())
    assert t["state"] == "failed" and "7.0.0" in t["error"]


def test_a_mpn_file_network_is_verified_and_imported_without_its_core(client, studio_home):
    import test_builder
    from mpnstudio.library import mpnfile
    seed = test_builder.seed(studio_home / "s.sqlite", n=15)
    seed.close()
    dest = studio_home / "m.mpn"
    manifest = {"name": "Old model", "model_id": "old", "core": {"name": "c", "sha256": "0" * 64, "member": "core/model.gguf"}, "network": {"member": "network/network.sqlite", "nodes": 15}}
    mpnfile.write(dest, manifest, [{"name": "core/model.gguf", "kind": "core", "data": b"x" * 5000},
                                  {"name": "network/network.sqlite", "kind": "network", "path": str(studio_home / "s.sqlite")},
                                  {"name": "model.json", "kind": "config", "data": json.dumps(manifest).encode()}])
    t = wait(client, client.post("/api/library/networks/import-mpn", json={"path": str(dest)}).json())
    assert t["state"] == "done", t
    nets = client.get("/api/library/overview").json()["networks"]
    assert nets[0]["name"] == "Old model" and nets[0]["nodes"] == 15 and client.get("/api/library/overview").json()["cores"] == []
    # damaged file: refused, nothing imported
    b = bytearray(dest.read_bytes())
    b[len(b) // 2] ^= 0xFF
    bad = studio_home / "bad.mpn"
    bad.write_bytes(bytes(b))
    t = wait(client, client.post("/api/library/networks/import-mpn", json={"path": str(bad), "name": "Broken"}).json())
    assert t["state"] == "failed" and len(client.get("/api/library/overview").json()["networks"]) == 1


def test_rebind_route_and_pairing_with_a_server_core(client, studio_home):
    from test_core_servers import make_network
    a, b = FakeCore(name="core-A"), FakeCore(name="core-B")
    try:
        ca = client.post("/api/core-servers", json={"name": "A", "base_url": a.url, "model_id": "fake-model"}).json()
        cb = client.post("/api/core-servers", json={"name": "B", "base_url": b.url, "model_id": "fake-model"}).json()
        net = make_network(studio_home)
        m = client.post("/api/library/models", json={"name": "Pairing", "core_id": ca["id_ref"], "network_id": net}).json()
        t = wait(client, client.post(f"/api/library/models/{m['id']}/build", json={}).json(), 120)
        assert t["state"] == "done", t
        assert client.patch(f"/api/library/models/{m['id']}", json={"core_id": cb["id_ref"]}).json()["status"]["state"] == "needs build"
        t = wait(client, client.post(f"/api/library/models/{m['id']}/rebind", json={}).json(), 120)
        assert t["state"] == "done" and t["result"]["status"]["state"] == "ready" and "core-B" in t["result"]["build"]["test"]["answer"]
        r = client.post(f"/api/library/models/{m['id']}/export", json={"path": str(studio_home)}).json()
        assert wait(client, r)["state"] == "failed"
    finally:
        a.close()
        b.close()
