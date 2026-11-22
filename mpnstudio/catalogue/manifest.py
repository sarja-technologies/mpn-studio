"""A `fylume-release/1` manifest, checked and reduced to what Studio relies on.

Fields Studio reads (everything else is kept in `raw` and ignored):

  schema, kind (mpn | core | model), id, name, version, released_at
  licence.spdx (or a plain string), licence.notes
  hosting.primary {type: huggingface | github | https, repo | base_url, revision | tag}, hosting.mirrors[]
  files[] {path, bytes, sha256, role}
  mpn   {format, format_version, manifest_format, generation, nodes, relationships, read_only, studio_min}
  core  {parameters, architecture, derivation, formats[{kind, quant, file}], api, context_length, base_model}
  model {pairing, mpn{id, version, repo, revision, manifest_sha256}, core{..., file}, encoder, defaults, runtime.studio_min}
"""
from __future__ import annotations

import hashlib
import re

from .. import __version__

SCHEMA = "fylume-release/1"
KINDS = ("mpn", "core", "model")
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX_REV = re.compile(r"^[0-9a-f]{7,40}$")

# What this Studio can read. FG-MPN stores are read-only, and the reader accepts one format version family.
FGMPN_VERSIONS = ("0.2",)
FGMPN_MANIFESTS = ("fgmpn-manifest/1", "fgmpn-manifest/2")
MPN_FORMATS = ("fgmpn", "studio-store")
# fgmpn: a read-only store Studio explores. studio-store: what `mpn network import-yaob` expects, `studio/network/yaob-n10k.sqlite` plus the router
# checkpoint directory `studio/network/checkpoint/` (so `mpn network import-yaob <repo>/studio` works as is); the only payload a pairing can answer from. A release may carry both (`mpn.formats`) and each
# file says which payload it belongs to (`files[].format`); a file with no format (or a descriptive label) is documentation.
STUDIO_NETWORK = "studio/network/yaob-n10k.sqlite"
STUDIO_CHECKPOINT = "studio/network/checkpoint/"


class ManifestError(ValueError):
    pass


