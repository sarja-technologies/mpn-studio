"""Downloading and installing releases: resumable, size- and SHA-256-checked, atomic; prebuilt pairings; errors a person can act on."""
import json
import os
import shutil
import threading

import pytest

import relhost
from mpnstudio.catalogue import download, install, manifest as mf, source
from mpnstudio.catalogue.download import DownloadError
from mpnstudio.desktop import paths
from mpnstudio.library import cores, models


def parse(m):
    return mf.parse_bytes(json.dumps(m).encode())


def seen(host, fragment):
    return [r for r in host.requests if fragment in r[0]]


# ------------------------------------------------------------------------------------------------ files
def test_fetch_file_verifies_and_names_atomically(host, studio_home):
    data = os.urandom(300_000)
    host.put("r", "a" * 40, "f.bin", data)
    url = f"{host.url}/{relhost.ORG}/r/resolve/{'a' * 40}/f.bin"
    out = download.fetch_file([url], studio_home / "d" / "f.bin", len(data), relhost.sha(data))
    assert out.read_bytes() == data and not (studio_home / "d" / "f.bin.part").exists()


def test_corrupted_download_is_deleted_not_installed(host, studio_home):
    data = os.urandom(100_000)
    host.put("r", "a" * 40, "f.bin", data)
    host.corrupt.add(host.path("r", "a" * 40, "f.bin"))
    url = f"{host.url}{host.path('r', 'a' * 40, 'f.bin')}"
    with pytest.raises(DownloadError) as e:
        download.fetch_file([url], studio_home / "f.bin", len(data), relhost.sha(data))
    assert e.value.code == "checksum_mismatch" and "SHA-256" in str(e.value)
    assert not (studio_home / "f.bin").exists() and not (studio_home / "f.bin.part").exists()


def test_a_mirror_is_used_when_the_primary_copy_is_damaged(host, studio_home):
    data = os.urandom(50_000)
    host.put("r", "a" * 40, "f.bin", data)
    host.put("m", "b" * 40, "f.bin", data)
    host.corrupt.add(host.path("r", "a" * 40, "f.bin"))
    urls = [host.url + host.path("r", "a" * 40, "f.bin"), host.url + host.path("m", "b" * 40, "f.bin")]
    assert download.fetch_file(urls, studio_home / "f.bin", len(data), relhost.sha(data)).read_bytes() == data


def test_interrupted_download_resumes_with_a_range_request(host, studio_home):
    data = os.urandom(900_000)
    host.put("r", "a" * 40, "f.bin", data)
    p = host.path("r", "a" * 40, "f.bin")
    host.cut[p] = 400_000                                           # the connection drops after 400,000 bytes, once
    out = download.fetch_file([host.url + p], studio_home / "f.bin", len(data), relhost.sha(data))
    assert out.read_bytes() == data
    ranges = [r[1] for r in host.requests if r[0] == p]
    start = int(ranges[1].split("=")[1].rstrip("-"))
    assert ranges[0] is None and 0 < start <= 400_000              # the second request asked only for the rest, from what had arrived


def test_resume_after_cancel_keeps_the_part_and_finishes(host, studio_home):
    data = os.urandom(2_000_000)
    host.put("r", "a" * 40, "f.bin", data)
    url = host.url + host.path("r", "a" * 40, "f.bin")
    cancel = threading.Event()
    got = [0]

    def tick(n):
        got[0] += n
        if got[0] > 500_000:
            cancel.set()
    with pytest.raises(DownloadError) as e:
        download.fetch_file([url], studio_home / "f.bin", len(data), relhost.sha(data), tick, cancel)
    assert e.value.code == "cancelled"
    part = studio_home / "f.bin.part"
    kept = part.stat().st_size
    assert 0 < kept < len(data)
    assert download.fetch_file([url], studio_home / "f.bin", len(data), relhost.sha(data)).read_bytes() == data
    assert host.requests[-1][1] == f"bytes={kept}-"


