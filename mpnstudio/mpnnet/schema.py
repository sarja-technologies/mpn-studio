# Vendored from the Yaob repository (mpn14b/schema.py at f8bd650, 2026-11-13). Read-only use: MPN Studio
# opens MPN network packs with it. Keep in step with the source; the mpn-network/1 JSON must stay identical.
"""MPN-14B node schema, version 1.

A node is one meaningful knowledge unit: a concept (entity), a claim (one sourced statement about a concept), a
lexeme or word sense, a scholarly work, a person, an organisation, a venue, a topic, a procedure, or a fact extracted
from text with a verbatim evidence quote. Nodes carry the three KTR cells of the existing Yaob 3CN schema
(knowledge, thinking, reasoning; see docs/formats.md), provenance, licence and confidence.

Identity is content-independent and source-independent: a node's id is derived from its canonical key
(`wd:Q42`, `doi:10.1000/xyz`, `orcid:0000-0002-...`), so the same thing arriving from two datasets resolves to one
node. Claims are co-located with their subject (the first byte of a claim id is the subject's first byte), so a
concept and all its claims live in one storage partition.
"""
from __future__ import annotations

import hashlib
import re
import time
import unicodedata

SCHEMA_VERSION = 1
FORMAT = "mpn-node/1"

KINDS = ["concept", "claim", "lexeme", "sense", "work", "person", "organisation", "venue", "topic", "procedure",
         "extracted_fact", "migrated"]
KIND_ID = {k: i for i, k in enumerate(KINDS)}

# Fixed domain vocabulary. Ids are stable: append only.
DOMAINS = ["general", "mathematics", "physics", "chemistry", "biology", "medicine", "history", "geography", "languages",
           "government", "economics", "engineering", "computing", "literature", "art_culture", "agriculture", "astronomy",
           "philosophy", "law", "earth_sciences", "social_sciences", "people", "organisations", "scholarly_works",
           "religion", "sport", "music", "film_media", "technology", "education", "transport", "psychology",
           "environment", "military"]
DOMAIN_ID = {d: i for i, d in enumerate(DOMAINS)}

STATUS = {"validated": 1, "quarantined": 2}
STATUS_NAME = {v: k for k, v in STATUS.items()}

# Licence ids. "public" says whether the content may go into a public pack.
LICENCES = {
    "CC0-1.0": {"id": 1, "public": True, "attribution": False, "share_alike": False},
    "CC-BY-SA-4.0": {"id": 2, "public": True, "attribution": True, "share_alike": True},
    "CC-BY-4.0": {"id": 3, "public": True, "attribution": True, "share_alike": False},
    "ODC-BY-1.0+third-party": {"id": 4, "public": False, "attribution": True, "share_alike": False},
    "Apache-2.0": {"id": 5, "public": True, "attribution": True, "share_alike": False},
    "unknown": {"id": 0, "public": False, "attribution": True, "share_alike": False},
}
LIC_ID = {k: v["id"] for k, v in LICENCES.items()}
LIC_NAME = {v["id"]: k for k, v in LICENCES.items()}
PUBLIC_LIC_IDS = {v["id"] for v in LICENCES.values() if v["public"]}

# Kinds that get a full-text entry (titles and aliases). Claims are reached through their subject.
SEARCHABLE = {KIND_ID[k] for k in ("concept", "lexeme", "sense", "work", "person", "organisation", "venue", "topic",
                                   "procedure", "extracted_fact", "migrated")}

_ws = re.compile(r"\s+")
_ctrl = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_lang = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{1,8})*$|^mul$|^und$")


def h16(key: str) -> bytes:
    return hashlib.blake2b(key.encode("utf-8"), digest_size=16).digest()


def node_id(key: str) -> bytes:
    return h16(key)


def claim_id(subject: bytes, key: str) -> bytes:
    """A claim lives in its subject's partition: same first byte."""
    return subject[:1] + h16(key)[1:]


def hexid(b: bytes) -> str:
    return b.hex()


def unhex(s: str) -> bytes:
    b = bytes.fromhex(s)
    if len(b) != 16:
        raise ValueError("node id must be 16 bytes (32 hex characters)")
    return b


def clean(s, limit: int = 2000) -> str:
    if s is None:
        return ""
    s = _ctrl.sub("", unicodedata.normalize("NFC", str(s)))
    s = _ws.sub(" ", s).strip()
    return s[:limit]


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if d.startswith(p):
            d = d[len(p):]
    return d if d.startswith("10.") and "/" in d else None


def norm_orcid(o: str | None) -> str | None:
    if not o:
        return None
    o = o.strip().rsplit("/", 1)[-1].upper()
    return o if re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", o) else None


def canonical_key(ids: dict) -> str:
    """One rule for every source, so the same thing gets the same id wherever it comes from."""
    for k in ("doi", "orcid", "wikidata", "ror", "issn_l", "openalex", "lexeme", "key"):
        v = ids.get(k)
        if v:
            return {"wikidata": "wd", "lexeme": "wdl"}.get(k, k) + ":" + str(v) if k != "key" else str(v)
    raise ValueError("record has no identifier")


class Invalid(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def validate(n: dict) -> dict:
    """Deterministic validation. Raises Invalid(reason). Returns the node, normalised."""
    if n.get("kind") not in KIND_ID:
        raise Invalid("unknown kind")
    if not n.get("key") or len(n["key"]) > 400:
        raise Invalid("missing or oversized key")
    t = clean(n.get("title"), 500)
    if not t:
        raise Invalid("no title or label")
    n["title"] = t
    if n.get("summary"):
        n["summary"] = clean(n["summary"], 4000)
    lang = n.get("lang") or "und"
    if not _lang.match(lang):
        raise Invalid("bad language tag")
    n["lang"] = lang
    if n.get("domain", "general") not in DOMAIN_ID:
        raise Invalid("unknown domain")
    p = n.get("prov") or {}
    if not p.get("dataset") or not p.get("record"):
        raise Invalid("no provenance")
    if n.get("licence") not in LIC_ID or n["licence"] == "unknown":
        raise Invalid("unknown licence")
    c = n.get("cells")
    if not isinstance(c, dict) or not any(c.get(x) for x in ("knowledge", "thinking", "reasoning")):
        raise Invalid("empty KTR cells")
    if n["kind"] == "claim":
        f = (c.get("knowledge") or {}).get("facts") or []
        if not f or not n.get("subject"):
            raise Invalid("claim without subject or fact")
    if n["kind"] == "extracted_fact":
        ev = (n.get("evidence") or {}).get("quote")
        if not ev:
            raise Invalid("extracted fact without evidence quote")
    conf = float(n.get("conf", 0.5))
    if not 0.0 <= conf <= 1.0:
        raise Invalid("confidence out of range")
    n["conf"] = conf
    return n


def now() -> int:
    return int(time.time())
