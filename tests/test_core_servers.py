"""Cores on servers Studio does not run: configuration, secrets, Test connection, the engine talking to them, and a pairing
that changes core without touching its network."""
import hashlib
import json
import os
import socket
import stat

import pytest

from fakecore import FakeCore
from mpnstudio.engine.extcore import ExternalCore
from mpnstudio.library import coreconf, models
from mpnstudio.library.coreconf import CoreServerError

KEY = "sk-test-SECRET-123"


@pytest.fixture
def fake():
    made = []

    def make(**kw):
        f = FakeCore(**kw)
        made.append(f)
        return f
    yield make
    for f in made:
        f.close()


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------------------------------------ configuration and secrets
def test_save_list_get_and_url_normalisation(studio_home):
    c = coreconf.save({"name": "Local llama", "base_url": "http://127.0.0.1:8080", "model_id": "qwen", "context_tokens": "8192", "max_output_tokens": 1024})
    assert c["id"] == "local-llama" and c["base_url"] == "http://127.0.0.1:8080/v1" and c["context_tokens"] == 8192 and c["id_ref"] == "endpoint:local-llama"
    assert coreconf.get(c["id"])["timeout_s"] == 120
    again = coreconf.save({"name": "Local llama", "base_url": "http://127.0.0.1:8081/v1/", "model_id": "q2"})
    assert again["id"] == "local-llama-2" and again["base_url"] == "http://127.0.0.1:8081/v1"
    assert [x["id"] for x in coreconf.list_servers()] == ["local-llama-2", "local-llama"]


@pytest.mark.parametrize("body,words", [
    ({"name": "", "base_url": "http://x", "model_id": "m"}, "name"),
    ({"name": "a", "base_url": "ftp://x", "model_id": "m"}, "http://"),
    ({"name": "a", "base_url": "not a url", "model_id": "m"}, "http://"),
    ({"name": "a", "base_url": "http://u:p@x/v1", "model_id": "m"}, "user name"),
    ({"name": "a", "base_url": "http://x", "model_id": ""}, "model id"),
    ({"name": "a", "base_url": "http://x", "model_id": "m", "context_tokens": "lots"}, "whole number"),
    ({"name": "a", "base_url": "http://x", "model_id": "m", "context_tokens": 10}, "between"),
    ({"name": "a", "base_url": "http://x", "model_id": "m", "api_key_env": "bad name"}, "environment variable"),
])
def test_validation_messages(studio_home, body, words):
    with pytest.raises(CoreServerError) as e:
        coreconf.save(body)
    assert words in str(e.value)


def test_api_key_is_stored_apart_and_never_returned(studio_home):
    c = coreconf.save({"name": "Hosted", "base_url": "https://api.example.test/v1", "model_id": "m", "api_key": KEY})
    assert c["has_key"] and c["key_source"] == "saved in Studio" and KEY not in json.dumps(c) and "api_key" not in c
    assert KEY not in json.dumps(coreconf.list_servers())
    on_disk = "".join(p.read_text() for p in (studio_home / "data" / "core-servers").glob("*.json"))
    assert KEY not in on_disk
    sec = studio_home / "config" / "secrets.json"
    assert KEY in sec.read_text() and stat.S_IMODE(sec.stat().st_mode) == 0o600
    assert coreconf.api_key(c["id"]) == KEY
    coreconf.save({"name": "Hosted", "base_url": "https://api.example.test/v1", "model_id": "m2"}, c["id"])          # an edit without a key keeps it
    assert coreconf.api_key(c["id"]) == KEY
    coreconf.save({"name": "Hosted", "base_url": "https://api.example.test/v1", "model_id": "m2", "api_key": ""}, c["id"])    # empty removes it
    assert coreconf.api_key(c["id"]) is None and not coreconf.get(c["id"])["has_key"]


def test_key_can_come_from_an_environment_variable_instead(studio_home, monkeypatch):
    c = coreconf.save({"name": "Env", "base_url": "http://x:1/v1", "model_id": "m", "api_key_env": "MY_CORE_KEY"})
    assert not c["has_key"]
    monkeypatch.setenv("MY_CORE_KEY", KEY)
    assert coreconf.api_key(c["id"]) == KEY and coreconf.get(c["id"])["key_source"] == "environment variable MY_CORE_KEY"
    assert not (studio_home / "config" / "secrets.json").exists()