def test_a_server_that_ignores_range_restarts_cleanly(host, studio_home):
    data = os.urandom(300_000)
    host.put("r", "a" * 40, "f.bin", data)
    p = host.path("r", "a" * 40, "f.bin")
    (studio_home / "f.bin.part").write_bytes(data[:100_000])
    host.no_range = True
    assert download.fetch_file([host.url + p], studio_home / "f.bin", len(data), relhost.sha(data)).read_bytes() == data


def test_a_stale_part_with_wrong_bytes_is_caught_by_the_hash(host, studio_home):
    data = os.urandom(300_000)
    host.put("r", "a" * 40, "f.bin", data)
    p = host.path("r", "a" * 40, "f.bin")
    (studio_home / "f.bin.part").write_bytes(b"\0" * 100_000)       # a leftover from some other file
    with pytest.raises(DownloadError) as e:
        download.fetch_file([host.url + p], studio_home / "f.bin", len(data), relhost.sha(data))
    assert e.value.code == "checksum_mismatch"
    assert download.fetch_file([host.url + p], studio_home / "f.bin", len(data), relhost.sha(data)).read_bytes() == data     # next try starts clean


def test_wrong_size_is_refused(host, studio_home):
    data = os.urandom(10_000)
    host.put("r", "a" * 40, "f.bin", data)
    p = host.path("r", "a" * 40, "f.bin")
    with pytest.raises(DownloadError) as e:
        download.fetch_file([host.url + p], studio_home / "f.bin", len(data) - 5, relhost.sha(data))
    assert e.value.code == "size_mismatch"


@pytest.mark.parametrize("status,code", [(404, "not_found"), (401, "auth"), (403, "auth"), (500, "http")])
def test_http_errors_are_named(host, studio_home, status, code):
    p = host.path("r", "a" * 40, "f.bin")
    host.files[p] = b"x"
    host.status[p] = status
    with pytest.raises(DownloadError) as e:
        download.fetch_file([host.url + p], studio_home / "f.bin", 1, relhost.sha(b"x"))
    assert e.value.code == code


def test_unreachable_server_says_so(studio_home):
    with pytest.raises(DownloadError) as e:
        download.fetch_file(["http://127.0.0.1:9/f.bin"], studio_home / "f.bin", 1, "0" * 64, retries=1)
    assert e.value.code == "unreachable" and "127.0.0.1" in str(e.value)


# ------------------------------------------------------------------------------------------------ install a network
def test_install_fgmpn_release_verifies_every_file_and_lists_it(host, studio_home):
    r = parse(relhost.mpn_release(host))
    prog = []
    rec = install.install(r, lambda label, d, t: prog.append((d, t)))
    assert rec["format"] == "fgmpn" and rec["nodes"] == 42 and rec["store"]["nodes"] == 42 and rec["read_only"]
    assert (install.store_dir(rec) / "CURRENT").is_file()
    assert prog[-1][0] <= prog[-1][1] and prog[0][1] == r["totals"]["download_bytes"]
    assert [x["id"] for x in install.installed("mpn")] == [r["id"]]
    assert not list((paths.data_dir() / "releases" / "mpn" / r["id"]).glob("*.partial"))           # nothing half-installed remains
    again = install.install(r)                                                                      # idempotent: no second download
    n = len(host.requests)
    install.install(r)
    assert len(host.requests) == n and again["installed_at"] == rec["installed_at"]


def test_install_with_a_damaged_file_installs_nothing(host, studio_home):
    m = relhost.mpn_release(host)
    bad = host.path("yaob-test-fg", m["hosting"]["primary"]["revision"], "segments/seg-000001.fgs")
    host.corrupt.add(bad)
    with pytest.raises(DownloadError) as e:
        install.install(parse(m))
    assert e.value.code == "checksum_mismatch"
    assert install.installed() == [] and not (paths.data_dir() / "releases" / "mpn" / "yaob-test-fg" / "1.0.0").exists()


