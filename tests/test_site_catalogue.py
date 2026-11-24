"""The website's catalogue (`https://fylu.me/api/catalogue.json`, schema fylume-catalogue/1) read natively; the two payloads of an
official network (fgmpn to explore, studio-store to answer); a pairing's retrieval encoder."""
import json
import os
from pathlib import Path

import pytest

import relhost
from mpnstudio.catalogue import install, manifest as mf, site, source
from mpnstudio.desktop import settings
from mpnstudio.library import cores, models, networks

LIVE = Path(__file__).parent / "data" / "live-catalogue-2026-11-22.json"      # `curl https://fylu.me/api/catalogue.json`, saved
LIVE2 = Path(__file__).parent / "data" / "live-catalogue-2026-11-23.json"     # the same address after the Hugging Face packages were listed


def serve_doc(host, doc, path="/api/catalogue.json"):
    host.files[path] = json.dumps(doc).encode()
    settings.update({"catalogue": {"url": host.url + path}})


def seen(host, fragment):
    return [r for r in host.requests if fragment in r[0]]


# ------------------------------------------------------------------------------------------------ the live response
def test_default_catalogue_address_is_the_websites():
    assert source.DEFAULT_URL == "https://fylu.me/api/catalogue.json"
    assert "/catalogue/v1/" not in open(source.__file__).read()


def test_the_saved_live_response_is_read_natively_and_legacy_entries_are_download_only(host):
    doc = json.loads(LIVE.read_text())
    assert doc["schema"] == "fylume-catalogue/1" and doc["cores"] == [] and len(doc["mpns"]) == 1 and len(doc["models"]) == 2 and len(doc["apps"]) == 1
    serve_doc(host, doc)
    r = source.refresh()
    assert r["releases"] == 3 and r["skipped"] == []                  # models and mpns; the app is not Studio's business; cores are empty
    got = source.load()
    assert got["source"]["updated_at"] == doc["updated_at"] and got["rejected"] == []
    by = {(x["kind"], x["id"]): x for x in got["releases"]}
    assert set(by) == {("mpn", "yaob-n10k"), ("model", "yaob-next"), ("model", "yaob-n10k-model")}
    mpn = by[("mpn", "yaob-n10k")]
    assert mpn["download_only"]["url"].endswith("yaob-n10k-0.1.0.tar.gz") and mpn["download_only"]["bytes"] == 45621685
    assert "Download only, not installable by Studio" in mpn["compat"]["reasons"][0] and mpn["problems"] == []
    assert mpn["files"][0]["sha256"] == "bc2f831f090e2c25c57860f54885600470ce1def99cbd105e2f79c7094bbdd5c" and mpn["files"][0]["urls"]
    assert mpn["licence"]["label"] and mpn["licence"]["notes"].startswith("Node data: CC BY-SA 4.0")
    nxt = by[("model", "yaob-next")]
    assert nxt["download_only"]["filename"] == "Yaob-Next-N10K-C4B-0.2.0.mpn" and nxt["download_only"]["bytes"] == 6635995615
    assert by[("model", "yaob-n10k-model")]["download_only"]["page"].endswith("v0.1.0")
    # files without an exact size and SHA-256 (SHA256SUMS) are never downloaded
    assert all(f["sha256"] for r_ in got["releases"] for f in r_["files"])


def test_the_live_response_with_the_public_packages(host):
    """Two networks (FG-MPN store and Studio store payloads), two GGUF cores and the adapters entry; the complete-model downloads stay download-only."""
    doc = json.loads(LIVE2.read_text())
    serve_doc(host, doc)
    r = source.refresh()
    assert r["releases"] == 7 and r["skipped"] == []
    by = {(x["kind"], x["id"]): x for x in source.load()["releases"]}
    for rid in ("yaob-next-n10k",):
        n = by[("mpn", rid)]
        assert not n.get("download_only") and n["compat"]["ok"] and n["problems"] == []
        fmts = {f.get("format") for f in n["files"]}
        assert {"fgmpn", "studio-store"} <= fmts, fmts
        assert {p["format"]: p["ok"] for p in n["payloads"]} == {"fgmpn": True, "studio-store": True}, n["payloads"]
    # the retired original (yaob-n10k 0.1.0) is archived in 0.6.1: listed, labelled, download only, never installed by Studio
    assert by[("mpn", "yaob-n10k")]["download_only"] and by[("mpn", "yaob-n10k")]["name"] == "Yaob N10K (retired)"
    for cid in ("qwen3-5-4b-q8-0-gguf", "qwen3-embedding-0-6b-f16-gguf"):
        assert by[("core", cid)]["compat"]["ok"]
    assert not by[("core", "yaob-n10k-specialists-0-1-0")]["compat"]["ok"]            # adapters need their base model
    assert by[("model", "yaob-next")]["download_only"] and by[("model", "yaob-n10k-model")]["download_only"]


