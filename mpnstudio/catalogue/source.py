"""Where the catalogue comes from.

Setting `catalogue.url` (Settings, or settings.json; MPN_STUDIO_CATALOGUE_URL overrides) names a document in one of
these shapes, served over http(s) or read from a file:

  the website's own: {"schema": "fylume-catalogue/1", "updated_at", "lang", "models": [], "mpns": [], "apps": [], "cores": []}
                     (read natively, see site.py; the default, https://fylu.me/api/catalogue.json)
  {"schema": "fylume-catalogue/1", "generated_at": "...", "releases": [ <entry>, ... ]}
  [ <entry>, ... ]
  <entry>                                     (one bare release manifest)

where an <entry> is a `fylume-release/1` manifest, or {"manifest_url": "...", "manifest_sha256": "..."} that points at
one. The last good copy is kept in the cache, so the catalogue still lists releases when the site cannot be reached.
Manifests imported from files are kept in <data>/catalogue-local/ and listed beside the online ones.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..desktop import paths, settings
from . import manifest as mf
from . import site
from .download import DownloadError, fetch_bytes

DEFAULT_URL = "https://fylu.me/api/catalogue.json"
INDEX_SCHEMA = "fylume-catalogue/1"
MAX_ENTRIES = 2000


class CatalogueError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def url() -> str:
    return (os.environ.get("MPN_STUDIO_CATALOGUE_URL") or settings.load()["catalogue"].get("url") or DEFAULT_URL).strip()


def cache_file() -> Path:
    return paths.cache_dir() / "catalogue" / "index.json"


def local_dir() -> Path:
    return paths.data_dir() / "catalogue-local"


def _read(location: str) -> bytes:
    if location.startswith("file://"):
        location = location[7:]
    if location.startswith("/"):
        try:
            return Path(location).read_bytes()
        except OSError as e:
            raise CatalogueError(f"Cannot read {location}: {e.strerror or e}")
    return fetch_bytes(location)


ENTRY_SCHEMA = "fylume-catalogue-entry/1"       # one website entry kept from an imported catalogue file


def _entries(doc) -> list:
    if isinstance(doc, dict) and doc.get("schema") == INDEX_SCHEMA:
        r = doc.get("releases")
        if not isinstance(r, list):
            raise CatalogueError("The catalogue has no releases list.")
        return r
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict) and doc.get("schema") == mf.SCHEMA:
        return [doc]
    raise CatalogueError("That is not a Fylume catalogue or release manifest (expected schema fylume-catalogue/1 or fylume-release/1).")


def _resolve(entry) -> tuple[dict, str | None]:
    """An entry to (manifest, SHA-256 of its bytes when known). `manifest_url` entries are fetched and checked."""
    if isinstance(entry, dict) and entry.get("schema") == mf.SCHEMA:
        return entry, None
    if isinstance(entry, dict) and entry.get("manifest_url"):
        raw = _read(str(entry["manifest_url"]))
        want = str(entry.get("manifest_sha256") or "").lower()
        if want and mf.sha256_bytes(raw) != want:
            raise CatalogueError(f"{entry['manifest_url']} does not match the SHA-256 the catalogue lists for it.")
        return json.loads(raw), mf.sha256_bytes(raw)
    raise CatalogueError("An entry is neither a release manifest nor a manifest_url.")


def refresh() -> dict:
    """Fetch the catalogue now and keep it. Raises CatalogueError / DownloadError with a readable message when it cannot."""
    u = url()
    try:
        raw = _read(u)
    except DownloadError as e:
        raise CatalogueError(f"The catalogue at {u} could not be read: {e}", 502)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CatalogueError(f"The catalogue at {u} is not JSON.", 502)
    manifests, skipped, info = [], [], {}
    if site.is_site_doc(doc):
        info = {"updated_at": doc.get("updated_at"), "lang": doc.get("lang")}
        manifests = [{"site": {"kind": k, "entry": e}} for k, e in site.entries(doc)[:MAX_ENTRIES]]
    else:
        for e in _entries(doc)[:MAX_ENTRIES]:
            try:
                m, sha = _resolve(e)
                manifests.append({"manifest": m, "sha256": sha})
            except (CatalogueError, DownloadError, ValueError) as err:
                skipped.append(str(err))
    c = cache_file()
    c.parent.mkdir(parents=True, exist_ok=True)
    tmp = c.with_suffix(".tmp")
    tmp.write_text(json.dumps({"url": u, "fetched_at": time.time(), "skipped": skipped, "info": info, "manifests": manifests}, ensure_ascii=False))
    os.replace(tmp, c)
    return {"url": u, "releases": len(manifests), "skipped": skipped}


def import_file(path: str | Path) -> list[dict]:
    """Add the manifests in a file (or `fylume-release.json` in a folder) to the local catalogue. Returns what was added."""
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / "fylume-release.json"
    if not p.is_file():
        raise CatalogueError(f"No manifest file at {p}.")
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CatalogueError(f"{p.name} is not JSON.")
    added = []
    d = local_dir()
    if site.is_site_doc(doc):                       # the website's catalogue saved to a file: each entry is kept on its own
        for kind, e in site.entries(doc):
            r = site.release_of(kind, e)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"site-{kind}-{r['id'] or 'unnamed'}-{r['version'] or '0'}.json").write_text(json.dumps({"schema": ENTRY_SCHEMA, "kind": kind, "entry": e}, indent=1, ensure_ascii=False))
            added.append({"id": r["id"], "kind": kind, "version": r["version"], "problems": r["problems"], "download_only": bool(r["download_only"])})
        return added
    for e in _entries(doc):
        m, _sha = _resolve(e)
        r = mf.parse(m)                          # raises for a wrong schema or kind
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{r['kind']}-{r['id'] or 'unnamed'}-{r['version'] or '0'}.json".replace("/", "_")
        # a single manifest is kept byte for byte, so its SHA-256 is the one a pairing pins
        f.write_bytes(raw) if doc is e else f.write_text(json.dumps(m, indent=1, ensure_ascii=False))
        added.append({"id": r["id"], "kind": r["kind"], "version": r["version"], "problems": r["problems"]})
    return added


def remove_local(kind: str, rid: str, version: str) -> bool:
    f = local_dir() / f"{kind}-{rid}-{version}.json"
    if f.exists():
        f.unlink()
        return True
    return False


def load() -> dict:
    """The releases Studio knows about: the cached online catalogue plus imported manifests. Never touches the network."""
    out, rejected, seen = [], [], set()
    src = {"url": url(), "fetched_at": None, "updated_at": None, "error": None, "skipped": [], "local": 0}
    try:
        c = json.loads(cache_file().read_text())
        if c.get("url") == src["url"]:
            src.update(fetched_at=c.get("fetched_at"), skipped=c.get("skipped") or [], updated_at=(c.get("info") or {}).get("updated_at"))
            ms = [((x["site"] if "site" in x else x["manifest"]), "catalogue", x.get("sha256")) for x in c.get("manifests") or []]
        else:
            ms = []                                       # the cache belongs to another address
    except (OSError, ValueError):
        ms = []
    d = local_dir()
    if d.exists():
        for f in sorted(d.glob("*.json")):
            try:
                raw = f.read_bytes()
                ms.append((json.loads(raw), "file", mf.sha256_bytes(raw)))
                src["local"] += 1
            except (OSError, ValueError):
                continue
    for m, origin, sha in ms:
        try:
            if isinstance(m, dict) and "entry" in m and "kind" in m and (m.get("schema") in (None, ENTRY_SCHEMA)):
                r = site.release_of(m["kind"], m["entry"])           # a website entry
            else:
                r = mf.parse(m)
        except mf.ManifestError as e:
            rejected.append({"id": (m or {}).get("id") if isinstance(m, dict) else None, "reason": str(e)})
            continue
        key = (r["kind"], r["id"], r["version"])
        if key in seen:
            continue
        seen.add(key)
        r["origin"] = origin
        r["manifest_sha256"] = sha
        out.append(r)
    out.sort(key=lambda r: (r["kind"], r["name"].lower(), tuple(-x for x in mf.version_tuple(r["version"]))))
    return {"source": src, "releases": out, "rejected": rejected}


def find(kind: str | None, rid: str, version: str | None = None) -> dict | None:
    best = None
    for r in load()["releases"]:
        if r["id"] == rid and (kind is None or r["kind"] == kind):
            if version is not None and r["version"] == version:
                return r
            if version is None and (best is None or mf.version_tuple(r["version"]) > mf.version_tuple(best["version"])):
                best = r
    return best