def test_interrupted_install_resumes_from_the_partial_folder(host, studio_home):
    m = relhost.mpn_release(host)
    seg = host.path("yaob-test-fg", m["hosting"]["primary"]["revision"], "segments/seg-000001.fgs")
    cancel = threading.Event()

    def tick(label, done, total):
        if done > 100_000:
            cancel.set()
    with pytest.raises(DownloadError) as e:
        install.install(parse(m), tick, cancel)
    assert e.value.code == "cancelled" and install.installed() == []
    stage = paths.data_dir() / "releases" / "mpn" / "yaob-test-fg" / "1.0.0.partial"
    assert stage.exists()
    plan = install.plan(parse(m))
    assert 0 < plan["remaining_bytes"] < plan["download_bytes"]
    host.requests.clear()
    rec = install.install(parse(m))
    assert rec["store"]["segments"] == 2
    assert any(r[1] and r[1].startswith("bytes=") for r in seen(host, "seg-000001"))        # the interrupted file continued, it did not restart


def test_install_refuses_when_the_disk_is_too_small(host, studio_home, monkeypatch):
    monkeypatch.setattr(download, "free_bytes", lambda p: 1_000_000)
    monkeypatch.setattr(install, "free_bytes", lambda p: 1_000_000)
    with pytest.raises(DownloadError) as e:
        install.install(parse(relhost.mpn_release(host)))
    assert e.value.code == "disk_space" and "more disk space" in str(e.value) and e.value.status == 507
    assert host.requests == [] and install.installed() == []                                      # checked before downloading anything


def test_a_release_this_studio_cannot_read_is_refused_with_the_reason(host, studio_home):
    for detail, words in (({"studio_min": "9.0"}, "9.0"), ({"format_version": "0.3.0"}, "0.3.0"), ({"format": "lance"}, "lance")):
        with pytest.raises(install.InstallError) as e:
            install.install(parse(relhost.mpn_release(host, detail=detail)))
        assert e.value.code == "incompatible" and words in str(e.value)


def test_missing_component_is_reported_before_install(host, studio_home):
    files = relhost.fg_store()
    del files["CURRENT"]
    m = relhost.release("mpn", "no-current", files, host, detail={"format": "fgmpn", "format_version": "0.2.2", "manifest_format": "fgmpn-manifest/2", "nodes": 42})
    with pytest.raises(install.InstallError) as e:
        install.install(parse(m))
    assert e.value.code in ("missing_component", "incompatible") and "CURRENT" in str(e.value)
    assert install.installed() == []


def test_a_segment_listed_but_not_in_the_store_manifest_is_a_missing_component(host, studio_home):
    files = relhost.fg_store()
    del files["segments/seg-000002.fgs"]
    m = relhost.release("mpn", "short", files, host, detail={"format": "fgmpn", "format_version": "0.2.2", "manifest_format": "fgmpn-manifest/2", "nodes": 42})
    with pytest.raises(install.InstallError) as e:
        install.install(parse(m))
    assert "seg-000002.fgs" in str(e.value) and e.value.code == "missing_component"


def test_verify_installed_finds_a_changed_file(host, studio_home):
    rec = install.install(parse(relhost.mpn_release(host)))
    assert install.verify_installed("mpn", rec["id"], rec["version"])["ok"]
    seg = install.store_dir(rec) / "segments" / "seg-000002.fgs"
    b = bytearray(seg.read_bytes())
    b[100] ^= 1
    seg.write_bytes(bytes(b))
    v = install.verify_installed("mpn", rec["id"], rec["version"])
    assert not v["ok"] and "seg-000002.fgs" in " ".join(v["problems"])


def test_uninstall_moves_to_trash(host, studio_home):
    rec = install.install(parse(relhost.mpn_release(host)))
    out = install.uninstall("mpn", rec["id"], rec["version"])
    assert install.installed() == [] and os.path.exists(out["moved_to"])