def test_remove_is_blocked_while_a_pairing_uses_it_and_deletes_the_key(studio_home, fake):
    c = coreconf.save({"name": "Used", "base_url": "http://127.0.0.1:1/v1", "model_id": "m", "api_key": KEY})
    from test_builder import seed
    from mpnstudio.desktop import projects
    seed(studio_home / "s.sqlite", n=5).close()
    slug = projects.import_store_file(studio_home / "s.sqlite", "Net")
    m = models.create("P", "", c["id_ref"], slug)
    with pytest.raises(CoreServerError) as e:
        coreconf.remove(c["id"])
    assert "uses this core" in str(e.value) and e.value.status == 409
    models.update(m["id"], {"core_id": None})
    coreconf.remove(c["id"])
    assert coreconf.list_servers() == [] and KEY not in (studio_home / "config" / "secrets.json").read_text()


# ------------------------------------------------------------------------------------------------ Test connection
def test_connection_ok_reports_models_reply_and_context(studio_home, fake):
    f = fake(n_ctx=16384)
    r = coreconf.test(f.url, "fake-model")
    assert r["ok"] and r["code"] is None and r["models"] == ["fake-model"] and "ready" in r["reply"] and r["context_tokens"] == 16384 and r["latency_ms"] is not None


def test_connection_unreachable(studio_home):
    r = coreconf.test(f"http://127.0.0.1:{closed_port()}/v1", "m")
    assert not r["ok"] and r["code"] == "unreachable" and "Nothing is listening" in r["message"]
    r = coreconf.test("http://no-such-host.invalid/v1", "m")
    assert r["code"] == "unreachable" and "could not be found" in r["message"]


def test_connection_auth_missing_wrong_and_right(studio_home, fake):
    f = fake(key=KEY)
    r = coreconf.test(f.url, "fake-model")
    assert r["code"] == "auth" and "needs an API key" in r["message"]
    r = coreconf.test(f.url, "fake-model", "wrong-key-xyz")
    assert r["code"] == "auth" and "rejected the API key" in r["message"] and "wrong-key-xyz" not in json.dumps(r)
    assert coreconf.test(f.url, "fake-model", KEY)["ok"]


def test_connection_wrong_model_id_lists_what_the_server_has(studio_home, fake):
    f = fake(strict_ids=True)
    r = coreconf.test(f.url, "typo-model")
    assert r["code"] == "model_not_found" and "typo-model" in r["message"] and "fake-model" in r["message"]


def test_connection_single_model_server_warns_but_works(studio_home, fake):
    r = coreconf.test(fake().url, "other-name")                       # llama.cpp answers with its one model whatever the id
    assert r["ok"] and "regardless" in " ".join(r["warnings"])


def test_connection_wrong_path_says_where_the_api_should_be(studio_home, fake):
    f = fake()
    r = coreconf.test(f.url.replace("/v1", "/wrong/v1"), "fake-model")
    assert r["code"] == "bad_url" and "/chat/completions" in r["message"]


def test_connection_timeout(studio_home, fake):
    f = fake(delay=2.5)
    r = coreconf.test(f.url, "fake-model", timeout=1)
    assert r["code"] == "timeout" and "did not answer within 1 s" in r["message"]


def test_connection_server_that_refuses_the_question(studio_home, fake):
    r = coreconf.test(fake(context_error=True).url, "fake-model")
    assert r["code"] == "request_rejected" and "context" in r["message"]


def test_connection_not_an_openai_server(studio_home):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            b = b"<html>hello</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        r = coreconf.test(f"http://127.0.0.1:{s.server_address[1]}/v1", "m")
        assert r["code"] == "not_openai"
    finally:
        s.shutdown()


def test_test_saved_records_the_result_without_the_key(studio_home, fake):
    f = fake(key=KEY)
    c = coreconf.save({"name": "K", "base_url": f.url, "model_id": "fake-model", "api_key": KEY})
    assert coreconf.test_saved(c["id"])["ok"]
    assert coreconf.get(c["id"])["last_test"]["ok"] and KEY not in json.dumps(coreconf.get(c["id"]))
    f.key = "rotated"
    assert coreconf.test_saved(c["id"])["code"] == "auth" and coreconf.get(c["id"])["last_test"]["code"] == "auth"


# ------------------------------------------------------------------------------------------------ the engine's view of a server core
def conf(f, **kw):
    return {"base_url": f.url, "model_id": "fake-model", **kw}


def test_external_core_streams_and_never_sends_llama_only_fields(studio_home, fake):
    f = fake()
    c = ExternalCore(conf(f), None)
    c.start()
    out = "".join(d["choices"][0]["delta"].get("content", "") for d in c.stream("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 50, "cache_prompt": True,
                                                                                                        "timings_per_token": False, "top_k": 20, "stream_options": {"include_usage": True}}) if d.get("choices"))
    assert out.strip() == "core-A: ready"
    sent = f.requests[-1]["body"]
    assert "cache_prompt" not in sent and "timings_per_token" not in sent and sent["model"] == "fake-model" and sent["top_k"] == 20 and sent["stream"] is True


