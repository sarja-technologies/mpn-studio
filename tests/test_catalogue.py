"""The catalogue: reading fylume-release/1 manifests, the configurable source, a cached copy, imported files."""
import json

import pytest

import relhost
from mpnstudio import __version__
from mpnstudio.catalogue import manifest as mf, source
from mpnstudio.desktop import settings


def test_parse_valid_mpn_core_and_model(host):
    mpn = relhost.mpn_release(host)
    core = relhost.core_release(host)
    model = relhost.model_release(host, mpn, core)
    for m, kind in ((mpn, "mpn"), (core, "core"), (model, "model")):
        r = mf.parse(m)
        assert r["kind"] == kind and r["problems"] == [] and r["compat"]["ok"], (kind, r["problems"], r["compat"])
    assert mf.parse(mpn)["totals"]["file_count"] == 4 and mf.parse(mpn)["files"][0]["sha256"]


def test_parse_reports_every_missing_piece(host):
    m = relhost.mpn_release(host)
    m["licence"] = {}
    m["files"][0]["sha256"] = "abc"
    m["files"][1]["path"] = "../escape"
    m["hosting"]["primary"]["revision"] = "main"
    r = mf.parse(m)
    text = " | ".join(r["problems"])
    assert "licence is not stated" in text and "sha256" in text and "unsafe" in text and "commit sha" in text
    assert not r["compat"]["ok"]


@pytest.mark.parametrize("bad", [{"schema": "fylume-release/2"}, {"schema": "fylume-release/1", "kind": "dataset"}, []])
def test_parse_rejects_what_is_not_a_release(bad):
    with pytest.raises(mf.ManifestError):
        mf.parse(bad)


def test_compatibility_studio_min_and_fgmpn_version(host):
    r = mf.parse(relhost.mpn_release(host, detail={"studio_min": "9.0.0"}))
    assert not r["compat"]["ok"] and "9.0.0" in r["compat"]["reasons"][0] and __version__ in r["compat"]["reasons"][0]
    r = mf.parse(relhost.mpn_release(host, detail={"format_version": "0.3.1"}))
    assert any("0.3.1" in x and "0.2.x" in x for x in r["compat"]["reasons"])
    r = mf.parse(relhost.mpn_release(host, detail={"manifest_format": "fgmpn-manifest/9"}))
    assert any("fgmpn-manifest/9" in x for x in r["compat"]["reasons"])
    r = mf.parse(relhost.mpn_release(host, detail={"format": "lance"}))
    assert any("'lance'" in x for x in r["compat"]["reasons"])


def test_version_comparison_ignores_pre_release_suffix():
    assert mf.version_ge("0.5.0.dev0", "0.5.0") and mf.version_ge("0.10.0", "0.9.9") and not mf.version_ge("0.4.1", "0.5.0")


def test_core_without_gguf_or_adapter_is_not_installable_here(host):
    c = relhost.core_release(host)
    c["core"]["formats"] = [{"kind": "safetensors", "quant": "bf16", "file": "x.safetensors"}]
    assert "no GGUF" in " ".join(mf.parse(c)["compat"]["reasons"])
    c = relhost.core_release(host)
    c["core"]["derivation"] = "adapter"
    assert "adapter" in " ".join(mf.parse(c)["compat"]["reasons"])


def test_default_url_setting_and_override(studio_home, monkeypatch):
    assert source.url() == source.DEFAULT_URL
    settings.update({"catalogue": {"url": "https://example.test/c.json"}})
    assert source.url() == "https://example.test/c.json"
    monkeypatch.setenv("MPN_STUDIO_CATALOGUE_URL", "file:///x.json")
    assert source.url() == "file:///x.json"
    with pytest.raises(ValueError):
        settings.update({"catalogue": {"url": "ftp://nope"}})


def _serve_index(host, entries, path="/catalogue/index.json"):
    host.files[path] = json.dumps({"schema": "fylume-catalogue/1", "generated_at": "2026-11-22", "releases": entries}).encode()
    return host.url + path