def test_local_fgmpn_folder_is_verified_before_it_is_added(host, studio_home, tmp_path):
    root = tmp_path / "mystore"
    for p, b in relhost.fg_store().items():
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_bytes(b)
    rec = install.install_local_fgmpn(root, "My store")
    assert rec["origin"] == "local" and rec["nodes"] == 42 and install.store_dir(rec) == root.resolve()
    seg = root / "segments" / "seg-000001.fgs"
    seg.write_bytes(seg.read_bytes()[:-1] + b"X")
    with pytest.raises(install.InstallError) as e:
        install.install_local_fgmpn(root, "Damaged")
    assert e.value.code in ("verify", "missing_component") and len(install.installed()) == 1
    with pytest.raises(install.InstallError):
        install.install_local_fgmpn(tmp_path, "Not a store")


# ------------------------------------------------------------------------------------------------ install a core
def test_install_core_downloads_one_quant_into_the_library(host, studio_home):
    m = relhost.core_release(host, quants=("Q4_K_M", "Q8_0"))
    r = parse(m)
    p = install.plan(r, "Q8_0")
    assert p["files"] == 2 and p["download_bytes"] < r["totals"]["download_bytes"]
    rec = install.install(r, file="Q8_0")
    c = cores.get(rec["core_id"])
    assert c["present"] and c["info"]["architecture"] == "llama" and c["source"]["how"] == "moved" and c["release"]["quant"] == "Q8_0"
    assert not seen(host, "Q4_K_M")                                    # the other format was never requested
    assert not (paths.data_dir() / "releases" / "core" / r["id"] / "1.0.0" / "store").exists()      # no second copy of the weights
    assert install.verify_installed("core", r["id"], "1.0.0")["ok"]
    with pytest.raises(install.InstallError):
        install.plan(r, "Q2_K")


def test_core_that_is_not_a_gguf_is_refused_after_the_hash_check(host, studio_home):
    files = {"x.gguf": b"not a gguf at all" * 100}
    m = relhost.release("core", "fake-core", files, host, detail={"parameters": 1, "architecture": "x", "derivation": "base", "api": "openai-compatible",
                                                                  "formats": [{"kind": "gguf", "quant": "Q4", "file": "x.gguf"}]})
    with pytest.raises(install.InstallError) as e:
        install.install(parse(m))
    assert "cannot read it as a GGUF" in str(e.value) and e.value.code == "incompatible"


# ------------------------------------------------------------------------------------------------ prebuilt pairings
def _studio_store_release(host, tmp_path):
    import test_builder
    p = tmp_path / "n.sqlite"
    s = test_builder.seed(p, n=20)
    s.close()
    data = p.read_bytes()
    return relhost.release("mpn", "tiny-net", {"tiny-net.sqlite": data}, host,
                           detail={"format": "studio-store", "nodes": 20, "read_only": False, "studio_min": "0.5.0"})


def test_install_prebuilt_pairing_installs_both_and_refers_to_them(host, studio_home, tmp_path):
    mpn = _studio_store_release(host, tmp_path)
    core = relhost.core_release(host)
    model = relhost.model_release(host, mpn, core)
    m = install.install_model(parse(model))
    assert m["release"]["pairing"] == "N10K-C4B" and m["status"]["state"] == "needs build"
    assert m["network"]["id"] and m["core"]["id"]
    assert models.get(m["id"])["core"]["name"]
    assert {r["kind"] for r in install.installed()} == {"mpn", "core"}
    # the installed network is an ordinary network of its own: it stays usable with any other core
    from mpnstudio.library import networks
    nets = networks.list_networks()
    assert [n["name"] for n in nets] == ["Tiny Net"] and nets[0]["nodes"] == 20
    other = models.create("Same network, my own core", "", None, nets[0]["id"], None)
    assert other["network"]["id"] == nets[0]["id"]


