"""The website's catalogue shape (`GET https://fylu.me/api/catalogue.json`, schema `fylume-catalogue/1`) read natively.

    {schema, updated_at, lang, models[], mpns[], apps[], cores[]}

Every entry becomes the same internal release record `manifest.parse` makes, so the rest of Studio does not care which shape a
release came from. What is read from an entry:

  id, name, version, status, released_at, summary, licence (free text), links{release, docs, huggingface, source},
  download{type, url, filename, bytes, sha256}, files[{name, url, bytes, sha256, role, format, mirrors}]
  mpns[].mpn    {nodes, relationships, format, formats, format_version, manifest_format, read_only, studio_min}
  cores[].core  {parameters, architecture, derivation, context_length, formats[{kind, quant, file}], base_model, fine_tuned}
  models[].model {pairing, mpn{name,nodes,version}, core{name,...}, components[{role: mpn|core|encoder, id, name, version, repo_url, download}], studio_min}

File addresses are the entry's own pinned `url`s (plus `mirrors`); a file is downloaded only with exact `bytes` and `sha256`.
`files[].format` is "fgmpn" or "studio-store" for payload files; any other value is a description of the file and is ignored.
An entry Studio cannot install (a legacy .tar.gz, a single .mpn bundle, a page) is kept and marked `download_only`: the Releases
view shows it with its download link. `apps[]` are not Studio's business and are skipped.
"""
from __future__ import annotations

from .. import __version__
from .. import names as pnames
from . import manifest as mf

KIND_KEYS = {"mpns": "mpn", "cores": "core", "models": "model"}
CATEGORY_KEYS = ("models", "mpns", "apps", "cores")


def is_site_doc(doc) -> bool:
    return isinstance(doc, dict) and doc.get("schema") == "fylume-catalogue/1" and any(isinstance(doc.get(k), list) for k in CATEGORY_KEYS)


def entries(doc: dict) -> list[tuple[str, dict]]:
    """(kind, entry) for every entry Studio can list. Entries that are not objects are skipped."""
    out = []
    for key, kind in KIND_KEYS.items():
        for e in doc.get(key) or []:
            if isinstance(e, dict):
                out.append((kind, e))
    return out


