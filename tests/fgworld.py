"""A small synthetic FG-MPN world, built with the public runtime's own fixtures (fgmpn.fixtures), for tests of retrieval over a native store.

Marta Kowalenko (a botanist) was born in Wrocklow, a city in Polandia; two records are called Orlin (a river and an actor); one claim about
Marta is quarantined (status 2) and so is one whole record, Vesper Ellison. Nothing here is a real person or place.
"""
from __future__ import annotations

from pathlib import Path


def build(root: Path, indexed: bool = False) -> dict:
    from fgmpn import v1compat as V
    from fgmpn.fixtures import Gen, _body, rec
    from fgmpn.segment import SegmentBuilder
    from fgmpn.store import FGStore, ListProvider

    g = Gen(11)

    def lit(subj_q: str, pid: str, plabel: str, value: str, status: int = 1, guid: str = "0000"):
        """A claim whose value is text, a date or a number (Gen.claim only takes records as values)."""
        skey = f"wd:{subj_q}"
        snid = V.h16(skey)
        ckey = f"wds:{subj_q}${guid}-{pid}"
        b = _body(key=ckey, cells={"knowledge": {"facts": [{"pid": pid, "property": plabel, "value": value}]}, "thinking": {"cues": [plabel]},
                                   "reasoning": {"constraints": [{"kind": "rank", "text": "normal"}], "referrals": []}},
                  prov={"dataset": "wikidata", "record": f"{subj_q}${guid}-{pid}", "item": subj_q, "revision": 1234567, "url": f"https://www.wikidata.org/wiki/{subj_q}#{pid}"},
                  evidence={"basis": "referenced", "count": 1}, method="deterministic:wikidata/1", licence=V.LIC_NAME[1])
        title = f"{g.labels.get(skey, subj_q)} — {plabel}: {value}"
        return g._add(rec(V.claim_id(snid, ckey), "claim", title, b, status=status, subj=snid, conf=0.95))

    g.entity("Q1002", "Polandia", "country in Central Europe", wikipedia=True)
    g.entity("Q1001", "Wrocklow", "city in Polandia", wikipedia=True)
    g.claim("Q1001", "P17", "country", "Q1002")
    g.entity("Q2001", "Marta Kowalenko", "Polandian botanist (1951-2019)", kind="person", domain=21, wikipedia=True)
    g.claim("Q2001", "P19", "place of birth", "Q1001")
    g.claim("Q2001", "P27", "country of citizenship", "Q1002")
    lit("Q2001", "P569", "date of birth", "1951-03-02")
    lit("Q2001", "P106", "occupation", "botanist")
    lit("Q2001", "P373", "Commons category", "Kowalenko botanist photographs", status=2, guid="q")      # quarantined: kept, never used
    lit("Q2001", "P856", "official website", "https://kowalenko.example/")
    g.entity("Q3001", "Orlin", "river in Polandia")
    g.entity("Q3002", "Orlin", "Polandian actor born in Wrocklow", kind="person", domain=21)
    g.claim("Q3002", "P19", "place of birth", "Q1001")
    lit("Q3002", "P569", "date of birth", "1977-08-09")
    g.entity("Q2002", "Hugo Strandvik", "Swedish ship's cook", kind="person", domain=21)
    lit("Q2002", "P569", "date of birth", "1822-01-05")
    g.claim("Q2002", "P27", "country of citizenship", "Q1002")
    g.entity("Q9001", "Vesper Ellison", "a record that failed validation", kind="person", domain=21, status=2)      # a quarantined record
    g.entity("Q9100", "Lichen Institute of Wrocklow", "research institute in Wrocklow", kind="organisation", domain=22)
    lit("Q9100", "P571", "inception", "1923")
    g.claim("Q9100", "P159", "headquarters location", "Q1001")
    g.author("A5000000001", "Ivo Petrenko")
    g.work(7000000001, "10.5555/lichen.1", "A long study of lichen growth on the walls of Wrocklow", "Polandian Journal of Botany", ["A5000000001"], 1994)
    recs = list(g.recs.values())
    st = FGStore.create(root)
    st.publish(ListProvider(recs), kind="base", builder=SegmentBuilder(block=16), note="synthetic test world")
    if indexed:
        from fgmpn import titleindex
        titleindex.build(str(root), 1)
    ids = {k: V.h16(f"wd:{k}").hex() for k in ("Q1001", "Q1002", "Q2001", "Q3001", "Q3002", "Q2002", "Q9001", "Q9100")}
    ids["quarantined_claim"] = V.claim_id(V.h16("wd:Q2001"), "wds:Q2001$q-P373").hex()
    ids["validated_claim"] = V.claim_id(V.h16("wd:Q2001"), "wds:Q2001$0000-P569").hex()
    return ids
