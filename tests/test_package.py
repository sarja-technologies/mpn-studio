"""Yaob packages (fylume-package/1): checksums verified, network and GGUF files registered where they are, the pairing created."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import relhost
from mpnstudio.catalogue import install, package
from mpnstudio.library import cores, models


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def make_package(root: Path, configuration="C4B-N119M", studio=False, encoder=False, fg=True, version="1.0.0") -> Path:
    """A package folder as the packaging workstream writes it. `studio`: the N10K layout (Studio payload and router checkpoint besides the FG-MPN store)."""
    root.mkdir(parents=True)
    files: dict[str, bytes] = {}
    core = relhost.tiny_gguf(name="core-4b", pad=300_000)
    files["cognitive-core/core-4b-Q8_0.gguf"] = core
    if encoder:
        files["encoder/enc-F16.gguf"] = relhost.tiny_gguf(arch="bert", name="enc", pooling=True, pad=100_000)
    mpn_files: dict[str, bytes] = {}
    if fg:
        mpn_files.update({f"store/{k}": v for k, v in relhost.fg_store().items()})
    if studio:
        mpn_files.update(relhost.studio_payload())
    for k, v in mpn_files.items():
        files[f"mpn/{k}"] = v
    files["README.md"] = b"# package\n"
    for rel, b in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    m = {"schema": "fylume-package/1", "family": "yaob-next", "configuration": configuration, "version": version,
         "core": {"id": "qwen3.5-4b-q8_0-gguf", "version": "1.0.0", "repo": "fylume/qwen3.5-4b-q8_0-gguf", "revision": "a" * 40, "file": "cognitive-core/core-4b-Q8_0.gguf",
                  "sha256": sha(core), "bytes": len(core), "parameters": 4_000_000_000, "quant": "Q8_0"},
         "mpn": {"id": "yaob-origin-n119m" if not studio else "yaob-next-n10k", "version": "1.0.0", "repo": "fylume/yaob-origin-n119m", "revision": "b" * 40,
                 "format": "studio-store" if studio else "fgmpn", "path": "mpn", "nodes": 42,
                 "files": [{"path": k, "bytes": len(v), "sha256": sha(v)} for k, v in mpn_files.items()]},
         "runtime": {"studio": "0.5.0", "fgmpn": "0.2.2", "llama_cpp": "b12318"}, "checksums": "checksums.sha256"}
    if encoder:
        e = files["encoder/enc-F16.gguf"]
        m["encoder"] = {"id": "qwen3-embedding-0.6b-f16-gguf", "version": "1.0.0", "file": "encoder/enc-F16.gguf", "sha256": sha(e), "bytes": len(e), "quant": "F16"}
    (root / "manifest.json").write_text(json.dumps(m, indent=1))
    (root / "checksums.sha256").write_text("".join(f"{sha(b)}  {rel}\n" for rel, b in files.items()))
    return root


def tree_state(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


def test_an_fgmpn_package_is_registered_where_it_is_and_gets_its_pairing(studio_home, tmp_path):
    pkg = make_package(tmp_path / "yaob-next-c4b-n119m")
    before = tree_state(pkg)
    r = package.import_package(pkg)
    assert r["verified"] == "full" and not r["already"] and not r["extracted"]
    rec = install.get_installed("mpn", "yaob-origin-n119m", "1.0.0", "fgmpn")
    assert Path(rec["store_path"]) == (pkg / "mpn" / "store").resolve() and rec["verified"] == "size" and rec["store"]["manifest_sha256"] and rec["package"]["configuration"] == "N119M-C4B"
    core = next(c for c in cores.list_cores() if c["kind"] == "core")
    assert core["path"] == str((pkg / "cognitive-core" / "core-4b-Q8_0.gguf").resolve()) and core["source"]["how"] == "reference"       # in place, not copied
    lib = Path(cores.library_dir())
    assert not list(lib.rglob("model.gguf"))
    assert sum(p.stat().st_size for p in (studio_home / "data").rglob("*") if p.is_file()) < 200_000                                  # no gigabytes anywhere in Studio's folder
    m = r["pairing"]
    assert m["network"] == {"release": {"id": "yaob-n119m-mpn", "version": "1.0.0", "format": "fgmpn"}} and m["core"]["id"] == core["id"] and m["encoder"] is None
    assert m["model_id"] == "yaob-next-n119m-c4b" and m["release"]["package"]["configuration"] == "N119M-C4B"
    assert m["status"]["state"] == "needs build" and not m["status"]["problems"]
    assert tree_state(pkg) == before                                                                     # the package folder was only read


def test_importing_the_same_package_again_finds_its_pairing(studio_home, tmp_path):
    pkg = make_package(tmp_path / "p")
    first = package.import_package(pkg)
    again = package.import_package(pkg)
    assert again["already"] and again["pairing"]["id"] == first["pairing"]["id"] and len(models.list_models()) == 1
    assert len([c for c in cores.list_cores() if c["kind"] == "core"]) == 1 and len(install.installed("mpn")) == 1


def test_an_n10k_package_registers_the_studio_payload_for_answering_and_the_store_to_explore(studio_home, tmp_path):
    pkg = make_package(tmp_path / "n10k", configuration="C4B-N10K", studio=True, encoder=True)
    r = package.import_package(pkg, verify="size")
    kinds = {(x["kind"], x.get("format")) for x in r["registered"]}
    assert ("network", "studio-store") in kinds and ("network", "fgmpn") in kinds and ("encoder", None) in kinds and ("core", None) in kinds
    m = r["pairing"]
    assert m["network"].get("id") and m["encoder"] and m["encoder"]["id"]                              # a Studio network: the router and the encoder as before
    assert not (m["network"].get("release"))
    from mpnstudio.library import networks
    assert networks.router(m["network"]["id"])["encoder"]["sha256"] == cores.get(m["encoder"]["id"])["sha256"]
    assert install.get_installed("mpn", "yaob-next-n10k", "1.0.0", "fgmpn")["store_path"].endswith("n10k/mpn/store")


def test_a_damaged_file_is_found_by_the_checksums_and_nothing_is_registered(studio_home, tmp_path):
    pkg = make_package(tmp_path / "p")
    seg = next((pkg / "mpn" / "store" / "segments").glob("*"))
    b = bytearray(seg.read_bytes())
    b[100] ^= 1
    seg.write_bytes(bytes(b))
    with pytest.raises(package.PackageError) as e:
        package.import_package(pkg)
    assert "does not verify" in str(e.value) and "SHA-256 differs" in str(e.value) and e.value.code == "verify"
    assert install.installed() == [] and cores.list_cores() == [] and models.list_models() == []
    # the quick check cannot see a same-size flip (it says so in the record); it does see a missing or short file
    r = package.import_package(pkg, verify="size", pairing=False)
    assert r["verified"] == "size"
    seg.write_bytes(b"short")
    with pytest.raises(package.PackageError) as e2:
        package.import_package(pkg, verify="size")
    assert "bytes, the manifest says" in str(e2.value)


def test_missing_files_wrong_schema_and_unsafe_paths_are_refused(studio_home, tmp_path):
    pkg = make_package(tmp_path / "p")
    m = json.loads((pkg / "manifest.json").read_text())
    (pkg / "cognitive-core" / "core-4b-Q8_0.gguf").unlink()
    with pytest.raises(package.PackageError) as e:
        package.import_package(pkg)
    assert "missing" in str(e.value)
    bad = dict(m, schema="fylume-package/2")
    (pkg / "manifest.json").write_text(json.dumps(bad))
    with pytest.raises(package.PackageError) as e:
        package.import_package(pkg)
    assert "fylume-package/1" in str(e.value)
    esc = json.loads(json.dumps(m))
    esc["core"]["file"] = "../outside.gguf"
    (pkg / "manifest.json").write_text(json.dumps(esc))
    with pytest.raises(package.PackageError) as e:
        package.import_package(pkg)
    assert "relative path" in str(e.value)
    new = json.loads(json.dumps(m))
    new["runtime"]["studio"] = "9.0.0"
    (pkg / "manifest.json").write_text(json.dumps(new))
    with pytest.raises(package.PackageError) as e:
        package.import_package(pkg)
    assert "9.0.0" in str(e.value)
    with pytest.raises(package.PackageError):
        package.import_package(tmp_path / "nothing")
    assert install.installed() == [] and cores.list_cores() == []


def test_a_zip_is_extracted_once_into_studios_folder_and_used_there(studio_home, tmp_path):
    pkg = make_package(tmp_path / "src" / "pkg")
    z = tmp_path / "yaob-next-c4b-n119m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(pkg.rglob("*")):
            if p.is_file():
                zf.write(p, "pkg/" + p.relative_to(pkg).as_posix())            # one top folder, as `zip -r` of a folder makes it
    r = package.import_package(z)
    dest = Path(studio_home) / "data" / "packages" / "yaob-next-c4b-n119m"
    assert r["extracted"] and r["path"] == str(dest.resolve()) and (dest / "manifest.json").is_file()
    rec = install.get_installed("mpn", "yaob-origin-n119m", "1.0.0", "fgmpn")
    assert Path(rec["store_path"]) == (dest / "mpn" / "store").resolve()
    assert r["pairing"]["status"]["state"] == "needs build"
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", (pkg / "manifest.json").read_text())
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(package.PackageError) as e:
        package.import_package(evil)
    assert "leaves the package folder" in str(e.value) and not (tmp_path / "escape.txt").exists()
    with pytest.raises(package.PackageError):
        package.import_package(tmp_path / "x.tar")


def test_not_enough_free_space_to_extract_is_said_before_anything_is_written(studio_home, tmp_path, monkeypatch):
    pkg = make_package(tmp_path / "src" / "pkg")
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in pkg.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(pkg).as_posix())
    from mpnstudio.catalogue import download
    monkeypatch.setattr(download, "free_bytes", lambda p: 1000)
    with pytest.raises(package.PackageError) as e:
        package.import_package(z)
    assert e.value.code == "disk_space" and not (Path(studio_home) / "data" / "packages").exists() or not list((Path(studio_home) / "data" / "packages").iterdir())


def test_the_api_and_the_cli_import_a_package(studio_home, tmp_path):
    from fastapi.testclient import TestClient
    from mpnstudio.api.app import create_app
    pkg = make_package(tmp_path / "p")
    c = TestClient(create_app())
    ins = c.post("/api/packages/inspect", json={"path": str(pkg)}).json()
    assert ins["ok"] and ins["configuration"] == "N119M-C4B" and ins["mpn"]["format"] == "fgmpn" and ins["bytes"] > 300_000
    assert c.post("/api/packages/inspect", json={"path": "relative/path"}).status_code == 400
    t = wait(c, c.post("/api/packages/import", json={"path": str(pkg), "verify": "size"}).json())
    assert t["state"] == "done", t
    assert t["result"]["pairing"]["status"]["state"] == "needs build"
    t2 = wait(c, c.post("/api/packages/import", json={"path": str(tmp_path / "missing")}).json())
    assert t2["state"] == "failed" and "No such file" in t2["error"]


def wait(c, t, secs=60):
    import time
    end = time.time() + secs
    while t["state"] in ("running", "queued") and time.time() < end:
        time.sleep(0.05)
        t = c.get(f"/api/library/tasks/{t['id']}").json()
    return t


def test_the_cli_imports_a_package(studio_home, tmp_path, capsys):
    from mpnstudio import localcli
    pkg = make_package(tmp_path / "p")
    localcli.main(["package", "inspect", str(pkg)])
    assert json.loads(capsys.readouterr().out)["ok"]
    localcli.main(["package", "import", str(pkg), "--verify", "size"])
    out = capsys.readouterr().out
    assert '"pairing"' in out and "needs build" in out
    with pytest.raises(SystemExit) as e:
        localcli.main(["package", "import", str(tmp_path / "nope")])
    assert "error:" in str(e.value)
