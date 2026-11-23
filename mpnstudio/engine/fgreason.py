"""Answering from a native FG-MPN network (for example the 119.7 million record Yaob Origin N119M).

The network is used where it is: its store is memory-mapped by the public FG-MPN runtime (`fgmpn`, v0.2.x), nothing is
converted, copied or embedded, and nothing here iterates the records. Every step goes through an index of the store
(exact-title index, text index, id and alias index, a subject's claim range). What a question needs is found like this:

  link      names in the question (spans of its words, longest first) are looked up by exact title; a name that has no exact
            title is searched in titles and aliases. Several records with one title are ranked by how many of the question's
            other words their description holds, then by kind, dataset and Wikipedia coverage.
  read      a linked record becomes a node: its description, and the facts of its validated claims (a value that is a bare
            Wikidata id, whose label the network does not hold, is left out because it cannot be read or cited).
  follow    relations are followed 1 to 2 hops: a fact whose value is another record leads to it when the relation's wording
            matches the question ("born" -> place of birth) or when the question asks for something the first record does not
            hold (the country of a place where someone was born). Fan-out and the number of nodes are bounded.
  supply    nodes are numbered [1], [2], ... in the order given to the core; a citation [n] maps to the node's id, key and
            provenance (dataset, record, revision, licence, url).

Status: a stored record is `validated` or `quarantined`. Quarantined records stay in the store and are returned, with their
status, by an explicit id. They are never found by title or search, never returned as a subject's claim, never followed from an
edge, never given to the core as evidence and so never cited. (`include_quarantined` exists only for inspection tools.)

There are no embeddings: retrieval is lexical entity linking over the store's own indexes.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

ENTITY_KINDS = ["concept", "person", "organisation", "venue", "topic", "migrated"]
KIND_PREF = {"migrated": 0, "concept": 0, "person": 0, "organisation": 0, "topic": 1, "venue": 1, "work": 2, "lexeme": 3, "sense": 3}
DATASET_RANK = {"yaob-n10k": 3, "wikidata": 2, "wikidata-lexemes": 1}
SUPPORTED = ("0.2",)

STOP = set("""what when who whom whose which where why how is was were are the a an in on of and or i please tell me about does did do
can could would should list name give explain describe its it his her their there this that these those with for from by to at as be
been has have had yes no some any many much more most find show know called known one also than then into over under not if so
year years born die died death birth briefly short answer""".split())
EDGE_STOP = STOP | {"in", "of", "and", "or", "for", "from", "by", "to", "at", "as", "with"}
CONNECT = {"of", "the", "de", "la", "le", "von", "van", "der", "den", "da", "di", "del", "and", "for", "in", "on", "at", "to", "a", "an", "du", "des", "el",
           "al", "bin", "ibn", "y", "e", "et", "und", "der", "zu", "af", "av", "ter", "ten", "dos", "das", "do", "lo", "los", "las"}
PAPER_WORDS = ("paper", "papers", "article", "articles", "study", "studies", "research", "journal", "published", "publication", "doi",
               "preprint", "citation", "who wrote the paper")
WID = re.compile(r"(?<![\w-])[QPL]\d{2,}(?:-[SF]\d+)?(?![\w-])")
HEX32 = re.compile(r"(?<![0-9a-fA-F])([0-9a-f]{32})(?![0-9a-fA-F])")
INSTRUCTION = re.compile(r"^(?:please\s+)?(?:answer|reply|respond|keep (?:it|the answer)|be (?:brief|concise|short)|give (?:a |me a )?(?:short|brief|one)|in (?:one|a few) words?)\b", re.I)

# a question word, and the property label words it stands for ("born" asks for "place of birth" or "date of birth")
SYN = {"born": "birth", "birthplace": "birth", "birthday": "birth", "died": "death", "dead": "death", "buried": "burial", "wrote": "author",
       "written": "author", "writer": "author", "directed": "director", "composed": "composer", "performed": "performer",
       "founded": "inception", "established": "inception", "founder": "founded", "created": "creator", "made": "creator",
       "citizen": "citizenship", "nationality": "citizenship", "employed": "employer", "worked": "employer", "educated": "educated",
       "studied": "educated", "headquarters": "headquarters", "capital": "capital", "published": "publication", "released": "publication",
       "located": "located", "lies": "located", "region": "administrative", "state": "administrative", "province": "administrative",
       "party": "party", "played": "team", "position": "position", "occupation": "occupation", "job": "occupation", "profession": "occupation"}
GENERIC = {"date", "place", "of", "the", "in", "and", "to", "for", "or", "on", "by"}
# facts of a record worth showing first when the question does not point at one (dates and places are what a second hop asks for)
FACT_FIRST = {p: i for i, p in enumerate(["P569", "P570", "P19", "P20", "P17", "P27", "P571", "P131", "P36", "P159", "P577", "P106", "P50", "P57", "P86",
                                          "P175", "P170", "P102", "P108", "P69", "P54", "P413", "P1532"])}
PREFER_LINK = {p: i for i, p in enumerate(["P19", "P20", "P131", "P17", "P27", "P50", "P57", "P86", "P175", "P170", "P36", "P159", "P749", "P127", "P361",
                                           "P706", "P30", "P26", "P22", "P25", "P69", "P108", "P463", "P102"])}
# values that are classes or bookkeeping, never the subject of a follow-up (instance of, sex, described by source, ...)
SKIP_LINKS = {"P31", "P279", "P21", "P1343", "P735", "P734", "P1412", "P103", "P910", "P373", "P407", "P1050", "P2670"}
LINK_RELATION_TEXT = re.compile(r"\b(?:the (?:place|city|town|country|region|state|company|film|book|work|team|party|school|university|"
                                r"author|director|composer|creator|founder|performer|father|mother|spouse|employer|capital) (?:where|of|in|that|which|whose)|"
                                r"(?:author|director|composer|creator|founder|performer|employer|capital|birthplace|country|father|mother|spouse) of)\b", re.I)

ASK_RULES = [   # (pattern, property labels the answer lives under)
    (r"\b(?:what year|which year|when|year)\b.*\b(?:born|birth)\b|\bdate of birth\b|\bbirth ?date\b|\bborn in what year\b", ["date of birth"]),
    (r"\b(?:what year|which year|when|year)\b.*\b(?:die|died|death)\b|\bdate of death\b", ["date of death"]),
    (r"\bwhere\b.*\b(?:born)\b|\bbirth ?place\b|\bplace of birth\b|\bborn where\b", ["place of birth"]),
    (r"\bwhere\b.*\b(?:die|died|death)\b|\bplace of death\b", ["place of death"]),
    (r"\bfounded\b|\bestablished\b|\binception\b", ["inception"]),
    (r"\bfirst published\b|\breleased\b|\bpublication date\b", ["publication date"]),
    (r"\bcountry\b", ["country", "country of citizenship", "country for sport"]),
    (r"\bcitizen", ["country of citizenship"]),
    (r"\badministrative\b|\bwhich region\b", ["located in the administrative territorial entity"]),
    (r"\bcapital\b", ["capital"]), (r"\bdirect", ["director"]), (r"\bauthor|\bwrote\b", ["author"]), (r"\bcompos", ["composer"]),
    (r"\bperform", ["performer"]), (r"\bcreat", ["creator"]), (r"\bparty\b", ["member of political party"]), (r"\bemployer\b", ["employer"]),
    (r"\beducated\b", ["educated at"]), (r"\bheadquarter", ["headquarters location"]), (r"\boccupation\b|\bprofession\b", ["occupation"]),
    (r"\bposition\b", ["position played on team / speciality"]), (r"\bteam\b", ["member of sports team"]),
]


QSTOP = STOP - {"born", "birth", "die", "died", "death", "year", "years"}        # words that name a relation or property count when a question is compared with one


def _stem(w: str) -> str:
    return SYN.get(w, w)[:5]


def words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[\wÀ-ɏ'-]+", (text or "").lower()) if w not in QSTOP and len(w) > 2}


def plain_words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[\wÀ-ɏ'-]+", (text or "").lower()) if w not in STOP and len(w) > 2}


def plain_question(question: str) -> str:
    """The question without its instruction ("Answer briefly.") and without descriptions in parentheses ("(German judge and politician)"): what it
    asks, not what it says about the entity it names."""
    q = strip_instructions(question)
    return re.sub(r"\s{2,}", " ", re.sub(r"\((?:[^()]*\([^()]*\))*[^()]*\)", lambda m: m.group(0) if len(m.group(0).split()) < 3 else " ", q)).strip()


def asked_properties(question: str) -> list[str]:
    q = question.lower()
    out: list[str] = []
    for pat, labs in ASK_RULES:
        if re.search(pat, q):
            out += [x for x in labs if x not in out]
    return out


class FgError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def runtime_ok() -> str | None:
    """None when the public FG-MPN runtime is usable, else why not."""
    try:
        import fgmpn
    except Exception as e:
        return f"The FG-MPN runtime (the fgmpn package) is not available: {e}"
    ver = ".".join(str(getattr(fgmpn, "__version__", "")).split(".")[:2])
    if ver not in SUPPORTED:
        return f"fgmpn {getattr(fgmpn, '__version__', '?')} is installed; this Studio answers from FG-MPN {', '.join(v + '.x' for v in SUPPORTED)}."
    return None


def proc_counters(since: dict | None = None) -> dict:
    """Resident memory split (anonymous / file-backed pages: a memory-mapped store counts as file-backed), major page faults and the bytes
    this process read from disk. With `since` (an earlier result) the differences."""
    import resource
    out: dict = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("VmRSS", "RssAnon", "RssFile"):
                out[k] = int(v.split()[0]) // 1024        # MiB
    except OSError:
        pass
    try:
        for line in Path("/proc/self/io").read_text().splitlines():
            k, _, v = line.partition(":")
            if k == "read_bytes":
                out["disk_read_mib"] = round(int(v) / 1048576, 2)
    except OSError:
        pass
    ru = resource.getrusage(resource.RUSAGE_SELF)
    out["major_faults"], out["minor_faults"] = ru.ru_majflt, ru.ru_minflt
    if since:
        return {"disk_read_mib": round(out.get("disk_read_mib", 0) - since.get("disk_read_mib", 0), 2), "major_faults": out["major_faults"] - since["major_faults"],
                "minor_faults": out["minor_faults"] - since["minor_faults"], "rss_mib": out.get("VmRSS"), "rss_file_mib": out.get("RssFile")}
    return out


def store_identity(root: Path) -> dict:
    """What identifies a store, without reading a segment: the live manifest's SHA-256, its generation and its node count."""
    cur = (root / "CURRENT").read_text().strip()
    raw = (root / "manifests" / cur).read_bytes()
    m = json.loads(raw)
    segs = m.get("segments") or []
    return {"manifest": cur, "manifest_sha256": hashlib.sha256(raw).hexdigest(), "generation": m.get("generation"), "segments": len(segs),
            "stored_records": sum(int(s.get("nodes") or 0) for s in segs), "bytes": sum(int(s.get("bytes") or 0) for s in segs)}


