"""Yaob product names and the legacy ids and names that still resolve (mpnstudio/names.py)."""
from mpnstudio import names
from mpnstudio.catalogue import install, site
from mpnstudio.workspace.network import KNOWN


def test_official_products_and_components():
    assert names.PRODUCT_NAMES == {"yaob-n10k-c4b": "Yaob N10K-C4B", "yaob-n119m-c4b": "Yaob N119M-C4B"}
    assert names.COMPONENT_NAMES.keys() == {"yaob-n10k-mpn", "yaob-n119m-mpn"}


def test_every_legacy_product_id_resolves_to_its_successor():
    assert names.product_id("yaob-next-c4b-n10k") == "yaob-n10k-c4b"
    assert names.product_id("yaob-c4b-n10k") == "yaob-n10k-c4b"
    assert names.product_id("yaob-next-n10k-c4b") == "yaob-n10k-c4b"
    assert names.product_id("yaob-next-c4b-n119m") == "yaob-n119m-c4b"
    assert names.product_id("yaob-origin") == "yaob-n119m-c4b"
    assert names.product_id("yaob-origin-n119m") == "yaob-n119m-c4b"       # the id a linked project stored before 0.6.1
    assert names.product_id("yaob-mpn14b") == "yaob-n119m-c4b"
    assert names.product_id("yaob-c4b-mpn14b") == "yaob-n119m-c4b"
    assert names.product_id("yaob-n10k-retrieval") == "yaob-n10k-c4b-retrieval"


def test_current_and_unknown_ids_are_unchanged():
    assert names.product_id("yaob-n10k-c4b") == "yaob-n10k-c4b"
    assert names.product_id("yaob-n119m-c4b") == "yaob-n119m-c4b"
    assert names.product_id("some-other-model") == "some-other-model"
    assert names.product_id(None) is None


def test_retired_original_product_is_not_a_successor_of_the_c4b_product():
    # the old id `yaob-n10k` is the retired 0.1.0 product; it resolves for existing projects, and it is not the new id
    assert names.is_retired("yaob-n10k")
    assert names.RETIRED["yaob-n10k"] == "yaob-n10k-c4b"
    assert not names.is_retired("yaob-n10k-c4b")
    assert names.product_id("yaob-n10k") == "yaob-n10k-c4b"


def test_component_ids_are_kind_aware():
    assert names.canonical("mpn", "yaob-next-n10k") == "yaob-n10k-mpn"
    assert names.canonical("mpn", "yaob-origin-n119m") == "yaob-n119m-mpn"
    assert names.canonical("model", "yaob-origin-n119m") == "yaob-n119m-c4b"
    assert names.canonical("core", "qwen3.5-4b-q8_0-gguf") == "qwen3.5-4b-q8_0-gguf"


def test_display_names_replace_legacy_product_names_only():
    assert names.display_name("Yaob Origin N119M") == "Yaob N119M-C4B"
    assert names.display_name("Yaob Origin N119M Shattered") == "Yaob N119M-C4B Shattered"
    assert names.display_name("Yaob Next (C4B-N10K)") == "Yaob N10K-C4B"
    assert names.display_name("Yaob C4B-N10K 1.0.0") == "Yaob N10K-C4B 1.0.0"
    assert names.display_name("Yaob Next C4B-N119M") == "Yaob N119M-C4B"
    # the retired product's own stored name and the current names are left as they are
    assert names.display_name("Yaob N10K") == "Yaob N10K"
    assert names.display_name("Yaob N10K-C4B") == "Yaob N10K-C4B"
    assert names.display_name(None) is None
    once = names.display_name("Yaob Origin N119M")
    assert names.display_name(once) == once


def test_package_configuration_has_one_official_spelling():
    assert names.configuration("C4B-N10K") == "N10K-C4B"
    assert names.configuration("C4B-N119M") == "N119M-C4B"
    assert names.configuration("N10K-C4B") == "N10K-C4B"
    assert names.configuration(None) is None