def test_external_core_falls_back_to_plain_openai_fields_when_extras_are_rejected(studio_home, fake):
    f = fake(reject_extras=True)
    c = ExternalCore(conf(f), None)
    c.start()
    r = c.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5, "top_k": 20, "chat_template_kwargs": {"enable_thinking": False}, "temperature": 0.2})
    assert r["choices"][0]["message"]["content"].startswith("core-A")
    assert c.strict and "top_k" not in f.requests[-1]["body"] and f.requests[-1]["body"]["temperature"] == 0.2


def test_external_core_caps_output_and_sends_the_key_only_as_a_header(studio_home, fake):
    f = fake(key=KEY)
    c = ExternalCore(conf(f, max_output_tokens=64), KEY)
    c.start()
    c.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}], "max_tokens": 4096})
    rq = f.requests[-1]
    assert rq["body"]["max_tokens"] == 64 and rq["auth"] == f"Bearer {KEY}" and KEY not in json.dumps(rq["body"])


def test_external_core_start_failure_is_a_readable_error(studio_home, fake):
    f = fake(key=KEY)
    with pytest.raises(RuntimeError) as e:
        ExternalCore(conf(f), "nope").start()
    assert "rejected the API key" in str(e.value) and "nope" not in str(e.value)


def test_token_counts_are_estimated_high_without_a_tokenizer_endpoint(studio_home, fake):
    c = ExternalCore(conf(fake()), None)
    text = "word " * 300
    assert len(c.tokenize(text)) >= len(text) / 4 and c._tok == "estimate"
    assert "<|assistant|>" in c.apply_template([{"role": "user", "content": "hi"}])


def test_a_dropped_server_is_reported_not_hung(studio_home, fake):
    f = fake()
    c = ExternalCore(conf(f, timeout_s=5), None)
    c.start()
    f.close()
    from mpnstudio.engine.llama import LlamaError
    with pytest.raises(LlamaError) as e:
        c.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})
    assert e.value.status == 503 and not c.alive()


# ------------------------------------------------------------------------------------------------ pairing: one network, two cores
def make_network(home):
    from mpnstudio.desktop import projects
    from mpnstudio.store.db import Store
    s = Store(home / "net.sqlite")
    with s.edit(kind="import", message="seed") as tx:
        a = tx.put_mpn("rivers", "Rivers")
        for i in range(30):
            nid = tx.put_node(title=f"River {i}", summary=f"River number {i} flows through the valley.", origin="reference",
                              cells={"knowledge": {"statements": [{"text": f"The river {i} flows through the valley of Elmsworth for {i + 50} kilometres."}]}})
            tx.set_membership(nid, a)
    s.close()
    return projects.import_store_file(home / "net.sqlite", "Rivers network")


def sha_of(p):
    """The network's content, not its bytes: opening a SQLite file for reading may flip its journal-mode header byte."""
    import sqlite3
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        h = hashlib.sha256()
        for line in c.iterdump():
            h.update(line.encode())
        return h.hexdigest()
    finally:
        c.close()


def test_one_network_answers_with_core_a_then_core_b_without_rebuilding_it(studio_home, fake):
    from mpnstudio.desktop import projects
    a, b = fake(name="core-A"), fake(name="core-B")
    ca = coreconf.save({"name": "Core A", "base_url": a.url, "model_id": "fake-model"})
    cb = coreconf.save({"name": "Core B", "base_url": b.url, "model_id": "fake-model"})
    net = make_network(studio_home)
    net_before = sha_of(projects.store_path(net))
    m = models.create("Rivers", "", ca["id_ref"], net)
    assert m["status"]["state"] == "needs build"
    log = []
    m = models.build(m["id"], log=log.append)
    assert m["status"]["state"] == "ready" and "core-A" in m["build"]["test"]["answer"]
    report = json.loads((models.model_dir(m["id"]) / "build" / "build.json").read_text())
    assert report["test"]["evidence_nodes"]                                       # the answer came with nodes from the network
    snap = models.model_dir(m["id"]) / "build" / "network.sqlite"
    snap_before = sha_of(snap)
    assert any("Connecting to the cognitive core" in x for x in log)

    m = models.update(m["id"], {"core_id": cb["id_ref"]})
    assert m["status"]["state"] == "needs build" and "network is kept" in m["status"]["problems"][0]
    m = models.rebind(m["id"], log=log.append)
    assert m["status"]["state"] == "ready" and "core-B" in m["build"]["test"]["answer"] and m["build"]["rebound"]
    assert sha_of(snap) == snap_before                                            # the snapshot was not rebuilt
    assert sha_of(projects.store_path(net)) == net_before                          # the installed network was not touched
    assert b.requests and any(r["body"].get("stream") for r in b.requests)
    man = json.loads((models.model_dir(m["id"]) / "build" / "MANIFEST.json").read_text())
    assert man["core_endpoint"]["id"] == "core-b" and man["paths"]["core"] == "none"
    # back to A: also without a rebuild
    m = models.rebind(models.update(m["id"], {"core_id": ca["id_ref"]})["id"])
    assert "core-A" in m["build"]["test"]["answer"] and sha_of(snap) == snap_before