def test_installing_a_legacy_entry_is_refused_by_name_not_as_an_error_screen(host):
    serve_doc(host, json.loads(LIVE.read_text()))
    source.refresh()
    legacy = source.find("mpn", "yaob-n10k", "0.1.0")
    with pytest.raises(install.InstallError) as e:
        install.install(legacy)
    assert e.value.code == "download_only" and "not installable by Studio" in str(e.value) and e.value.status == 422


def test_the_api_lists_legacy_entries_with_their_download_and_refuses_to_install_them(host, studio_home, monkeypatch):
    monkeypatch.setenv("MPN_DESKTOP", "1")
    import importlib
    import mpnstudio.api.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    serve_doc(host, json.loads(LIVE.read_text()))
    with TestClient(appmod.create_app(), client=("127.0.0.1", 5000)) as c:
        assert c.post("/api/catalogue/refresh").status_code == 200
        cat = c.get("/api/catalogue").json()
        assert cat["default_url"] == "https://fylu.me/api/catalogue.json" and len(cat["releases"]) == 3
        row = next(x for x in cat["releases"] if x["id"] == "yaob-n10k")
        assert row["download_only"]["url"].endswith(".tar.gz") and row["licence"]["label"] and not row["compat"]["ok"] and row["installed"] is None
        r = c.post("/api/catalogue/install", json={"kind": "mpn", "id": "yaob-n10k", "version": "0.1.0"})
        assert r.status_code == 422 and r.json()["code"] == "download_only"
    monkeypatch.delenv("MPN_DESKTOP")
    importlib.reload(appmod)


def test_a_catalogue_file_saved_from_the_site_can_be_imported(host, studio_home):
    added = source.import_file(LIVE)
    assert {(a["kind"], a["id"]) for a in added} == {("mpn", "yaob-n10k"), ("model", "yaob-next"), ("model", "yaob-n10k-model")} and all(a["download_only"] for a in added)
    assert len(source.load()["releases"]) == 3 and source.load()["releases"][0]["origin"] == "file"


def test_empty_categories_and_a_manifest_list_still_work(host, studio_home):
    serve_doc(host, relhost.site_doc(host))
    assert source.refresh()["releases"] == 0 and source.load()["releases"] == []
    serve_doc(host, {"schema": "fylume-catalogue/1", "releases": [relhost.core_release(host)]})
    assert source.refresh()["releases"] == 1
    got = source.load()
    assert got["rejected"] == [] and [x["kind"] for x in got["releases"]] == ["core"]


# ------------------------------------------------------------------------------------------------ site entries that can be installed
def test_a_core_entry_with_files_installs_from_its_own_urls(host, studio_home):
    c = relhost.core_release(host, quants=("Q4_K_M", "Q8_0"))
    serve_doc(host, relhost.site_doc(host, cores=[relhost.as_site(c, host)]))
    source.refresh()
    r = source.find("core", c["id"], "1.0.0")
    assert r["shape"] == "site" and r["compat"]["ok"] and not r["download_only"] and r["licence"]["notes"]
    assert [f["quant"] for f in r["detail"]["formats"]] == ["Q4_K_M", "Q8_0"]
    rec = install.install(r, file="Q8_0")
    assert cores.get(rec["core_id"])["present"] and not seen(host, "Q4_K_M")


def test_a_core_entry_without_a_gguf_says_so(host, studio_home):
    c = relhost.core_release(host)
    e = relhost.as_site(c, host)
    e["core"]["formats"] = [{"kind": "safetensors", "quant": "bf16", "file": "x.safetensors"}]
    r = site.release_of("core", e)
    assert not r["compat"]["ok"] and "no GGUF" in r["compat"]["reasons"][0] and not r["download_only"]


