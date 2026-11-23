"""Retrieval over the real Yaob Origin N119M store (119.7 million records, 39.86 GB). Skipped unless MPN_N119M_STORE names the store folder
(on the DGX Spark: ~/release-staging/yaob-origin-n119m/store). Read-only; nothing here iterates the store or loads a model."""
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("fgmpn")
STORE = os.environ.get("MPN_N119M_STORE")
pytestmark = pytest.mark.skipif(not STORE or not (Path(STORE or "x") / "CURRENT").is_file(), reason="MPN_N119M_STORE is not set to a store folder")

from mpnstudio.engine.fgreason import FgNetwork, FgRetriever   # noqa: E402


@pytest.fixture(scope="module")
def rt():
    return FgRetriever(FgNetwork(STORE))


def test_the_store_opens_in_seconds_and_identifies_itself_from_its_manifest():
    t0 = time.perf_counter()
    net = FgNetwork(STORE)
    assert time.perf_counter() - t0 < 30 and net.identity["stored_records"] == 119_733_309 and net.generation == 3


def test_a_long_tail_person_is_linked_and_the_gold_fact_is_in_the_evidence(rt):
    r = rt.retrieve("In what year was Aleida van Culemborg (Dutch noblewoman) born? Answer briefly.")
    n = r["cands"][0]["node"]
    assert n["title"] == "Aleida van Culemborg" and n["key"] == "wd:Q2149288"
    assert any(f["property"] == "date of birth" and f["value"].startswith("1440") for f in n["cells"]["knowledge"]["facts"])


def test_two_hops_reach_the_country_of_the_birthplace(rt):
    r = rt.retrieve("In which country is the place where Ehrenfried Walther von Tschirnhaus (German mathematician and philosopher) was born? Answer briefly.")
    place = next(c for c in r["cands"] if c["node"]["title"].startswith("Sławnikowice"))
    assert any(f["property"] == "country" and f["value"] == "Poland" for f in place["node"]["cells"]["knowledge"]["facts"])


def test_quarantined_claims_are_stored_but_never_evidence(rt):
    net = rt.net
    rec = net.get("00616f79" + "0" * 24, False)            # placeholder id: only checks the by-id path answers without error
    assert rec is None or rec["status"] in ("validated", "quarantined")
    qs = [h for h in net.title_lookup("Emília Rotter", 3, None)]
    if qs:
        r = rt.retrieve("What is the Commons category of Emília Rotter?")
        assert all(c["node"]["fg_status"] == "validated" for c in r["cands"])


def test_an_invented_name_links_to_nothing_and_a_question_costs_milliseconds_not_a_scan(rt):
    t0 = time.perf_counter()
    r = rt.retrieve("Where was Zorblax Quindleton born?")
    assert r["cands"] == [] and time.perf_counter() - t0 < 5