def test_the_retired_original_is_archived_download_only_and_labelled():
    doc = {"models": [{"id": "yaob-n10k-c4b", "name": "Yaob N10K-C4B"}],
           "mpns": [{"id": "yaob-n10k", "name": "Yaob N10K", "version": "0.1.0", "links": {"release": "https://example.invalid/r"}},
                    {"id": "yaob-next-n10k", "name": "Yaob Next N10K-C4B", "version": "0.2.0"}],
           "cores": [], "apps": []}
    listed = {(kind, e["id"]): e for kind, e in site.entries(doc)}
    retired = site.release_of("mpn", listed[("mpn", "yaob-n10k")])
    assert retired["download_only"] and "retired" in retired["download_only"]["reason"].lower()
    assert retired["name"] == "Yaob N10K (retired)"
    assert not retired["compat"]["ok"]
    component = site.release_of("mpn", listed[("mpn", "yaob-next-n10k")])
    assert component["name"] == "Yaob N10K network"      # a network is a component, named as one


def test_pins_match_old_and_new_ids_of_the_same_release():
    assert install._same("model", "yaob-origin-n119m", "yaob-n119m-c4b")
    assert install._same("mpn", "yaob-origin-n119m", "yaob-n119m-mpn")
    assert not install._same("mpn", "yaob-origin-n119m", "yaob-n119m-c4b")   # a component is not its product
    assert not install._same("model", "yaob-n10k-c4b", "yaob-n119m-c4b")


def test_linked_network_identity_maps_to_the_renamed_known_key():
    # a linked project stores identity.known = "yaob-origin-n119m"; it must still find the official entry
    assert names.product_id("yaob-origin-n119m") in KNOWN
    assert KNOWN["yaob-n119m-c4b"]["name"] == "Yaob N119M network"
    assert "yaob-origin-n119m" not in KNOWN


def test_overview_of_a_linked_project_saved_with_the_old_key_shows_the_official_name():
    # the real profile's linked project stores identity.known = "yaob-origin-n119m"; its overview must still name the network
    from mpnstudio.workspace.network import Workspace
    ws = Workspace.__new__(Workspace)              # no source is opened: only the identity mapping is under test
    ws.net = {"identity": {"known": "yaob-origin-n119m", "name": "Yaob Origin N119M"}}
    ws.m = {"name": "Yaob Origin N119M"}
    ws.overlay = None
    ws.last_ms = None
    ws._base = lambda op: {"name": "Yaob Origin N119M", "description": "stored", "validated_nodes": 5}
    ov = ws.overview()
    assert ov["name"] == "Yaob N119M network"
    assert ov["description"] == KNOWN["yaob-n119m-c4b"]["description"]


def test_a_fork_saved_before_0_6_1_keeps_its_name_mapped():
    from mpnstudio.workspace.network import Workspace
    ws = Workspace.__new__(Workspace)
    ws.net = {"identity": {"known": "yaob-origin-n119m"}}
    ws.m = {"name": "Yaob Origin N119M Shattered"}
    ws.overlay = None
    ws.last_ms = None
    ws._base = lambda op: {"name": "n", "validated_nodes": 5}
    assert ws.overview()["name"] == "Yaob N119M network"


def test_package_family_and_component_names_are_display_names():
    # the packages carry family "yaob" and the component id they were built with; Studio shows the official names
    assert names.family_name("yaob") == "Yaob"
    assert names.family_name("Yaob") == "Yaob"
    assert names.component_name("yaob-next-n10k", "yaob") == "Yaob N10K network"
    assert names.component_name("yaob-origin-n119m", "yaob") == "Yaob N119M network"
    assert names.component_name("yaob-n10k-mpn", "Yaob") == "Yaob N10K network"
    assert names.component_name("some-other-net", "yaob") == "Yaob some-other-net"


def test_network_projects_show_component_names_not_product_names():
    # A linked or library network project is a network: its stored legacy name must not read as the complete product.
    assert names.network_display_name("Yaob Origin N119M") == "Yaob N119M network"
    assert names.network_display_name("Yaob Origin N119M Shattered") == "Yaob N119M network Shattered"
    assert names.network_display_name("Yaob Next N10K") == "Yaob N10K network"
    assert names.network_display_name("Yaob N10K-C4B") == "Yaob N10K-C4B"      # a product name is left alone
    assert names.network_display_name(None) is None