def test_an_installable_entry_needs_exact_sizes_and_checksums_and_a_licence(host, studio_home):
    m = relhost.both_payloads_release(host)
    e = relhost.as_site(m, host)
    e["files"][0]["sha256"] = None
    e["licence"] = ""
    r = site.release_of("mpn", e)
    assert any("no exact size and SHA-256" in p for p in r["problems"]) and "the licence is not stated" in r["problems"]


def test_payload_names_are_read_and_descriptive_labels_are_ignored(host, studio_home):
    m = relhost.both_payloads_release(host)
    e = relhost.as_site(m, host)
    r = site.release_of("mpn", e)
    assert [p["format"] for p in r["payloads"]] == ["fgmpn", "studio-store"] and all(p["ok"] for p in r["payloads"])
    assert next(f for f in r["files"] if f["path"] == "README.md")["format"] is None             # "Descriptive label, not a payload name"
    assert {f["path"] for f in next(p for p in r["payloads"] if p["format"] == "studio-store")["files"]} == \
        {"studio/network/yaob-n10k.sqlite", "studio/network/checkpoint/manifest.json", "studio/network/checkpoint/shards/shared.safetensors"}
    assert install.default_format(r) == "fgmpn"


def test_a_store_kept_under_store_slash_installs_explores_and_verifies(host, studio_home):
    """The published Yaob N10K packages keep the FG-MPN store in store/ beside studio/: the payload is usable, installs with the store
    where Studio looks for it, and verifies, with the documents outside it checked too."""
    m = relhost.both_payloads_release(host, store_prefix="store")
    r = site.release_of("mpn", relhost.as_site(m, host))
    pl = {p["format"]: p for p in r["payloads"]}
    assert pl["fgmpn"]["ok"], pl["fgmpn"]["reason"]
    rec = install.install(r, fmt="fgmpn")
    assert rec["store_prefix"] == "store" and rec["store"]["nodes"] == 42
    assert install.store_dir(rec).name == "store" and (install.store_dir(rec) / "CURRENT").is_file()
    assert install.verify_installed("mpn", r["id"], r["version"], fmt="fgmpn")["ok"]
    assert install.get_installed("mpn", r["id"], r["version"], "fgmpn")["store_prefix"] == "store"


# ------------------------------------------------------------------------------------------------ two payloads, one repo
@pytest.mark.parametrize("shape", ["manifest", "site"])
def test_only_the_payload_studio_needs_is_downloaded(host, studio_home, shape):
    m = relhost.both_payloads_release(host)
    r = mf.parse_bytes(json.dumps(m).encode()) if shape == "manifest" else site.release_of("mpn", relhost.as_site(m, host))
    p = install.plan(r, fmt="fgmpn")
    q = install.plan(r, fmt="studio-store")
    assert p["files"] == 5 and q["files"] == 4 and p["download_bytes"] != q["download_bytes"]          # payload + README
    rec = install.install(r, fmt="fgmpn")
    assert rec["format"] == "fgmpn" and rec["store"]["nodes"] == 42 and not seen(host, "studio/")
    host.requests.clear()
    rec2 = install.install(r, fmt="studio-store")
    assert rec2["format"] == "studio-store" and rec2["project"] and rec2["router"]
    assert not seen(host, "segments/") and not seen(host, "CURRENT") and seen(host, "studio/network/yaob-n10k.sqlite")
    # both payloads of one release are installed side by side, each with its own record
    assert sorted(x["format"] for x in install.installed("mpn")) == ["fgmpn", "studio-store"]
    nets = networks.list_networks()
    assert len(nets) == 1 and nets[0]["nodes"] == 20 and nets[0]["router"] and nets[0]["origin"]["kind"] == "release"
    assert install.verify_installed("mpn", r["id"], "1.0.0", fmt="fgmpn")["ok"]
    install.uninstall("mpn", r["id"], "1.0.0", "fgmpn")
    assert [x["format"] for x in install.installed("mpn")] == ["studio-store"]


def test_default_install_is_the_primary_payload_and_a_missing_payload_is_named(host, studio_home):
    only_fg = mf.parse(relhost.mpn_release(host))
    assert install.install(only_fg)["format"] == "fgmpn"
    with pytest.raises(install.InstallError) as e:
        install.install(only_fg, fmt="studio-store")
    assert e.value.code == "no_payload" and "fgmpn" in str(e.value)


