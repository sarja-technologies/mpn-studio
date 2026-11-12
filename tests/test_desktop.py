"""Desktop layer: XDG paths, projects, backup/restore, migration, relocation, session token, project switching."""
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mpnstudio.desktop import paths, projects, settings
from mpnstudio.store import relocate
from mpnstudio.store.db import Store


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("MPN_STUDIO_CONFIG_DIR", "MPN_STUDIO_DATA_DIR", "MPN_STUDIO_CACHE_DIR", "MPN_STUDIO_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    paths.ensure()
    return tmp_path


def test_xdg_defaults_and_overrides(home, monkeypatch):
    assert paths.config_dir() == home / "home/.config/mpn-studio"
    assert paths.data_dir() == home / "home/.local/share/mpn-studio"
    assert paths.cache_dir() == home / "home/.cache/mpn-studio"
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg-data"))
    assert paths.data_dir() == home / "xdg-data/mpn-studio"
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")           # invalid per spec: ignored
    assert paths.data_dir() == home / "home/.local/share/mpn-studio"


def test_create_import_backup_restore_trash(home):
    slug = projects.create("Test Net")
    assert slug == "test-net" and (paths.projects_dir() / slug / "project.json").exists()
    m = projects.read_manifest(slug)
    assert m["format"] == projects.FORMAT and m["architecture"] == "Modular Persistent Network"
    assert m["legacy_architecture_name"] == "Modular Parameter Network"
    assert projects.create("Test Net") == "test-net-2"

    # import an existing store file in WAL mode with an uncheckpointed write: the copy must include it
    src = home / "src.sqlite"
    st = Store(src)
    st.conn().execute("INSERT INTO settings VALUES('marker', '\"x\"')")
    imp = projects.import_store_file(src, "Imported")
    c = sqlite3.connect(projects.store_path(imp))
    assert c.execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0] == '"x"'
    assert projects.read_manifest(imp)["origin"]["kind"] == "store-file"
    with pytest.raises(projects.ProjectError):
        projects.import_store_file(home / "nope.sqlite", "x")
    bad = home / "bad.sqlite"
    sqlite3.connect(bad).execute("create table t(a)").connection.commit()
    with pytest.raises(projects.ProjectError):
        projects.import_store_file(bad, "bad")
    assert not projects.project_dir("bad").exists()                 # failed import leaves nothing behind

    b = projects.backup(slug)
    assert projects.verify_backup(b)["project"] == slug
    r = projects.restore(b, "Restored copy")
    assert r == "restored-copy" and projects.store_schema_version(projects.store_path(r)) is not None
    # tampered backup is refused
    raw = b.read_bytes()
    bad_b = b.with_name("20000101-000000.mpnbak")
    bad_b.write_bytes(raw[:-40] + b"\0" * 40)
    with pytest.raises(projects.ProjectError):
        projects.verify_backup(bad_b)
    moved = projects.trash(slug)
    assert moved.exists() and not projects.project_dir(slug).exists()
    assert {p["id"] for p in projects.list_projects()} >= {"test-net-2", "imported", "restored-copy"}


def test_manifest_migration_backs_up_first(home):
    slug = projects.create("Old")
    d = projects.project_dir(slug)
    m = json.loads((d / "project.json").read_text())
    for k in ("format", "architecture", "legacy_architecture_name", "references"):
        m.pop(k, None)                                              # shape of a manifest from before the rename
    m["architecture"] = "Modular Parameter Network"
    (d / "project.json").write_text(json.dumps(m))
    assert projects.list_backups(slug) == []
    out = projects.prepare_open(slug)
    assert out["architecture"] == "Modular Persistent Network" and out["format"] == projects.FORMAT
    assert len(projects.list_backups(slug)) == 1                    # backup made before modifying
    assert out["migrations"][0]["changes"]
    assert projects.read_manifest(slug)["architecture"] == "Modular Persistent Network"


def test_newer_store_schema_is_refused(home):
    slug = projects.create("Future")
    c = sqlite3.connect(projects.store_path(slug))
    c.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    c.commit()
    c.close()
    with pytest.raises(projects.ProjectError, match="newer"):
        projects.prepare_open(slug)


def test_checkpoint_relocation(tmp_path, monkeypatch):
    ck = tmp_path / "mounted/banks/main/checkpoints/ck-1"
    ck.mkdir(parents=True)
    (ck / "manifest.json").write_text("{}")
    recorded = "/home/user/mpn-studio/data/banks/main/checkpoints/ck-1"      # a path that only existed on another machine
    monkeypatch.delenv("MPN_CHECKPOINT_ROOTS", raising=False)
    assert relocate.resolve_checkpoint(recorded, "ck-1") is None
    monkeypatch.setenv("MPN_CHECKPOINT_ROOTS", str(tmp_path / "mounted"))
    assert relocate.resolve_checkpoint(recorded, "ck-1") == ck
    assert relocate.resolve_checkpoint(str(ck), "ck-1") == ck