def version_tuple(v: str | None) -> tuple[int, ...]:
    """'0.5.0', '0.5.0.dev0' and 'v0.5' compare by their numeric release part."""
    m = re.match(r"^v?(\d+(?:\.\d+)*)", str(v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def version_ge(a: str | None, b: str | None) -> bool:
    ta, tb = version_tuple(a), version_tuple(b)
    n = max(len(ta), len(tb))
    return ta + (0,) * (n - len(ta)) >= tb + (0,) * (n - len(tb))


def safe_path(p) -> bool:
    if not isinstance(p, str) or not p or p.startswith("/") or "\\" in p or "\x00" in p:
        return False
    parts = p.split("/")
    return all(x not in ("", ".", "..") for x in parts)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _licence(m: dict) -> dict:
    lic = m.get("licence")
    if isinstance(lic, str):
        lic = {"spdx": lic}
    lic = dict(lic or {})
    return {"spdx": (lic.get("spdx") or "").strip() or None, "notes": lic.get("notes")}


def _hosting(m: dict, problems: list[str], need_revision: bool = True) -> dict:
    h = m.get("hosting") or {}
    prim = h.get("primary")
    if not isinstance(prim, dict) or prim.get("type") not in ("huggingface", "github", "https"):
        problems.append("hosting.primary is missing or has an unknown type")
        prim = None
    else:
        t = prim["type"]
        if t == "huggingface":
            if not prim.get("repo") or "/" not in str(prim["repo"]):
                problems.append("hosting.primary.repo must be '<org>/<repo>'")
            if need_revision and not HEX_REV.match(str(prim.get("revision") or "")):
                problems.append("hosting.primary.revision must be a commit sha (the download would not be reproducible)")
        elif t == "github":
            if not prim.get("repo") or not prim.get("tag"):
                problems.append("hosting.primary needs repo and tag")
        elif not str(prim.get("base_url") or "").startswith(("http://", "https://")):
            problems.append("hosting.primary.base_url must be an http(s) address")
    mirrors = [x for x in (h.get("mirrors") or []) if isinstance(x, dict) and x.get("type") in ("huggingface", "github", "https")]
    return {"primary": prim, "mirrors": mirrors}


def _files(m: dict, problems: list[str], required: bool) -> list[dict]:
    out, seen = [], set()
    raw = m.get("files")
    if not isinstance(raw, list) or (required and not raw):
        if required:
            problems.append("files is missing or empty")
        return []
    for f in raw:
        if not isinstance(f, dict):
            problems.append("a files entry is not an object")
            continue
        p, n, s = f.get("path"), f.get("bytes"), str(f.get("sha256") or "").lower()
        if not safe_path(p):
            problems.append(f"unsafe or missing file path {p!r}")
        elif p in seen:
            problems.append(f"{p} is listed twice")
        elif not isinstance(n, int) or isinstance(n, bool) or n < 0:
            problems.append(f"{p}: bytes must be a whole number")
        elif not HEX64.match(s):
            problems.append(f"{p}: sha256 must be 64 hex digits")
        else:
            seen.add(p)
            fmt = f.get("format") if f.get("format") in MPN_FORMATS else None
            urls = [u for u in [f.get("url"), *(f.get("mirrors") or [])] if isinstance(u, str) and u.startswith(("http://", "https://"))]
            out.append({"path": p, "bytes": n, "sha256": s, "role": f.get("role") or "data", "format": fmt, "urls": urls})
    return out


def formats_of(detail: dict) -> list[str]:
    """Payloads an mpn release carries: `mpn.formats`, or just `mpn.format` (primary first)."""
    fs = [x for x in (detail.get("formats") or []) if isinstance(x, str)]
    if detail.get("format") and detail["format"] not in fs:
        fs.insert(0, detail["format"])
    return fs


def store_prefix(files: list[dict]) -> str | None:
    """The folder of an FG-MPN payload that holds the store (`CURRENT`, `manifests/`, `segments/`): "" when it is the release's root, "store"
    when the repository keeps it under store/ beside the Studio payload. None when there is no CURRENT file."""
    cur = sorted((f["path"] for f in files if f["path"] == "CURRENT" or f["path"].endswith("/CURRENT")), key=len)
    return cur[0][: -len("CURRENT")].rstrip("/") if cur else None


def payloads(detail: dict, files: list[dict]) -> list[dict]:
    """One entry per payload of an mpn release: {format, ok, reason, files}. `ok` says whether this Studio can use it."""
    fs = formats_of(detail)
    tagged = {f["format"] for f in files if f.get("format")}
    out = []
    for fmt in fs:
        mine = [f for f in files if f.get("format") == fmt]
        if not mine and len(fs) == 1 and not tagged:          # a release with one payload need not tag its files
            mine = [f for f in files if f.get("role") not in ("docs", "checksum")]
        reason = None
        if fmt not in MPN_FORMATS:
            reason = f"This Studio does not read the network format {fmt!r}."
        elif fmt == "fgmpn":
            fv = ".".join(str(detail.get("format_version") or "").split(".")[:2])
            mf = detail.get("manifest_format")
            if fv not in FGMPN_VERSIONS:
                reason = f"This network was written by FG-MPN {detail.get('format_version')}; this Studio reads FG-MPN {', '.join(v + '.x' for v in FGMPN_VERSIONS)}."
            elif mf and mf not in FGMPN_MANIFESTS:
                reason = f"Its store manifest format {mf} is not one this Studio reads."
            elif store_prefix(mine) is None:
                reason = "Its FG-MPN payload has no CURRENT file."
        elif fmt == "studio-store":
            if not any(f["path"].endswith((".sqlite", ".db")) for f in mine):
                reason = "Its Studio payload has no network file (studio/network/yaob-n10k.sqlite)."
        out.append({"format": fmt, "ok": reason is None, "reason": reason, "files": mine, "bytes": sum(f["bytes"] for f in mine)})
    return out


def parse(m: dict) -> dict:
    """Validate one manifest. Returns the normalised release; `problems` lists why it cannot be installed, and `compat`
    says whether this Studio can use it. Never raises for a bad release, so one broken entry does not hide the rest."""
    problems: list[str] = []
    if not isinstance(m, dict):
        raise ManifestError("A release manifest must be a JSON object.")
    if m.get("schema") != SCHEMA:
        raise ManifestError(f"Not a {SCHEMA} manifest (schema is {m.get('schema')!r}).")
    kind = m.get("kind")
    if kind not in KINDS:
        raise ManifestError(f"Unknown release kind {kind!r}; expected mpn, core or model.")
    rid = str(m.get("id") or "")
    if not SLUG.match(rid):
        problems.append("id is missing or not a lowercase slug")
    if not str(m.get("version") or "").strip():
        problems.append("version is missing")
    if not str(m.get("name") or "").strip():
        problems.append("name is missing")
    lic = _licence(m)
    if not lic["spdx"]:
        problems.append("the licence is not stated")
    # a pairing downloads nothing from its own repository (it pins its parts), so it may be imported before that repository has a commit
    host = _hosting(m, problems, need_revision=kind != "model")
    files = _files(m, problems, required=kind in ("mpn", "core"))
    totals = {"download_bytes": sum(f["bytes"] for f in files), "file_count": len(files),
              "installed_bytes": (m.get("totals") or {}).get("installed_bytes")}
    detail = m.get(kind) if isinstance(m.get(kind), dict) else {}
    if not detail:
        problems.append(f"the '{kind}' section is missing")
    reasons: list[str] = []     # valid, but this Studio cannot use it
    pl: list[dict] = []
    if kind == "mpn" and detail:
        pl = payloads(detail, files)
        if not pl:
            reasons.append("It has no network format Studio reads.")
        elif not any(x["ok"] for x in pl):
            reasons.extend(x["reason"] for x in pl)
        smin = detail.get("studio_min")
        if smin and not version_ge(__version__, smin):
            reasons.append(f"It needs MPN Studio {smin} or newer; this is {__version__}.")
    if kind == "core" and detail:
        fm = [f for f in (detail.get("formats") or []) if isinstance(f, dict)]
        gg = [f for f in fm if f.get("kind") == "gguf"]
        names = {f["path"] for f in files}
        for f in gg:
            if f.get("file") not in names:
                problems.append(f"format {f.get('quant')}: file {f.get('file')!r} is not in files")
        if not gg:
            reasons.append("It has no GGUF file for Studio to serve; use it from a server you run and add that server as a core.")
        if detail.get("derivation") == "adapter":
            reasons.append("It is an adapter. It needs its base model; serve the combination yourself and add that server as a core.")
    if kind == "model" and detail:
        for part in ("mpn", "core"):
            p = detail.get(part)
            if not isinstance(p, dict) or not p.get("id") or not p.get("version"):
                problems.append(f"model.{part} must pin an id and a version")
            elif not HEX64.match(str(p.get("manifest_sha256") or "").lower()):
                problems.append(f"model.{part}.manifest_sha256 is missing")
        enc = detail.get("encoder")
        if enc is not None and (not isinstance(enc, dict) or not enc.get("id") or not enc.get("version")
                                or not HEX64.match(str(enc.get("manifest_sha256") or "").lower())):
            problems.append("model.encoder must pin an id, a version and a manifest_sha256 (or be null)")
        smin = (detail.get("runtime") or {}).get("studio_min")
        if smin and not version_ge(__version__, smin):
            reasons.append(f"It needs MPN Studio {smin} or newer; this is {__version__}.")
    return {"id": rid, "kind": kind, "name": m.get("name") or rid, "version": str(m.get("version") or ""), "released_at": m.get("released_at"),
            "licence": lic, "hosting": host, "files": files, "totals": totals, "detail": detail, "problems": problems,
            "compat": {"ok": not reasons and not problems, "reasons": reasons}, "payloads": pl, "manifest_sha256": None, "raw": m}


def parse_bytes(raw: bytes) -> dict:
    import json
    try:
        m = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ManifestError(f"The manifest is not valid JSON: {e}")
    r = parse(m)
    r["manifest_sha256"] = sha256_bytes(raw)
    return r