def _int(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _files(e: dict, problems: list[str], strict: bool) -> list[dict]:
    out, seen = [], set()
    for f in e.get("files") or []:
        if not isinstance(f, dict):
            continue
        name, n, sha = f.get("name"), _int(f.get("bytes")), str(f.get("sha256") or "").lower()
        role = f.get("role") or "data"
        if n is None or not mf.HEX64.match(sha):
            if strict and role not in ("checksum", "docs") and not f.get("optional"):
                problems.append(f"{name}: no exact size and SHA-256 in the catalogue")
            continue                                           # a file without them cannot be verified, so it is never downloaded
        if not mf.safe_path(name):
            if strict:
                problems.append(f"unsafe or missing file name {name!r}")
            continue
        if name in seen:
            continue
        seen.add(name)
        urls = [u for u in [f.get("url"), *(f.get("mirrors") or [])] if isinstance(u, str) and u.startswith(("http://", "https://"))]
        if strict and not urls:
            problems.append(f"{name}: no address")
        fmt = f.get("format") if f.get("format") in mf.MPN_FORMATS else None
        out.append({"path": name, "bytes": n, "sha256": sha, "role": role, "format": fmt, "urls": urls, "optional": bool(f.get("optional"))})
    return out


def _download_only(e: dict, why: str) -> dict:
    d = e.get("download") or {}
    links = e.get("links") or {}
    return {"reason": why, "url": d.get("url") or links.get("release"), "type": d.get("type"), "filename": d.get("filename"),
            "bytes": _int(d.get("bytes")) or _int(e.get("size_bytes")), "page": links.get("release") or links.get("docs")}


def release_of(kind: str, e: dict) -> dict:
    """The internal release record for one catalogue entry."""
    problems: list[str] = []
    reasons: list[str] = []
    rid = str(e.get("id") or "")
    if not mf.SLUG.match(rid):
        problems.append("id is missing or not a lowercase slug")
    version = str(e.get("version") or "").strip()
    if not version:
        problems.append("version is missing")
    lic_text = str(e.get("licence") or "").strip()
    block = e.get(kind) if isinstance(e.get(kind), dict) else {}
    detail: dict = dict(block)
    download_only = None
    strict = True

    if kind == "mpn":
        if not mf.formats_of(detail):
            download_only = _download_only(e, "Download only, not installable by Studio: a legacy archive, not an FG-MPN or Studio network.")
    elif kind == "model":
        comps = [c for c in (block.get("components") or []) if isinstance(c, dict)]
        if not comps:
            download_only = _download_only(e, "Download only, not installable by Studio: a complete model file or bundle, not a pairing of separate parts.")
        else:
            detail = {"pairing": block.get("pairing"), "components": comps, "runtime": {"studio_min": block.get("studio_min")}, "defaults": {},
                      **_pins(block, comps)}
    elif kind == "core":
        pass
    if pnames.is_retired(e.get("id")):       # the retired original (Yaob N10K 0.1.0) stays archived: a download, never an active install
        download_only = _download_only(e, "Download only, not installable by Studio: the original Yaob N10K (0.1.0) is retired and archived.")
    if download_only:
        strict = False
        reasons.append(download_only["reason"])
    files = _files(e, problems, strict)
    if kind == "mpn" and not download_only:
        pl = mf.payloads(detail, files)
        if not any(x["ok"] for x in pl):
            reasons.extend(x["reason"] for x in pl)
    else:
        pl = []
    if kind == "core" and not download_only:
        names = {f["path"] for f in files}
        detail["formats"] = [dict(f, file=_match_file(f.get("file"), names)) for f in detail.get("formats") or [] if isinstance(f, dict)]
        if not [f for f in detail["formats"] if f.get("kind") == "gguf" and f.get("file") in names]:
            reasons.append("It has no GGUF file for Studio to serve; use it from a server you run and add that server as a core.")
        if detail.get("derivation") == "adapter":
            reasons.append("It is an adapter. It needs its base model; serve the combination yourself and add that server as a core.")
    if not download_only and not lic_text:
        problems.append("the licence is not stated")
    smin = detail.get("studio_min") if kind == "mpn" else (detail.get("runtime") or {}).get("studio_min") if kind == "model" else None
    if smin and not mf.version_ge(__version__, smin):
        reasons.append(f"It needs MPN Studio {smin} or newer; this is {__version__}.")
    label = lic_text if len(lic_text) <= 48 else lic_text[:47].rstrip(" ,;:") + "…"
    totals = {"download_bytes": sum(f["bytes"] for f in files) if files else (_int(e.get("size_bytes")) or 0), "file_count": len(files),
              "installed_bytes": None}
    links = e.get("links") if isinstance(e.get("links"), dict) else {}
    return {"id": rid, "kind": kind, "name": _catalogue_name(e, rid, kind), "version": version, "released_at": e.get("released_at"),
            "licence": {"spdx": None, "notes": lic_text or None, "label": label or None}, "hosting": {"primary": {"type": "urls"}, "mirrors": []},
            "files": files, "totals": totals, "detail": detail, "problems": problems,
            "compat": {"ok": not reasons and not problems, "reasons": reasons}, "payloads": pl, "manifest_sha256": None,
            "download_only": download_only, "summary": e.get("summary"), "status": e.get("status"), "links": links, "shape": "site", "raw": e}


def _catalogue_name(e: dict, rid: str, kind: str = "") -> str:
    """The official spelling of the entry's name. A network is a component ("Yaob N10K network"), never a product name;
    the retired original is labelled as retired."""
    comp = pnames.component_id(rid) if kind == "mpn" else None
    if comp in pnames.COMPONENT_NAMES:
        return pnames.COMPONENT_NAMES[comp]
    name = pnames.display_name(e.get("name")) or rid
    return name + " (retired)" if pnames.is_retired(rid) else name


def _match_file(name, names: set[str]):
    if name in names:
        return name
    base = str(name or "").rsplit("/", 1)[-1]
    hit = [n for n in names if n.rsplit("/", 1)[-1] == base]
    return hit[0] if len(hit) == 1 else name


def _pins(block: dict, comps: list[dict]) -> dict:
    """The mpn, core and encoder a pairing is made of, from `components[]` (kind, id, name, version) and the summary blocks."""
    out: dict = {"mpn": None, "core": None, "encoder": None}
    for c in comps:
        k = str(c.get("role") or c.get("kind") or "").lower()
        k = {"network": "mpn", "cognitive core": "core", "embedding": "encoder", "retrieval encoder": "encoder"}.get(k, k)
        if k in out and out[k] is None:
            out[k] = {"id": c.get("id") or c.get("slug"), "name": c.get("name"), "version": c.get("version"), "repo_url": c.get("repo_url"),
                      "file": c.get("file") or (c.get("download") or {}).get("filename")}
    for k in ("mpn", "core"):
        b = block.get(k) if isinstance(block.get(k), dict) else None
        if out[k] is None and b:
            out[k] = {"id": b.get("id"), "name": b.get("name"), "version": b.get("version"), "file": None}
    return out