def test_rebind_to_a_core_that_does_not_answer_changes_nothing(studio_home, fake):
    a = fake(name="core-A")
    ca = coreconf.save({"name": "Core A", "base_url": a.url, "model_id": "fake-model"})
    dead = coreconf.save({"name": "Dead", "base_url": f"http://127.0.0.1:{closed_port()}/v1", "model_id": "m"})
    net = make_network(studio_home)
    m = models.build(models.create("Rivers", "", ca["id_ref"], net)["id"])
    man_path = models.model_dir(m["id"]) / "build" / "MANIFEST.json"
    before = man_path.read_text()
    models.update(m["id"], {"core_id": dead["id_ref"]})
    with pytest.raises(RuntimeError) as e:
        models.rebind(m["id"])
    assert "did not accept the test question" in str(e.value) or "could not be started" in str(e.value)
    assert man_path.read_text() == before


def test_rebind_refuses_when_the_network_changed(studio_home, fake):
    from mpnstudio.desktop import projects
    from mpnstudio.store.db import Store
    a, b = fake(), fake(name="core-B")
    ca = coreconf.save({"name": "A", "base_url": a.url, "model_id": "fake-model"})
    cb = coreconf.save({"name": "B", "base_url": b.url, "model_id": "fake-model"})
    net = make_network(studio_home)
    m = models.build(models.create("R", "", ca["id_ref"], net)["id"])
    st = Store(projects.store_path(net))
    with st.edit(kind="edit", message="x") as tx:
        tx.put_node(title="New river", summary="A new river runs here.", origin="reference", cells={"knowledge": {"statements": [{"text": "A new river runs through this valley for ninety kilometres."}]}})
    st.close()
    models.update(m["id"], {"core_id": cb["id_ref"]})
    with pytest.raises(models.ModelError) as e:
        models.rebind(m["id"])
    assert "rebuilt" in str(e.value)


def test_a_server_core_with_a_key_gets_it_from_the_environment_and_the_manifest_has_none(studio_home, fake):
    f = fake(key=KEY)
    c = coreconf.save({"name": "Keyed", "base_url": f.url, "model_id": "fake-model", "api_key": KEY})
    m = models.build(models.create("Keyed pairing", "", c["id_ref"], make_network(studio_home))["id"])
    d = models.model_dir(m["id"])
    for p in d.rglob("*"):
        if p.is_file() and p.stat().st_size < 5_000_000:
            assert KEY.encode() not in p.read_bytes(), p
    from mpnstudio.library import runner
    env_seen = {}

    class P:
        def __init__(self, cmd, **kw):
            env_seen.update(kw["env"])
        def poll(self): return None
    import subprocess
    orig = subprocess.Popen
    subprocess.Popen = P
    try:
        runner.Run("k", d / "build", 4096)
    finally:
        subprocess.Popen = orig
    assert env_seen.get("MPN_CORE_API_KEY") == KEY


def test_exporting_a_server_core_pairing_is_refused_clearly(studio_home, fake):
    c = coreconf.save({"name": "S", "base_url": fake().url, "model_id": "fake-model"})
    m = models.build(models.create("S pairing", "", c["id_ref"], make_network(studio_home))["id"])
    from mpnstudio.library import runner
    with pytest.raises(runner.RunError) as e:
        runner.export(m["id"], studio_home / "out.mpn")
    assert "no weights" in str(e.value)


def test_a_pairing_whose_server_was_removed_says_so(studio_home, fake):
    c = coreconf.save({"name": "Gone", "base_url": fake().url, "model_id": "fake-model"})
    m = models.create("P", "", c["id_ref"], make_network(studio_home))
    os.unlink(studio_home / "data" / "core-servers" / "gone.json")
    assert "no longer configured" in " ".join(models.get(m["id"])["status"]["problems"])
