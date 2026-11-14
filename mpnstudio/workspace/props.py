"""Plain names for the Wikidata properties that appear as relationship and claim types in Yaob Origin N119M.

The network stores some relationships by property id ("P50"). The workspace shows the name ("author") and keeps the
id as `rel_id`, which is what edits use. Unknown ids are shown as they are.
"""
from __future__ import annotations

import re

_EXTRA = {
    "P1877": "after a work by", "P287": "designed by", "P5185": "grammatical gender", "P2860": "cites work",
    "P407": "language of work", "P495": "country of origin", "P364": "original language", "P123": "publisher",
    "P175": "performer", "P161": "cast member", "P58": "screenwriter", "P162": "producer", "P272": "production company",
    "P750": "distributed by", "P144": "based on", "P4969": "derivative work", "P179": "part of the series",
    "P1441": "present in work", "P674": "characters", "P26": "spouse", "P40": "child", "P22": "father", "P25": "mother",
    "P3373": "sibling", "P69": "educated at", "P108": "employer", "P166": "award received", "P102": "member of political party",
    "P1412": "languages spoken", "P150": "contains", "P5137": "item for this sense", "P6271": "demonym of",
    "P5972": "translation", "P1056": "product", "P176": "manufacturer", "P1433": "published in", "P921": "main subject",
    "P1343": "described by source", "P1269": "facet of", "P3095": "practiced by", "P2283": "uses", "P366": "has use",
    "P1535": "used by", "P425": "field of this occupation", "P1557": "manifestation of", "P5008": "on focus list",
    "P1424": "topic's main template", "P910": "topic's main category", "P1151": "topic's main Wikimedia portal",
    "P373": "Commons category", "P8": "", "P747": "has edition", "P629": "edition of", "P655": "translator",
    "P98": "editor", "P110": "illustrator", "P1080": "from fictional universe", "P1445": "fictional universe described in",
    "P1830": "owner of", "P127": "owned by", "P749": "parent organization", "P355": "subsidiary", "P1037": "director or manager",
    "P488": "chairperson", "P169": "chief executive officer", "P1327": "partner in business or sport", "P54": "member of sports team",
    "P413": "position played", "P641": "sport", "P118": "league", "P1532": "country for sport", "P937": "work location",
    "P551": "residence", "P1038": "relative", "P1050": "medical condition", "P509": "cause of death", "P1196": "manner of death",
    "P119": "place of burial", "P21": "sex or gender", "P103": "native language", "P1303": "instrument", "P264": "record label",
    "P358": "discography", "P412": "voice type", "P1411": "nominated for", "P185": "doctoral student", "P184": "doctoral advisor",
    "P1066": "student of", "P802": "student", "P1416": "affiliation", "P2650": "interested in", "P1830x": "",
    "P4224": "category contains", "P360": "is a list of", "P1963": "properties for this type", "P2184": "history of topic",
    "P5125": "Wikimedia outline", "P1343x": "", "P3342": "significant person", "P180": "depicts", "P195": "collection",
    "P608": "exhibition history", "P186": "made from material", "P2079": "fabrication method", "P1071": "location of creation",
    "P840": "narrative location", "P136x": "", "P437": "distribution format", "P400": "platform", "P408": "software engine",
    "P306": "operating system", "P348": "software version", "P2554": "production designer", "P344": "director of photography",
    "P1040": "film editor", "P86x": "", "P676": "lyrics by", "P87": "librettist", "P826": "tonality", "P1191": "date of first performance",
}


def _table() -> dict[str, str]:
    out = {}
    try:
        from ..corpus.fetch import PROPS
        out.update({k: v[0] for k, v in PROPS.items() if re.fullmatch(r"P\d+", k) and v[0]})
    except Exception:  # noqa: BLE001 - the corpus tools are optional; the extra table still applies
        pass
    out.update({k: v for k, v in _EXTRA.items() if re.fullmatch(r"P\d+", k) and v})
    return out


NAMES = _table()
_PID = re.compile(r"P\d+")


def name(rel: str | None) -> str | None:
    if rel and _PID.fullmatch(rel):
        return NAMES.get(rel, rel)
    return rel


def named(resp: dict) -> dict:
    """Relationship rows (`out`, `in`, `results`), graph `edges` and `claims` with property names instead of ids."""
    if not isinstance(resp, dict):
        return resp
    for key in ("out", "in", "results"):
        for r in resp.get(key) or []:
            if isinstance(r, dict) and "rel" in r:
                r.setdefault("rel_id", r["rel"])
                r["rel"] = name(r["rel"])
    for e in resp.get("edges") or []:
        if isinstance(e, dict) and "rel" in e:
            e["rel"] = name(e["rel"])
    for c in resp.get("claims") or []:
        if isinstance(c, dict) and "property" in c:
            c["property"] = name(c["property"])
    return resp