@pytest.fixture
def client(home, monkeypatch):
    from mpnstudio.api import app as appmod
    monkeypatch.setattr(appmod, "DESKTOP", True)
    monkeypatch.setattr(appmod, "SESSION_TOKEN", "s3cret")
    app = appmod.create_app()
    c = TestClient(app, headers={"x-mpn-token": "s3cret"})
    return c


def test_token_and_no_project_state(client):
    assert TestClient(client.app).get("/api/health").status_code == 401        # no token: refused
    assert client.get("/api/health").json()["project"] is None
    r = client.get("/api/board")
    assert r.status_code == 409 and r.json()["code"] == "no_project"
    assert client.get("/api/desktop/info").json()["project"] is None


def test_project_switching_and_persistence(client):
    a = client.post("/api/desktop/projects", json={"name": "Alpha"}).json()["id"]
    assert client.get("/api/board").status_code == 200
    n = client.post("/api/mpns", json={"name": "History"})
    assert n.status_code == 200, n.text
    head_a = client.get("/api/health").json()["head"]
    b = client.post("/api/desktop/projects", json={"name": "Beta"}).json()["id"]
    assert client.get("/api/health").json()["project"] == b
    assert client.get("/api/health").json()["head"] < head_a                    # different store
    client.post(f"/api/desktop/projects/{a}/open")
    assert client.get("/api/health").json()["head"] == head_a                   # alpha's edit persisted
    assert client.get("/api/desktop/info").json()["settings"]["last_project"] == a
    client.post("/api/desktop/project/save")
    assert projects.read_manifest(a)["summary"]["head"] == head_a
    exp = client.post("/api/desktop/project/export", json={"dest": str(paths.exports_dir() / "alpha-bundle")})
    assert exp.status_code == 200, exp.text
    imp = client.post("/api/desktop/projects/import", json={"kind": "bundle", "path": exp.json()["path"], "name": "Alpha copy"})
    assert imp.status_code == 200, imp.text
    assert client.get("/api/mpns").json() is not None


def test_settings_validation(home):
    assert settings.load()["compute"]["mode"] == "local"
    with pytest.raises(ValueError):
        settings.update({"storage": {"models_dir": "relative/dir"}})
    with pytest.raises(ValueError):
        settings.update({"compute": {"mode": "cloud"}})
    s = settings.update({"compute": {"remote": {"ssh_host": "spark", "api_port": "8840"}}})
    assert s["compute"]["remote"]["api_port"] == 8840 and settings.load()["compute"]["remote"]["ssh_host"] == "spark"


def test_status_works_without_a_runtime(client):
    """Regression: a local helper named like the runtime-url helper once shadowed it and /api/status returned 500."""
    client.post("/api/desktop/projects", json={"name": "Status"})
    r = client.get("/api/status")
    assert r.status_code == 200, r.text
    assert r.json()["runtime"]["available"] is False


def test_desktop_window_origin_needs_the_token(home, monkeypatch):
    """The bundled UI (tauri://localhost) may call the API, but only with the per-launch token; preflight is answered."""
    from mpnstudio.api import app as appmod
    monkeypatch.setattr(appmod, "DESKTOP", True)
    monkeypatch.setattr(appmod, "SESSION_TOKEN", "s3cret")
    monkeypatch.setattr(appmod, "ALLOW_ORIGINS", ["tauri://localhost"])
    c = TestClient(appmod.create_app())
    pre = c.options("/api/desktop/projects", headers={"origin": "tauri://localhost", "access-control-request-method": "POST", "access-control-request-headers": "x-mpn-token,content-type"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "tauri://localhost"
    ok = c.post("/api/desktop/projects", json={"name": "From window"}, headers={"origin": "tauri://localhost", "x-mpn-token": "s3cret"})
    assert ok.status_code == 200, ok.text
    assert c.post("/api/desktop/projects", json={"name": "No token"}, headers={"origin": "tauri://localhost"}).status_code == 401
    assert c.post("/api/desktop/projects", json={"name": "Other site"}, headers={"origin": "https://evil.example", "x-mpn-token": "s3cret"}).status_code == 403


def test_jobs_are_refused_where_no_worker_exists(client):
    client.post("/api/desktop/projects", json={"name": "Jobs"})
    assert client.get("/api/jobs").json()["worker"]["available"] is False
    r = client.post("/api/jobs", json={"kind": "sleep", "params": {}})
    assert client.post("/api/jobs/260101-000000-abcdef/resume").status_code == 409
    assert r.status_code == 409 and "job worker" in r.json()["error"]