def test_refresh_reads_inline_and_linked_manifests_and_caches(host):
    mpn, core = relhost.mpn_release(host), relhost.core_release(host)
    host.files["/m/core.json"] = json.dumps(core).encode()
    url = _serve_index(host, [mpn, {"manifest_url": host.url + "/m/core.json", "manifest_sha256": relhost.sha(host.files["/m/core.json"])}])
    settings.update({"catalogue": {"url": url}})
    assert source.load()["releases"] == []                         # nothing is fetched until asked
    r = source.refresh()
    assert r["releases"] == 2 and r["skipped"] == []
    got = source.load()
    assert {x["kind"] for x in got["releases"]} == {"mpn", "core"} and got["source"]["fetched_at"]
    linked = next(x for x in got["releases"] if x["kind"] == "core")
    assert linked["manifest_sha256"] == relhost.sha(host.files["/m/core.json"])
    host.close()                                                   # the site goes away: the last good copy is still listed
    assert len(source.load()["releases"]) == 2


def test_linked_manifest_with_the_wrong_checksum_is_skipped_not_trusted(host):
    core = relhost.core_release(host)
    host.files["/m/core.json"] = json.dumps(core).encode()
    settings.update({"catalogue": {"url": _serve_index(host, [{"manifest_url": host.url + "/m/core.json", "manifest_sha256": "0" * 64}])}})
    r = source.refresh()
    assert r["releases"] == 0 and "SHA-256" in r["skipped"][0]


def test_unreachable_or_wrong_catalogue_says_why(host):
    settings.update({"catalogue": {"url": host.url + "/nothing.json"}})
    with pytest.raises(source.CatalogueError) as e:
        source.refresh()
    assert "could not be read" in str(e.value)
    host.files["/x.json"] = b"{not json"
    settings.update({"catalogue": {"url": host.url + "/x.json"}})
    with pytest.raises(source.CatalogueError) as e:
        source.refresh()
    assert "not JSON" in str(e.value)
    host.files["/y.json"] = json.dumps({"hello": 1}).encode()
    settings.update({"catalogue": {"url": host.url + "/y.json"}})
    with pytest.raises(source.CatalogueError) as e:
        source.refresh()
    assert "not a Fylume catalogue" in str(e.value)


def test_cache_of_another_address_is_not_shown(host):
    settings.update({"catalogue": {"url": _serve_index(host, [relhost.mpn_release(host)])}})
    source.refresh()
    settings.update({"catalogue": {"url": "https://elsewhere.test/c.json"}})
    assert source.load()["releases"] == []


def test_import_manifest_file_and_folder_and_remove(host, studio_home):
    m = relhost.core_release(host)
    f = studio_home / "fylume-release.json"
    f.write_bytes(json.dumps(m).encode())
    added = source.import_file(f)
    assert added[0]["id"] == m["id"] and added[0]["problems"] == []
    got = source.load()["releases"]
    assert got[0]["origin"] == "file" and got[0]["manifest_sha256"] == relhost.sha(f.read_bytes())      # kept byte for byte
    assert source.import_file(studio_home)[0]["id"] == m["id"]                                       # a folder with fylume-release.json
    assert source.remove_local("core", m["id"], m["version"]) and source.load()["releases"] == []
    with pytest.raises(source.CatalogueError):
        source.import_file(studio_home / "missing.json")
    (studio_home / "bad.json").write_text("[1,2]")
    with pytest.raises(source.CatalogueError):
        source.import_file(studio_home / "bad.json")


def test_invalid_entries_stay_listed_with_their_problems_not_hidden(host, studio_home):
    m = relhost.core_release(host)
    m["licence"] = {}
    (studio_home / "m.json").write_text(json.dumps(m))
    source.import_file(studio_home / "m.json")
    r = source.load()["releases"][0]
    assert "the licence is not stated" in r["problems"]


def test_a_catalogue_index_in_a_local_file(host, studio_home):
    idx = studio_home / "idx.json"
    idx.write_text(json.dumps({"schema": "fylume-catalogue/1", "releases": [relhost.core_release(host), relhost.mpn_release(host)]}))
    settings.update({"catalogue": {"url": str(idx)}})
    assert source.refresh()["releases"] == 2 and len(source.load()["releases"]) == 2
