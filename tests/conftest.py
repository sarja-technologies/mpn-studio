import pytest


@pytest.fixture
def studio_home(tmp_path, monkeypatch):
    """An empty MPN Studio: its own config, data, cache and state folders, no network catalogue, HOME elsewhere."""
    for k in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"MPN_STUDIO_{k}_DIR", str(tmp_path / k.lower()))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "HF_ENDPOINT", "HF_TOKEN", "MPN_STUDIO_CATALOGUE_URL", "MPN_CORE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MPN_TEST_NO_SLEEP", "1")
    from mpnstudio.desktop import paths
    paths.ensure()
    return tmp_path


@pytest.fixture
def host(studio_home, monkeypatch):
    import relhost
    h = relhost.Host()
    monkeypatch.setenv("HF_ENDPOINT", h.url)
    yield h
    h.close()
