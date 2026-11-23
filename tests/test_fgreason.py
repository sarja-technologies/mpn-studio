"""Answering from a native FG-MPN network: entity linking, relation following, status handling, citations, and the pairing built on it.

The world is synthetic (tests/fgworld.py, built with the public runtime's fixtures), so these run anywhere the `fgmpn` runtime is installed.
The same retrieval against the real 119.7 million record store is in tests/test_fgreason_spark.py (skipped unless MPN_N119M_STORE is set).
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fgmpn")

import fgworld                                                       # noqa: E402
from fakecore import FakeCore                                         # noqa: E402
from mpnstudio.engine import fgreason                                 # noqa: E402
from mpnstudio.engine.fgreason import FgNetwork, FgRetriever         # noqa: E402
from mpnstudio.model.prompts import render_node                       # noqa: E402


@pytest.fixture(params=[False, True], ids=["text-index", "title-index"])
def world(tmp_path, request):
    root = tmp_path / "store"
    ids = fgworld.build(root, indexed=request.param)
    net = FgNetwork(root)
    yield root, ids, net, FgRetriever(net)


def titles(r):
    return [c["node"]["title"] for c in r["cands"]]


def text_of(r):
    return "\n".join(render_node(c["node"], i) for i, c in enumerate(r["cands"], start=1))


# ------------------------------------------------------------------------------------------------ linking and reading
def test_a_name_in_the_question_links_to_its_record_and_the_facts_come_from_its_validated_claims(world):
    _, ids, net, rt = world
    r = rt.retrieve("Where was Marta Kowalenko born?")
    assert titles(r) == ["Marta Kowalenko"] and r["cands"][0]["id"] == ids["Q2001"]
    n = r["cands"][0]["node"]
    facts = {f["property"]: f["value"] for f in n["cells"]["knowledge"]["facts"]}
    assert facts["place of birth"] == "Wrocklow" and facts["date of birth"] == "1951-03-02" and facts["occupation"] == "botanist"
    assert n["cells"]["knowledge"]["statements"][0]["text"].startswith("Polandian botanist")
    src = n["sources"][0]
    assert src["kind"] == "wikidata" and src["url"].endswith("/Q2001") and src["revision"] and src["license"] == "CC0-1.0"
    assert n["key"] == "wd:Q2001" and n["fg_status"] == "validated"


def test_the_property_the_question_asks_for_is_listed_first(world):
    _, _, _, rt = world
    first = lambda q: rt.retrieve(q)["cands"][0]["node"]["cells"]["knowledge"]["facts"][0]["property"]
    assert first("In what year was Marta Kowalenko born?") == "date of birth"
    assert first("Where was Marta Kowalenko born?") == "place of birth"


def test_two_records_with_one_title_are_told_apart_by_the_questions_description(world):
    _, ids, _, rt = world
    assert rt.retrieve("Who is Orlin (Polandian actor)?")["cands"][0]["id"] == ids["Q3002"]
    assert rt.retrieve("Which river called Orlin runs through Polandia?")["cands"][0]["id"] == ids["Q3001"]


def test_a_name_is_found_inside_the_question_in_any_position_and_by_a_shorter_alias_style_name(world):
    _, ids, _, rt = world
    assert rt.retrieve("Tell me what the Lichen Institute of Wrocklow was founded in.")["cands"][0]["id"] == ids["Q9100"]
    assert rt.retrieve("Hugo Strandvik: when was he born? Answer briefly.")["cands"][0]["id"] == ids["Q2002"]
    # no exact title: a capitalised name that titles and aliases contain is searched
    r = rt.retrieve("What did Kowalenko do?")
    assert r["cands"] and r["cands"][0]["id"] == ids["Q2001"]


def test_an_invented_name_links_to_nothing(world):
    _, _, _, rt = world
    for q in ("Who is Zorblax Quindleton?", "Where was Quin Dlexton born?", "What is the weather in Paris today?"):
        r = rt.retrieve(q)
        assert r["cands"] == [], q


def test_a_scholarly_work_is_found_by_its_whole_title(world):
    _, _, _, rt = world
    r = rt.retrieve("In what year was A long study of lichen growth on the walls of Wrocklow published?")
    assert titles(r)[0].startswith("A long study of lichen") and "1994" in text_of(r)


# ------------------------------------------------------------------------------------------------ following relations
def test_a_second_hop_reaches_the_record_a_fact_points_to(world):
    _, _, _, rt = world
    r = rt.retrieve("In which country is the place where Marta Kowalenko was born?")
    assert "Wrocklow" in titles(r)
    place = next(c for c in r["cands"] if c["node"]["title"] == "Wrocklow")
    assert place["relation"] == "place of birth" and place["from"] == "Marta Kowalenko"
    assert {"property": "country", "value": "Polandia"}.items() <= next(f for f in place["node"]["cells"]["knowledge"]["facts"] if f["property"] == "country").items()
    assert r["trace"]["hops"][0]["added"]


def test_no_hop_is_taken_when_the_first_record_holds_the_answer(world):
    _, _, _, rt = world
    r = rt.retrieve("Where was Marta Kowalenko born?")
    assert titles(r) == ["Marta Kowalenko"] and r["trace"].get("follow", "").startswith("not needed")


def test_the_number_of_nodes_is_bounded(world):
    _, _, _, rt = world
    r = rt.retrieve("In which country is the place where Marta Kowalenko was born?", max_nodes=2)
    assert len(r["cands"]) <= 2


def test_mpn_follow_and_mpn_search_tools_return_new_validated_nodes_only(world):
    _, ids, _, rt = world
    anchor = rt.retrieve("Where was Marta Kowalenko born?")["cands"][0]
    new, links = rt.follow_relation(anchor, "place of birth", {anchor["id"]})
    assert [c["node"]["title"] for c in new] == ["Wrocklow"] and "place of birth" in links
    assert rt.follow_relation(anchor, "place of birth", {anchor["id"], ids["Q1001"]})[0] == []
    found = rt.search_query("Hugo Strandvik", {anchor["id"]})
    assert [c["id"] for c in found] == [ids["Q2002"]]
    assert rt.search_query("Hugo Strandvik", {ids["Q2002"]}) == []


# ------------------------------------------------------------------------------------------------ status: validated and quarantined
def test_a_quarantined_claim_is_in_the_store_but_never_in_a_nodes_facts(world):
    _, ids, net, rt = world
    q = net.get(ids["quarantined_claim"])
    assert q["status"] == "quarantined" and q["cells"]["knowledge"]["facts"][0]["value"] == "Kowalenko botanist photographs"      # kept; returned by id, with its status
    assert net.get(ids["validated_claim"])["status"] == "validated"
    r = rt.retrieve("What is the Commons category of Marta Kowalenko?")
    assert titles(r) == ["Marta Kowalenko"]                          # the subject is found, validated; what it holds is what is validated
    assert "Commons category" not in text_of(r) and "photographs" not in text_of(r)
    assert all("Commons" not in f["property"] for c in r["cands"] for f in c["node"]["cells"]["knowledge"]["facts"])


def test_a_quarantined_record_is_not_found_by_title_search_or_question(world):
    _, ids, net, rt = world
    assert net.get(ids["Q9001"])["status"] == "quarantined"          # by id: returned with its status
    assert net.title_lookup("Vesper Ellison", 6, None) == []
    assert net.search("Vesper Ellison", 12, None) == []
    assert rt.retrieve("Who is Vesper Ellison?")["cands"] == []


def test_an_explicit_quarantined_id_gives_a_notice_and_no_evidence(world):
    _, ids, _, rt = world
    for key in ("quarantined_claim", "Q9001"):
        r = rt.retrieve(f"What is the record {ids[key]}?")
        assert r["cands"] == []
        n = r["trace"]["notices"][0]
        assert n["id"] == ids[key] and n["status"] == "quarantined" and "cannot be cited" in n["text"]
    r = rt.retrieve(f"What is the record {ids['Q2001']}?")           # a validated id is an anchor like a name
    assert r["cands"][0]["id"] == ids["Q2001"] and r["cands"][0]["via"] == "record id" and r["trace"]["notices"] == []
    assert rt.retrieve("What is record " + "0" * 32 + "?")["trace"]["notices"][0]["status"] == "unknown"


def test_a_quarantined_value_is_never_followed_to_a_record(world, tmp_path):
    _, ids, net, rt = world
    anchor = rt.retrieve("Where was Marta Kowalenko born?")["cands"][0]
    new, _ = rt.follow_relation(anchor, "any", {anchor["id"]})
    assert ids["Q9001"] not in [c["id"] for c in new] and all(c["node"]["fg_status"] == "validated" for c in new)


# ------------------------------------------------------------------------------------------------ resources
def test_retrieval_reads_by_index_and_never_iterates_the_store(world, monkeypatch):
    _, _, net, rt = world
    seg = net.store.segments[0]
    reads = {"cols": 0}
    orig = seg.cols

    def counting(e, _o=orig):
        reads["cols"] += 1
        return _o(e)
    monkeypatch.setattr(seg, "cols", counting, raising=False)
    rt.retrieve("In which country is the place where Marta Kowalenko was born?")
    assert reads["cols"] < net.identity["stored_records"]


def test_the_store_identity_comes_from_the_manifest_alone(world):
    root, _, net, _ = world
    i = fgreason.store_identity(root)
    cur = (root / "CURRENT").read_text().strip()
    assert i["manifest_sha256"] == hashlib.sha256((root / "manifests" / cur).read_bytes()).hexdigest() and i["stored_records"] == net.identity["stored_records"] > 20
    assert net.describe()["generation"] == 1 and net.describe()["format"] == "fgmpn"
    assert fgreason.proc_counters()["major_faults"] >= 0


# ------------------------------------------------------------------------------------------------ the engine, with a core that answers from the nodes
def scripted(body, who: str = "") -> str:
    """A core that writes what the first supplied node says, citing it; says it has nothing when no node was supplied."""
    user = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")
    user = user if isinstance(user, str) else " ".join(p.get("text", "") for p in user)
    if "Knowledge nodes" not in user:
        return "I do not have that information."
    m = re.search(r"\[1\] ([^\n]+)\n.*?place of birth: ([^;\n]+)", user, re.S)
    if m:
        return f"{m.group(1)} was born in {m.group(2)} [1]. See also the Moon [9]." + (f" ({who})" if who else "")
    return "The nodes do not cover that." + (f" ({who})" if who else "")


@pytest.fixture
def engine(world, tmp_path):
    from mpnstudio.engine.engine import Engine
    root, ids, net, _ = world
    core = FakeCore(name="core-A", reply=scripted)
    run = tmp_path / "run"
    (run / "data" / "logs").mkdir(parents=True)
    man = {"name": "Test", "model_id": "test", "core": {"name": "fake", "server": True}, "paths": {"core": "none", "mmproj": "none", "encoder": "none", "network": "none", "checkpoint": "none", "fgmpn": str(root)},
           "core_endpoint": {"id": "a", "name": "A", "base_url": core.url, "model_id": "fake-model", "context_tokens": None, "max_output_tokens": None, "timeout_s": 30, "key_env": "MPN_CORE_API_KEY"}}
    (run / "MANIFEST.json").write_text(json.dumps(man))
    eng = Engine(run, run / "data", context=8192)
    eng.budget["tool_rounds"] = 0
    eng.load(log=lambda s: None)
    yield eng, ids, core
    eng.unload()
    core.close()


def ask(eng, q, **kw):
    out = {"answer": "", "route": None, "validation": None, "done": None}
    for kind, data in eng.run([{"role": "user", "content": q}], **kw):
        if kind == "token":
            out["answer"] += data["text"]
        elif kind in ("route", "validation", "done", "error"):
            out[kind] = data
    out["answer"] = out["answer"].strip()
    return out


def test_the_engine_answers_from_the_network_and_numbers_citations_by_node(engine):
    eng, ids, core = engine
    assert eng.fg is not None and eng.store is None and eng.enc is None and eng.ck is None        # no snapshot, no embeddings
    r = ask(eng, "Where was Marta Kowalenko born?")
    assert r["answer"].startswith("Marta Kowalenko was born in Wrocklow [1].")
    assert "[9]" not in r["answer"]                                                                  # a number that names no supplied node is removed
    cites = r["validation"]["citations"]
    assert cites[0]["n"] == 1 and cites[0]["node_id"] == ids["Q2001"] and cites[0]["key"] == "wd:Q2001" and cites[0]["cited"] and cites[0]["status"] == "validated"
    assert cites[0]["source"]["url"].endswith("Q2001") and cites[0]["source"]["kind"] == "wikidata"
    assert any(c["kind"] == "citations_removed" for c in r["validation"]["checks"])
    prompt = next(m["content"] for m in core.requests[-1]["body"]["messages"] if m["role"] == "user")
    assert prompt.startswith("Knowledge nodes:\n[1] Marta Kowalenko") and "place of birth: Wrocklow" in prompt
    nodes = r["route"]["nodes"]
    assert nodes[0]["id"] == ids["Q2001"] and nodes[0]["status"] == "validated" and nodes[0]["read_as"] == ["text"] and r["route"]["fgmpn"]["mentions"]
    assert eng.status()["routing"].startswith("FG-MPN") and eng.status()["network"]["format"] == "fgmpn"


def test_a_question_the_network_does_not_cover_gets_no_nodes_and_no_citation(engine):
    eng, _, core = engine
    r = ask(eng, "Who is Zorblax Quindleton?")
    assert r["answer"] == "I do not have that information." and r["validation"]["citations"] == [] and r["route"]["nodes"] == []
    assert "Knowledge nodes" not in next(m["content"] for m in core.requests[-1]["body"]["messages"] if m["role"] == "user")


def test_a_quarantined_record_is_never_supplied_to_the_core_or_cited(engine):
    eng, ids, core = engine
    for q in ("What is the Commons category of Marta Kowalenko?", "Who is Vesper Ellison?", f"What is the network record {ids['quarantined_claim']}?"):
        r = ask(eng, q)
        sent = json.dumps(core.requests[-1]["body"]["messages"])
        assert "photographs" not in sent and ids["Q9001"] not in sent
        assert all(n["status"] in (None, "validated") for n in r["route"]["nodes"])
        assert not [c for c in r["validation"]["citations"] if c["node_id"] in (ids["quarantined_claim"], ids["Q9001"])]
    note = next(m["content"] for m in core.requests[-1]["body"]["messages"] if m["role"] == "user")
    assert "status quarantined" in note and "cannot be cited" in note                                  # the core is told, and the record is not given
    assert ask(eng, f"What is the network record {ids['quarantined_claim']}?")["route"]["fgmpn"]["notices"][0]["status"] == "quarantined"


def test_core_alone_reads_no_node(engine):
    eng, _, core = engine
    r = ask(eng, "Where was Marta Kowalenko born?", mode="core")
    assert r["route"] is None or r["route"]["nodes"] == []
    assert "Knowledge nodes" not in next(m["content"] for m in core.requests[-1]["body"]["messages"] if m["role"] == "user")


# ------------------------------------------------------------------------------------------------ a pairing on the store, in place
def tree_state(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def test_a_pairing_uses_the_store_where_it_is_and_swapping_the_core_leaves_the_network_untouched(studio_home, world):
    from mpnstudio.catalogue import install
    from mpnstudio.library import coreconf, models, runner
    root, ids, _, _ = world
    a, b = FakeCore(name="core-A", reply=lambda body: scripted(body, "core-A")), FakeCore(name="core-B", reply=lambda body: scripted(body, "core-B"))
    try:
        ca = coreconf.save({"name": "Core A", "base_url": a.url, "model_id": "fake-model"})
        cb = coreconf.save({"name": "Core B", "base_url": b.url, "model_id": "fake-model"})
        rec = install.install_local_fgmpn(root, "Test world", verify="size", rid="test-world", extra={"nodes": 33})
        assert rec["store_path"] == str(root.resolve()) and rec["verified"] == "full" or rec["verified"] == "size"
        assert rec["store"]["manifest_sha256"] and "full SHA-256 check was not run" in rec["warnings"][0]
        before = tree_state(root)
        m = models.create("Marta", "", ca["id_ref"], None)
        raw = models._read(m["id"])
        raw["network"] = {"release": {"id": rec["id"], "version": rec["version"], "format": "fgmpn"}}
        models._write(m["id"], raw)
        m = models.get(m["id"])
        assert m["status"]["state"] == "needs build" and not m["status"]["problems"]
        log = []
        m = models.build(m["id"], log=log.append)
        assert m["status"]["state"] == "ready" and "core-A" in m["build"]["test"]["answer"] and m["build"]["routing"] == "fgmpn"
        bdir = models.model_dir(m["id"]) / "build"
        assert sum(p.stat().st_size for p in bdir.rglob("*") if p.is_file()) < 100_000                 # nothing of the store was copied
        man = json.loads((bdir / "MANIFEST.json").read_text())
        assert man["paths"]["fgmpn"] == str(root.resolve()) and man["paths"]["encoder"] == "none" and man["paths"]["network"].endswith("network.sqlite") and not (bdir / "network.sqlite").exists()
        assert man["network_format"]["manifest_sha256"] == rec["store"]["manifest_sha256"]
        built = m["build"]
        # swap the core: rebind, never a rebuild of the network
        m = models.update(m["id"], {"core_id": cb["id_ref"]})
        assert m["status"]["state"] == "needs build" and "network is kept" in m["status"]["problems"][0]
        m = models.rebind(m["id"])
        assert m["status"]["state"] == "ready" and "core-B" in m["build"]["test"]["answer"] and m["build"]["rebound"]
        assert m["build"]["net_hash"] == built["net_hash"] and m["build"]["network_rev"] == built["network_rev"] and m["build"]["config_hash"] != built["config_hash"]
        assert tree_state(root) == before                                                              # the store was neither written nor touched
        with pytest.raises(runner.RunError) as e:
            runner.export(m["id"], studio_home / "x.mpn")
        assert "FG-MPN" in str(e.value)
    finally:
        a.close()
        b.close()


def test_a_store_that_lost_a_segment_says_so_instead_of_answering(studio_home, world):
    from mpnstudio.catalogue import install
    from mpnstudio.library import coreconf, models
    root, _, _, _ = world
    a = FakeCore(reply=scripted)
    try:
        ca = coreconf.save({"name": "Core A", "base_url": a.url, "model_id": "fake-model"})
        rec = install.install_local_fgmpn(root, "Test world", verify="size", rid="test-world")
        m = models.create("Marta", "", ca["id_ref"], None)
        raw = models._read(m["id"])
        raw["network"] = {"release": {"id": rec["id"], "version": rec["version"], "format": "fgmpn"}}
        models._write(m["id"], raw)
        seg = next((root / "segments").glob("*.fgs"))
        seg.write_bytes(seg.read_bytes()[:-9])
        st = models.get(m["id"])["status"]
        assert st["state"] == "incomplete" and "missing parts" in st["problems"][0]
    finally:
        a.close()


def test_registering_a_store_by_size_does_not_read_its_segments(studio_home, world, monkeypatch):
    from mpnstudio.catalogue import install
    from mpnstudio.mpnnet import fgmpn_local
    root, _, _, _ = world
    monkeypatch.setattr(fgmpn_local, "verify_store", lambda *a, **k: pytest.fail("a full verification ran"))
    rec = install.install_local_fgmpn(root, "Test world", verify="size")
    assert rec["verified"] == "size" and rec["store_path"] == str(root.resolve())
    with pytest.raises(install.InstallError):
        install.install_local_fgmpn(root, "x", verify="never")