# ------------------------------------------------------------------------------------------------------------ the network
class FgNetwork:
    """One FG-MPN store opened read-only, with the reads retrieval needs."""

    def __init__(self, root: str | Path, engine: str | None = None):
        why = runtime_ok()
        if why:
            raise FgError(why, 501)
        from fgmpn import v1compat as V
        from fgmpn.store import FGStore
        from fgmpn.v1api import FGV1Store
        from ..mpnnet.fgmpn_net import _Fed
        self.root = Path(root)
        t0 = time.perf_counter()
        self.identity = store_identity(self.root)
        self.store = FGStore(self.root, engine=engine)
        self.v1 = FGV1Store(self.store, "local")
        self.fed = _Fed(self.v1)
        self.V = V
        self.open_s = round(time.perf_counter() - t0, 2)
        self.lock = threading.RLock()
        self.stats = None
        try:
            from ..mpnnet.fgmpn_net import stats_path
            p = stats_path(self.root, self.store.generation)
            if p.is_file():
                self.stats = json.loads(p.read_text())
        except (OSError, ValueError):
            pass
        self.calls = {"title_lookup": 0, "search": 0, "get": 0, "claims": 0, "resolve": 0}

    @property
    def generation(self) -> int:
        return int(self.store.generation)

    def describe(self) -> dict:
        s = self.stats or {}
        return {"format": "fgmpn", "root": str(self.root), "generation": self.generation, "engine": self.store.engine, **self.identity,
                "validated": s.get("validated"), "quarantined": s.get("quarantined", s.get("other_status")), "open_s": self.open_s}

    # ---- reads (every one is an index read)
    def title_lookup(self, name: str, limit: int = 6, kinds: list[str] | None = None) -> list[dict]:
        """Validated records whose title equals `name` (case-insensitive), of the kinds. Through the store's exact-title index when every
        segment has one (a few microseconds a segment, whatever the word's frequency); else through the runtime's own phrase lookup."""
        self.calls["title_lookup"] += 1
        want = name.strip().lower()
        if not want:
            return []
        with self.v1.lock:
            idx = [self.store.title_index(s) for s in self.store.segments]
            if not idx or any(i is None for i in idx):
                return self.fed.title_lookup(name, limit, kinds)
            kind_ids = {self.V.KIND_ID[k] for k in kinds} if kinds else None
            found = []
            for s, ti in zip(self.store.segments, idx):
                for e in ti.eids(want):
                    c = s.cols(e)
                    if c[4] != 1 or c[7].lower() != want or (kind_ids is not None and c[0] not in kind_ids):
                        continue
                    if self.store._newest_is(s, e, s.nid(e)):
                        found.append((s, e))
                if len(found) >= limit * 4:
                    break
            return [s.row(e) for s, e in found[:limit * 2]]

    def search(self, q: str, limit: int = 12, kinds: list[str] | None = None) -> list[dict]:
        self.calls["search"] += 1
        return self.fed.search(q, limit, kinds)

    def get(self, nid: str, render: bool = True) -> dict | None:
        """Any status: this is the by-id read. Callers decide what a quarantined record may be used for."""
        self.calls["get"] += 1
        try:
            return self.fed.get(nid, render)
        except ValueError:
            return None

    def resolve_key(self, key: str) -> dict | None:
        self.calls["resolve"] += 1
        return self.fed.resolve(key)

    def claims(self, nid: str, limit: int) -> list[dict]:
        self.calls["claims"] += 1
        return self.fed.claims(nid, limit)             # validated claims only (the runtime's own rule)

    def edges(self, nid: str, direction: str = "out", limit: int = 60) -> list[dict]:
        return self.fed.neighbors(nid, direction, None, limit)

    def status_of(self, nid: str) -> str | None:
        r = self.get(nid, render=False)
        return r.get("status") if r else None

    # ---- a record as a node of the pairing engine
    def node(self, d: dict, n_claims: int = 80) -> dict:
        """Studio's node shape (title, cells.knowledge statements and facts, sources) from a stored record and its validated claims."""
        k = (d.get("cells") or {}).get("knowledge") or {}
        stmts = [{"text": str(s["text"])} for s in (k.get("statements") or []) if str(s.get("text", "")).strip()][:8]
        if not stmts and d.get("summary"):
            stmts = [{"text": str(d["summary"])}]
        raw = list(k.get("facts") or [])
        if n_claims:
            for c in self.claims(d["id"], n_claims):
                cf = ((c.get("cells") or {}).get("knowledge") or {}).get("facts") or []
                refs = [r for r in (((c.get("cells") or {}).get("reasoning") or {}).get("referrals") or []) if str(r.get("node", "")).startswith("wd:Q")]
                for f in cf:       # the record a fact's value stands for: its own id, else the claim's referral for the same property
                    if not f.get("value_id") and refs:
                        r = next((r for r in refs if r.get("relation") in (f.get("pid"), f.get("property"))), None)
                        if r:
                            f = {**f, "value_id": str(r["node"])[3:]}
                    raw.append(f)
        facts, seen, per = [], set(), {}
        unresolved = 0
        for f in raw:
            if "property" not in f or "value" not in f:
                continue
            prop, val = str(f["property"]).strip(), str(f["value"]).strip()
            if not val or WID.fullmatch(prop):
                continue
            if WID.search(val):
                v2 = re.sub(r"\s{2,}", " ", WID.sub("", val)).strip(" ,;()")
                if not v2 or not re.search(r"\w", v2):
                    unresolved += 1
                    continue
                val = v2
            if (prop, val) in seen or per.get(prop, 0) >= 6:
                continue
            seen.add((prop, val))
            per[prop] = per.get(prop, 0) + 1
            x = {"property": prop, "value": val}
            for a in ("pid", "unit", "as_of", "from", "until"):
                if f.get(a):
                    x[a] = f[a]
            vid = f.get("value_id") or f.get("qid")
            if vid and re.fullmatch(r"Q\d+", str(vid)) and val != vid:
                x["qid"] = vid
            facts.append(x)
        prov = d.get("prov") or {}
        slug = self.V.DATASET_SLUG.get(int(d["dataset"]), str(d["dataset"])) if str(d.get("dataset", "")).isdigit() else str(d.get("dataset"))
        ids = d.get("ids") or {}
        url = prov.get("url") or (f"https://www.wikidata.org/wiki/{ids['wikidata']}" if ids.get("wikidata") else "")
        cells = {"knowledge": {"statements": stmts, "facts": facts}, "thinking": {}, "reasoning": {"constraints": []}}
        return {"id": d["id"], "title": d["title"], "type": "knowledge", "status": "live", "fg_status": d.get("status", "validated"), "kind": d.get("kind"),
                "key": d.get("key"), "aliases": (d.get("aliases") or [])[:10], "content_hash": d["id"], "cells": cells, "dataset": slug, "domain": d.get("domain"),
                "licence": d.get("licence"), "unresolved_facts": unresolved,
                "sources": [{"kind": slug, "title": f"{d['title']} ({prov.get('record') or d.get('key') or d['id'][:8]})"[:300], "url": url,
                             "license": d.get("licence"), "revision": str(prov.get("revision") or ""), "record": prov.get("record")}]}