def test_a_studio_payload_without_a_network_file_is_not_usable(host, studio_home):
    m = relhost.both_payloads_release(host)
    m["files"] = [f for f in m["files"] if f["path"] != "studio/network/yaob-n10k.sqlite"]
    r = mf.parse(m)
    pl = {p["format"]: p for p in r["payloads"]}
    assert pl["fgmpn"]["ok"] and not pl["studio-store"]["ok"] and "no network file" in pl["studio-store"]["reason"] and r["compat"]["ok"]


# ------------------------------------------------------------------------------------------------ prebuilt pairings with an encoder
def parts(host):
    mpn = relhost.both_payloads_release(host)
    core = relhost.core_release(host)
    enc = relhost.core_release(host, rid="test-embedding", arch="bert", pooling=True, quants=("F16",))
    return mpn, core, enc


def test_manifest_pairing_installs_the_studio_payload_the_core_and_the_encoder(host, studio_home):
    mpn, core, enc = parts(host)
    model = relhost.model_release(host, mpn, core, encoder=enc)
    assert mf.parse(model)["problems"] == []
    m = install.install_model(mf.parse_bytes(json.dumps(model).encode()))
    assert m["encoder"] and m["encoder"]["id"] and m["core"]["id"] and m["network"]["id"]
    e = cores.get(m["encoder"]["id"])
    assert e["kind"] == "encoder" and e["release"]["id"] == "test-embedding"
    assert not seen(host, "segments/")                                          # the pairing wanted the Studio payload only
    assert m["release"]["encoder"]["id"] == "test-embedding"
    # the router was trained for that encoder: Studio now knows its file
    r = networks.router(m["network"]["id"])
    assert r["encoder"]["sha256"] == e["sha256"]
    assert models.get(m["id"])["status"]["state"] == "needs build"
    assert {x["kind"] for x in install.installed()} == {"mpn", "core"} and len(install.installed("core")) == 2


def test_an_encoder_pin_must_be_complete_or_null(host, studio_home):
    mpn, core, _ = parts(host)
    model = relhost.model_release(host, mpn, core)
    model["model"]["encoder"] = {"id": "x"}
    assert "model.encoder must pin" in " ".join(mf.parse(model)["problems"])


def test_website_pairing_made_of_components_installs_all_three(host, studio_home):
    mpn, core, enc = parts(host)
    sm = relhost.site_model(host, mpn, core, enc)
    serve_doc(host, relhost.site_doc(host, models=[sm], mpns=[relhost.as_site(mpn, host)], cores=[relhost.as_site(core, host), relhost.as_site(enc, host)]))
    source.refresh()
    r = source.find("model", "site-pairing", "1.0.0")
    assert r["compat"]["ok"] and not r["download_only"] and r["detail"]["encoder"]["id"] == "test-embedding"
    m = install.install_model(r)
    assert m["encoder"]["id"] and m["network"]["id"] and m["status"]["state"] == "needs build"
    assert not seen(host, "segments/")


def test_pairing_that_pins_an_fgmpn_only_network_installs_its_store_and_answers_from_it_in_place(host, studio_home):
    fg_only = relhost.mpn_release(host, rid="fg-only")
    core = relhost.core_release(host)
    model = relhost.model_release(host, fg_only, core)
    r = mf.parse_bytes(json.dumps(model).encode())
    m = install.install_model(r)
    assert m["network"] == {"release": {"id": "fg-only", "version": "1.0.0", "format": "fgmpn"}}
    assert m["status"]["state"] == "needs build" and not m["status"]["problems"]            # an FG-MPN network is read natively: nothing to convert
    kinds = {(x["kind"], x.get("format")) for x in install.installed()}
    assert ("mpn", "fgmpn") in kinds and ("mpn", "studio-store") not in kinds and seen(host, "segments/")
    for x in (fg_only, core):                                                                   # the Releases view does not block it
        f = Path(studio_home) / f"{x['id']}.json"
        f.write_bytes(json.dumps(x).encode())
        source.import_file(f)
    from mpnstudio.api import catalogue_routes
    index = {(x["kind"], x["id"], x["version"]): x for x in source.load()["releases"]}
    v = catalogue_routes.view(mf.parse_bytes(json.dumps(model).encode()), index)
    assert not (v.get("model") or {}).get("blocked")