def test_a_pinned_manifest_may_leave_its_own_revision_empty_and_a_pairing_may_not_have_one_yet(host, studio_home, tmp_path):
    """A manifest cannot name the commit that holds it: the repository's manifest says revision null and the pairing's pin supplies it. The same
    goes for the pairing's own repository, which has no commit until after the assembly test."""
    mpn = _studio_store_release(host, tmp_path)
    core = relhost.core_release(host)
    model = relhost.model_release(host, mpn, core)
    for m, key in ((mpn, "mpn"), (core, "core")):
        doc = json.loads(json.dumps(m))
        pin = model["model"][key]
        doc["hosting"]["primary"]["revision"] = None
        raw = json.dumps(doc).encode()
        host.put(pin["repo"].split("/")[1], pin["revision"], "fylume-release.json", raw)
        pin["manifest_sha256"] = relhost.sha(raw)
    model["hosting"]["primary"]["revision"] = None
    r = parse(model)
    assert r["problems"] == []
    m = install.install_model(r)
    assert m["release"]["pairing"] == "N10K-C4B" and m["network"]["id"] and m["core"]["id"]


def test_prebuilt_pairing_with_my_own_server_downloads_no_core(host, studio_home, tmp_path):
    from fakecore import FakeCore
    from mpnstudio.library import coreconf
    mpn = _studio_store_release(host, tmp_path)
    model = relhost.model_release(host, mpn, relhost.core_release(host))
    fc = FakeCore()
    ep = coreconf.save({"name": "My server", "base_url": fc.url, "model_id": "fake-model"})
    try:
        m = install.install_model(parse(model), core_endpoint=ep["id"])
    finally:
        fc.close()
    assert m["core"]["endpoint_id"] == ep["id"] and [r["kind"] for r in install.installed()] == ["mpn"]
    assert not seen(host, ".gguf")


def test_pairing_is_refused_when_a_pinned_manifest_is_not_the_pinned_bytes(host, studio_home, tmp_path):
    mpn = _studio_store_release(host, tmp_path)
    core = relhost.core_release(host)
    model = relhost.model_release(host, mpn, core)
    # someone re-pushed the repository: the manifest at that revision is no longer what the pairing pinned
    repo, rev = mpn["hosting"]["primary"]["repo"].split("/")[1], mpn["hosting"]["primary"]["revision"]
    host.put(repo, rev, "fylume-release.json", json.dumps({**mpn, "version": "1.0.0", "name": "Tampered"}).encode())
    with pytest.raises(install.InstallError) as e:
        install.install_model(parse(model))
    assert e.value.code == "checksum_mismatch" and "does not match the checksum" in str(e.value)
    assert install.installed() == []


def test_pairing_with_a_component_that_cannot_be_found(host, studio_home, tmp_path):
    mpn = _studio_store_release(host, tmp_path)
    model = relhost.model_release(host, mpn, relhost.core_release(host))
    host.files.pop(host.path("tiny-net", mpn["hosting"]["primary"]["revision"], "fylume-release.json"))
    with pytest.raises(install.InstallError) as e:
        install.install_model(parse(model))
    assert e.value.code == "unreachable" and "tiny-net" in str(e.value)


def test_pairing_that_would_not_fit_downloads_nothing(host, studio_home, tmp_path, monkeypatch):
    mpn = _studio_store_release(host, tmp_path)
    model = relhost.model_release(host, mpn, relhost.core_release(host))
    monkeypatch.setattr(install, "free_bytes", lambda p: 10_000_000)
    monkeypatch.setattr(download, "free_bytes", lambda p: 10_000_000)
    monkeypatch.setattr(install, "MARGIN", 9_999_999)
    with pytest.raises(DownloadError):
        install.install_model(parse(model))
    assert not seen(host, ".sqlite") and not seen(host, ".gguf")


def test_pairing_found_in_the_catalogue_needs_no_second_manifest_download(host, studio_home, tmp_path):
    mpn = _studio_store_release(host, tmp_path)
    core = relhost.core_release(host)
    model = relhost.model_release(host, mpn, core)
    for m in (mpn, core):                                        # imported byte for byte: their checksums are the pinned ones
        f = studio_home / f"{m['id']}.json"
        f.write_bytes(json.dumps(m).encode())
        source.import_file(f)
    assert relhost.sha(json.dumps(mpn).encode()) == model["model"]["mpn"]["manifest_sha256"]      # the pin is those same bytes
    host.requests.clear()
    m = install.install_model(parse(model))
    assert not seen(host, "fylume-release.json") and m["status"]["state"] == "needs build"