# ------------------------------------------------------------------------------------------------------------ linking
def _is_cap(tok: str) -> bool:
    c = next((ch for ch in tok if ch.isalnum()), "")
    return bool(c) and (c.isupper() or c.isdigit())


def _clean_span(toks: list[str]) -> str:
    s = " ".join(toks)
    s = s.strip(" \t\"'“”‘’,;:!?.")
    if s.endswith(")") and s.count("(") < s.count(")"):
        s = s[:-1].rstrip()
    if s.startswith("(") and s.count("(") > s.count(")"):
        s = s[1:].lstrip()
    return s


def strip_instructions(question: str) -> str:
    q = question.strip()
    while True:
        m = list(re.finditer(r"(?<=[.?!])\s+", q))
        if not m:
            break
        tail = q[m[-1].end():]
        if len(tail.split()) <= 9 and INSTRUCTION.match(tail.strip()):
            q = q[:m[-1].start()].rstrip()
        else:
            break
    return q


class Mention:
    __slots__ = ("text", "i", "j", "hits", "tier")

    def __init__(self, text: str, i: int, j: int, hits: list[dict], tier: int):
        self.text, self.i, self.j, self.hits, self.tier = text, i, j, hits, tier


class FgRetriever:
    """Question -> nodes, for the pairing engine."""

    def __init__(self, net: FgNetwork, max_anchor: int = 3, fanout: int = 3, max_hops: int = 2, max_nodes: int = 8, claims_anchor: int = 160, claims_linked: int = 100):
        self.net = net
        self.max_anchor, self.fanout, self.max_hops, self.max_nodes = max_anchor, fanout, max_hops, max_nodes
        self.claims_anchor, self.claims_linked = claims_anchor, claims_linked
        self._node_cache: dict[str, dict] = {}

    # ---- the question's names
    def tokens(self, question: str):
        q = strip_instructions(question).replace("\u2019", "'")
        toks = re.findall(r"\S+", q)
        # a parenthetical of two words or more usually describes the entity before it ("(Dutch noblewoman)"): a hint, not a name.
        # A title that ends with such a group ("Hamlet (1990 film)") still links, because a span may contain a whole group.
        excluded, hint, depth, start = {}, [], 0, None
        for i, t in enumerate(toks):
            if "(" in t and depth == 0:
                start = i
            depth += t.count("(") - t.count(")")
            if depth <= 0 and start is not None:
                grp = toks[start:i + 1]
                if len(grp) >= 2:
                    excluded[(start, i + 1)] = True
                    hint += grp
                start, depth = None, 0
        return toks, excluded, " ".join(hint)

    def spans(self, toks: list[str], excluded: dict):
        n = len(toks)
        out = []
        for L in range(min(12, n), 0, -1):
            for i in range(0, n - L + 1):
                if any(a < i + L and i < b and not (i <= a and b <= i + L and (i < a or L > b - a)) for (a, b) in excluded):
                    continue          # inside a description, or cutting one in two
                seg = toks[i:i + L]
                first, last = re.sub(r"[^\w]", "", seg[0]).lower(), re.sub(r"[^\w]", "", seg[-1]).lower()
                if not first or not last or (last in EDGE_STOP and not _is_cap(seg[-1])):
                    continue
                caps = [_is_cap(t) or re.sub(r"[^\w]", "", t).lower() in CONNECT for t in seg]
                namelike = all(caps) and (_is_cap(seg[0]) and _is_cap(seg[-1]))
                if first in EDGE_STOP and not (_is_cap(seg[0]) and i > 0 and (L >= 2 or first not in STOP)):
                    continue
                text = _clean_span(seg)
                if len(text) < 2:
                    continue
                cap = any(_is_cap(t) and (k > 0 or re.sub(r"\W", "", t).lower() not in STOP) for k, t in enumerate(seg))
                if L == 1 and (first in STOP or (not cap and len(text) < 7)):
                    continue
                out.append((i, i + L, text, cap))
        return out

    DEFINITION = re.compile(r"^\s*(?:what|who)\s+(?:is|are|was|were)\s+(?:a |an |the )?[\w' -]{3,60}\??\s*$", re.I)

    def link(self, question: str, papers: bool | None = None) -> tuple[list[Mention], dict]:
        t0 = time.perf_counter()
        has_ids = bool(HEX32.search(question))
        question = HEX32.sub(" ", question)                      # a record id is an anchor of its own; it is not a word of a name
        toks, excluded, hint = self.tokens(question)
        papers = any(w in question.lower() for w in PAPER_WORDS) if papers is None else papers
        spans = self.spans(toks, excluded)
        tier1 = [s for s in spans if s[3]]
        # lower-case phrases of two words or more; one lower-case word only when the question is a definition ("What is photosynthesis?")
        defn = bool(self.DEFINITION.match(strip_instructions(question)))
        tier2 = [] if has_ids else [s for s in spans if not s[3] and (s[1] - s[0] >= 2 or (defn and len(s[2]) >= 5))]
        used: set[int] = set()
        found: list[Mention] = []
        probes = 0
        unmatched: list[str] = []
        for tier, group in ((1, tier1), (2, tier2)):
            if tier == 2 and found:
                break
            for i, j, text, _cap in group:
                if any(k in used for k in range(i, j)) or probes >= 48 or len(found) >= self.max_anchor + 1:
                    continue
                probes += 1
                hits = self.net.title_lookup(text, 6, ENTITY_KINDS)
                if not hits and (papers or (tier == 1 and j - i >= 2 and text[0] in "\"'“" )):
                    hits = self.net.title_lookup(text, 3, ["work"])
                if hits:
                    found.append(Mention(text, i, j, hits, tier))
                    used.update(range(i, j))
        # a capitalised phrase with no exact title: titles and aliases that contain it
        if not found or len(found) < 1:
            names = []
            run: list[str] = []
            for k, t in enumerate(toks):
                if any(a <= k < b for (a, b) in excluded) or not _is_cap(t) or re.sub(r"\W", "", t).lower() in STOP:
                    if run:
                        names.append(" ".join(run))
                    run = []
                else:
                    run.append(re.sub(r"^[\W]+|[\W]+$", "", t))
            if run:
                names.append(" ".join(run))
            for name in sorted({n for n in names if len(n) >= 4}, key=len, reverse=True)[:2]:
                hits = [h for h in self.net.search(name, 12, ENTITY_KINDS) if self._has(h, name)]
                if hits:
                    found.append(Mention(name, 0, 0, hits, 3))
                else:
                    unmatched.append(name)
        return found, {"hint": hint, "probes": probes, "ms": round((time.perf_counter() - t0) * 1000, 1), "unmatched": unmatched}

    @staticmethod
    def _has(h: dict, name: str) -> bool:
        blob = " ".join([h["title"], *(h.get("aliases") or [])]).lower()
        return re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", blob) is not None

    def _sitelinks(self, h: dict) -> int:
        for c in ((h.get("cells") or {}).get("reasoning") or {}).get("checks") or []:
            if c.get("kind") == "coverage":
                return int(c.get("sitelinks") or 0)
        return 0

    def rank(self, m: Mention, question: str, hint: str) -> list[dict]:
        ctx = plain_words(question + " " + hint) - plain_words(m.text)
        hw = plain_words(hint)
        for h in m.hits:
            desc = " ".join([h.get("summary") or "", *[str(s.get("text", "")) for s in ((h.get("cells") or {}).get("knowledge") or {}).get("statements", [])[:2]]])
            dw = plain_words(desc)
            h["_overlap"] = len(ctx & dw) + 2 * len(hw & dw)
        slug = self.net.V.DATASET_SLUG
        m.hits.sort(key=lambda h: (h["title"].lower() != m.text.lower(), -h["_overlap"], KIND_PREF.get(h["kind"], 2),
                                   -DATASET_RANK.get(slug.get(int(h["dataset"])) if str(h["dataset"]).isdigit() else h["dataset"], 0), -self._sitelinks(h)))
        return m.hits

    # ---- nodes
    def node_of(self, d: dict, n_claims: int) -> dict:
        n = self._node_cache.get(d["id"])
        if n is None:
            full = self.net.get(d["id"]) or d
            n = self.net.node(full, n_claims)
            if len(self._node_cache) > 4000:
                self._node_cache.clear()
            self._node_cache[d["id"]] = n
        return n

    @staticmethod
    def order_facts(node: dict, question: str, asked: list[str]) -> dict:
        """The question's own properties first, then dates and places, so a render limit never cuts what is asked."""
        qw = words(question)
        a = [x.lower() for x in asked]

        def key(f):
            p = f["property"].lower()
            return (0 if p in a else 1, -len(qw & words(p) - GENERIC), FACT_FIRST.get(f.get("pid"), 99))
        k = node["cells"]["knowledge"]
        k["facts"] = sorted(k["facts"], key=key)
        return node

    def cand(self, n: dict, via: str, score: float = 1.0, **kw) -> dict:
        return {"id": n["id"], "score": score, "gate": 1.0, "via_mpn": None, "home": None, "via": via, "node": n, **kw}

    def retrieve(self, question: str, exclude: set | None = None, anchors: int | None = None, expand: bool = True, max_nodes: int | None = None) -> dict:
        """The nodes that answer a question, in the order they are given to the core, with what was done to find them."""
        t0 = time.perf_counter()
        cap = max_nodes or self.max_nodes
        exclude = exclude or set()
        tr: dict = {"mentions": [], "notices": []}
        question_full, question = question, plain_question(question)
        asked = asked_properties(question)
        out: list[dict] = []
        # an explicit record id: returned with its status; only a validated record becomes evidence
        for hx in dict.fromkeys(HEX32.findall(question)):
            d = self.net.get(hx)
            if d is None:
                tr["notices"].append({"id": hx, "status": "unknown", "text": f"No record {hx} is stored in this network."})
            elif d.get("status") != "validated":
                tr["notices"].append({"id": hx, "status": d.get("status"), "title": d.get("title"), "kind": d.get("kind"),
                                      "text": f"Record {hx} is stored in this network with status {d.get('status')}. A {d.get('status')} record is kept but is not "
                                              f"used as evidence and cannot be cited."})
            elif d["id"] not in exclude:
                out.append(self.cand(self.order_facts(self.node_of(d, self.claims_anchor), question, asked), "record id"))
        mentions, lt = self.link(question_full)
        tr["link"] = lt
        hint = lt["hint"]
        anchor_nodes: list[dict] = []
        for m in mentions:
            hits = self.rank(m, question, hint)
            take = hits[:1]
            if (len(hits) > 1 and hits[1]["_overlap"] == hits[0]["_overlap"] and hits[1]["title"].lower() == hits[0]["title"].lower()
                    and KIND_PREF.get(hits[1]["kind"], 2) == KIND_PREF.get(hits[0]["kind"], 2)):
                take.append(hits[1])
            tr["mentions"].append({"text": m.text, "hits": len(m.hits), "chosen": [h["id"] for h in take]})
            for h in take:
                if h["id"] in exclude or any(h["id"] == c["id"] for c in out) or len(anchor_nodes) >= (anchors or self.max_anchor):
                    continue
                n = self.order_facts(self.node_of(h, self.claims_anchor), question, asked)
                c = self.cand(n, f"name “{m.text}”", 1.0 - 0.01 * len(anchor_nodes))
                anchor_nodes.append(c)
                out.append(c)
        if expand and anchor_nodes:
            out += self.follow_hops(anchor_nodes, out, question, asked, exclude, tr, cap)
        tr["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return {"cands": out[:cap], "trace": tr}

    # ---- relations
    def link_targets(self, node: dict, question: str, asked: list[str], relation: str | None = None) -> list[tuple[float, dict]]:
        """Facts of a node whose value is another record, best first: relation wording that matches the question first."""
        qw = words(question)
        rw = words(relation) if relation else set()
        out = []
        for f in node["cells"]["knowledge"]["facts"]:
            if not f.get("qid") or f.get("pid") in SKIP_LINKS:
                continue
            pw = words(f["property"]) - GENERIC
            ov = len((qw | rw) & pw)
            if relation and not rw & pw and relation.lower() not in f["property"].lower():
                continue
            out.append((ov * 10 - PREFER_LINK.get(f.get("pid"), 9) * 0.1, f))
        out.sort(key=lambda x: -x[0])
        return out

    def follow_hops(self, anchors: list[dict], have: list[dict], question: str, asked: list[str], exclude: set, tr: dict, cap: int) -> list[dict]:
        added: list[dict] = []
        known = {c["id"] for c in have} | set(exclude)
        asked_l = [a.lower() for a in asked]
        held = {f["property"].lower() for c in have for f in c["node"]["cells"]["knowledge"]["facts"]}
        structural = bool(LINK_RELATION_TEXT.search(question))
        # hop 1 only when the question needs another record: it asks for a property the anchors lack, or it points through a relation
        if asked_l and all(a in held for a in asked_l) and not structural:
            tr["follow"] = "not needed: the first record holds what the question asks"
            return added
        frontier = anchors[:2]
        for hop in range(1, self.max_hops + 1):
            nxt = []
            for src in frontier:
                cand = self.link_targets(src["node"], question, asked)
                matched = [c for c in cand if c[0] >= 10] or (cand[:2] if hop == 1 and asked_l else [])
                for score, f in matched[: self.fanout if hop == 1 else 1]:
                    if len(have) + len(added) >= cap:
                        return added
                    d = self.net.resolve_key("wd:" + f["qid"])
                    if not d or d["id"] in known or d.get("status") != "validated":
                        continue
                    n = self.order_facts(self.node_of(d, self.claims_linked), question, asked)
                    n["cells"]["knowledge"]["facts"] = n["cells"]["knowledge"]["facts"][:12]
                    n["cells"]["reasoning"]["constraints"] = [{"text": f"Linked from {src['node']['title']}: its {f['property']}."}]
                    c = self.cand(n, f"{f['property']} of {src['node']['title']}", 0.5, relation=f["property"], **{"from": src["node"]["title"]})
                    known.add(d["id"])
                    added.append(c)
                    nxt.append(c)
            held |= {f["property"].lower() for c in nxt for f in c["node"]["cells"]["knowledge"]["facts"]}
            tr.setdefault("hops", []).append({"hop": hop, "added": [c["node"]["title"] for c in nxt]})
            if not nxt or (asked_l and all(a in held for a in asked_l) and hop >= 1 and not structural) or hop >= self.max_hops:
                break
            # a second hop only from a record reached through a relation the question names
            frontier = [c for c in nxt if words(c["relation"]) & words(question)] or nxt[:1]
        return added

    # ---- the core's tools
    def search_query(self, query: str, have: set, k: int = 3) -> list[dict]:
        """mpn_search: a name or short phrase. The query as a whole is tried as a title first."""
        q = query.strip()
        out: list[dict] = []
        hits = self.net.title_lookup(q, 6, ENTITY_KINDS) if q else []
        if hits:
            m = Mention(q, 0, 0, hits, 1)
            for h in self.rank(m, q, "")[:k]:
                if h["id"] not in have:
                    out.append(self.cand(self.order_facts(self.node_of(h, self.claims_anchor), q, asked_properties(q)), f"search “{q}”"))
            if out:
                return out[:k]
        r = self.retrieve(q, exclude=have, anchors=k, expand=False)
        return r["cands"][:k]

    def follow_relation(self, src: dict, relation: str, have: set, k: int = 4) -> tuple[list[dict], list[str]]:
        """mpn_follow: records the source node's facts and its stored edges lead to, by relation wording ('any' lists all)."""
        rel = (relation or "any").strip().lower().replace("_", " ")
        anyrel = rel in ("any", "all", "")
        out: list[dict] = []
        links = sorted({f["property"] for f in src["node"]["cells"]["knowledge"]["facts"] if f.get("qid")})
        for f in src["node"]["cells"]["knowledge"]["facts"]:
            if len(out) >= k:
                break
            if not f.get("qid") or f.get("pid") in SKIP_LINKS and not anyrel:
                continue
            if not anyrel and rel not in f["property"].lower() and not (words(rel) & words(f["property"]) - GENERIC):
                continue
            d = self.net.resolve_key("wd:" + f["qid"])
            if d and d["id"] not in have and d.get("status") == "validated" and all(d["id"] != x["id"] for x in out):
                n = self.node_of(d, self.claims_linked)
                out.append(self.cand(n, f"{f['property']} of {src['node']['title']}", 0.5, relation=f["property"], **{"from": src["node"]["title"]}))
        if len(out) < k:                                  # stored edges (authors of a work, citations, ...)
            for e in self.net.edges(src["id"], "out", 60):
                name = (e.get("rel_label") or e["rel"]).strip().lower().replace("_", " ")
                if name == "mentions":
                    continue
                links.append(name)
                if not anyrel and rel not in name and name not in rel:
                    continue
                if e.get("via") and self.net.status_of(e["via"]) not in (None, "validated"):
                    continue                              # an edge that rests on a quarantined claim
                d = self.net.get(e["node"])
                if d and d.get("status") == "validated" and d["id"] not in have and all(d["id"] != x["id"] for x in out):
                    out.append(self.cand(self.node_of(d, self.claims_linked), f"{name} of {src['node']['title']}", 0.5, relation=name, **{"from": src["node"]["title"]}))
                if len(out) >= k:
                    break
        return out, sorted(set(links))[:30]