def test_a_network_release_with_no_payload_a_pairing_can_answer_from_says_so():
    r = {"name": "Nothing", "version": "1.0.0", "payloads": [{"format": "other", "ok": False, "reason": "unknown format"}]}
    assert install.pairing_payload(r) is None
    why = install.studio_payload_problem(r)
    assert "no payload a pairing can answer from" in why and "unknown format" in why
    ok = {"name": "Both", "version": "1", "payloads": [{"format": "fgmpn", "ok": True}, {"format": "studio-store", "ok": True}]}
    assert install.pairing_payload(ok) == "studio-store" and install.studio_payload_problem(ok) is None             # N10K keeps its Studio payload
    assert install.pairing_payload({"payloads": [{"format": "fgmpn", "ok": True}]}) == "fgmpn"


def test_pairing_with_my_own_server_still_installs_the_encoder(host, studio_home):
    from fakecore import FakeCore
    from mpnstudio.library import coreconf
    mpn, core, enc = parts(host)
    model = relhost.model_release(host, mpn, core, encoder=enc)
    fc = FakeCore()
    ep = coreconf.save({"name": "Mine", "base_url": fc.url, "model_id": "fake-model"})
    try:
        m = install.install_model(mf.parse_bytes(json.dumps(model).encode()), core_endpoint=ep["id"])
    finally:
        fc.close()
    assert m["core"]["endpoint_id"] == ep["id"] and m["encoder"] and not seen(host, "test-core-4b-Q4_K_M")


def test_the_studio_payload_layout_is_what_import_yaob_expects(studio_home, tmp_path):
    """`mpn network import-yaob <repo>/studio` works on the payload as published: studio/network/yaob-n10k.sqlite + studio/network/checkpoint/."""
    for p, b in relhost.studio_payload().items():
        (tmp_path / "repo" / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "repo" / p).write_bytes(b)
    n = networks.import_yaob_release(tmp_path / "repo" / "studio", "From the layout")
    assert n["nodes"] == 20 and n["router"] and n["router"]["id"] == "ck-test-0001"


def test_components_in_the_websites_exact_shape_resolve_by_id_then_repo_url(host, studio_home):
    """role (not kind), id, name, version, repo_url, optional nodes/parameters/bytes/download; each component is an entry of mpns[] / cores[]."""
    mpn, core, enc = parts(host)
    entries = {"mpns": [relhost.as_site(mpn, host)], "cores": [relhost.as_site(core, host), relhost.as_site(enc, host)]}
    for e in entries["mpns"] + entries["cores"]:          # an entry names its repository in links.huggingface
        e["links"]["huggingface"] = f"https://huggingface.co/{next(m for m in (mpn, core, enc) if m['id'] == e['id'])['hosting']['primary']['repo']}"
    sm = relhost.site_model(host, mpn, core, enc)
    assert [c["role"] for c in sm["model"]["components"]] == ["mpn", "core", "encoder"] and "kind" not in sm["model"]["components"][0]
    sm["model"]["components"][0]["nodes"] = 42
    sm["model"]["components"][1]["parameters"] = 4_000_000_000
    serve_doc(host, relhost.site_doc(host, models=[sm], **entries))
    source.refresh()
    r = source.find("model", "site-pairing", "1.0.0")
    assert [r["detail"][k]["id"] for k in ("mpn", "core", "encoder")] == [mpn["id"], core["id"], enc["id"]]
    m = install.install_model(r)
    assert m["encoder"]["id"] and m["core"]["id"] and m["network"]["id"]
    # no id on the components: matched by the repository they name
    for i in install.installed():
        install.uninstall(i["kind"], i["id"], i["version"], i.get("format"))
    sm2 = relhost.site_model(host, mpn, core, enc, rid="by-repo", by_repo_url=True)
    serve_doc(host, relhost.site_doc(host, models=[sm2], **entries))
    source.refresh()
    r2 = source.find("model", "by-repo", "1.0.0")
    assert r2["detail"]["mpn"]["id"] is None and r2["detail"]["mpn"]["repo_url"].startswith("https://huggingface.co/")
    assert install.install_model(r2)["encoder"]["id"]
