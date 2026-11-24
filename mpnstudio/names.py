"""Yaob product and component names, and the legacy ids and names that still resolve.

The official products are Yaob N10K-C4B and Yaob N119M-C4B (ids `yaob-n10k-c4b`, `yaob-n119m-c4b`). Components (the
networks alone) are `yaob-n10k-mpn` and `yaob-n119m-mpn`. The retired original product `yaob-n10k` (0.1.0) is never
offered in a catalogue, but a project or a history entry that names it still resolves to its successor.

Legacy spellings are accepted on input and in existing files, and are never displayed or listed. Nothing here
rewrites a stored file: a project keeps its id and its stored name, and the names shown are mapped on read.
"""
from __future__ import annotations

import re

PRODUCT_NAMES = {"yaob-n10k-c4b": "Yaob N10K-C4B", "yaob-n119m-c4b": "Yaob N119M-C4B"}
PRODUCT_CONFIGURATION = {"yaob-n10k-c4b": "N10K-C4B", "yaob-n119m-c4b": "N119M-C4B"}
COMPONENT_NAMES = {"yaob-n10k-mpn": "Yaob N10K network", "yaob-n119m-mpn": "Yaob N119M network"}

# The retired original product: resolves to its successor for existing projects and history, never listed in a catalogue.
RETIRED = {"yaob-n10k": "yaob-n10k-c4b"}

# Product and model ids (old -> new). Includes the retired id.
PRODUCT_LEGACY = {
    "yaob-next-c4b-n10k": "yaob-n10k-c4b",
    "yaob-c4b-n10k": "yaob-n10k-c4b",
    "yaob-next-n10k-c4b": "yaob-n10k-c4b",
    "yaob-n10k": "yaob-n10k-c4b",
    "yaob-n10k-retrieval": "yaob-n10k-c4b-retrieval",
    "yaob-next-c4b-n119m": "yaob-n119m-c4b",
    "yaob-origin": "yaob-n119m-c4b",
    "yaob-origin-n119m": "yaob-n119m-c4b",
    "yaob-c4b-mpn14b": "yaob-n119m-c4b",
    "yaob-mpn14b": "yaob-n119m-c4b",
}

# Component (network) repo ids, old -> new. A catalogue `mpn` entry is a component, not a product.
COMPONENT_LEGACY = {"yaob-next-n10k": "yaob-n10k-mpn", "yaob-origin-n119m": "yaob-n119m-mpn"}

# Display names, old -> new, longest first so that "Yaob Origin N119M Shattered" keeps its suffix.
_NAME_PAIRS = [
    ("Yaob Next (C4B-N10K)", "Yaob N10K-C4B"),
    ("Yaob Next (N10K-C4B)", "Yaob N10K-C4B"),
    ("Yaob Next N10K-C4B", "Yaob N10K-C4B"),
    ("Yaob Next C4B-N10K", "Yaob N10K-C4B"),
    ("Yaob C4B-N10K", "Yaob N10K-C4B"),
    ("Yaob Next N119M-C4B", "Yaob N119M-C4B"),
    ("Yaob Next C4B-N119M", "Yaob N119M-C4B"),
    ("Yaob Origin N119M", "Yaob N119M-C4B"),
]
_NAME_RE = re.compile("|".join(re.escape(old) for old, _ in sorted(_NAME_PAIRS, key=lambda p: -len(p[0]))))
_NAME_MAP = dict(_NAME_PAIRS)

# The package manifest's `configuration` (C4B-N10K in packages before 1.0.0 final) and the product configuration.
_CONFIGURATION_LEGACY = {"C4B-N10K": "N10K-C4B", "C4B-N119M": "N119M-C4B"}


def product_id(x: str | None) -> str | None:
    """The current product or model id for any id that once named it (unchanged when it is already current or unknown)."""
    if x is None:
        return None
    s = str(x).strip()
    return PRODUCT_LEGACY.get(s, s)


def component_id(x: str | None) -> str | None:
    if x is None:
        return None
    s = str(x).strip()
    return COMPONENT_LEGACY.get(s, s)


def canonical(kind: str, x: str | None) -> str | None:
    """Kind-aware comparison key: a catalogue `model` is a product, an `mpn` is a component, a `core` keeps its id."""
    if kind == "model":
        return product_id(x)
    if kind == "mpn":
        return component_id(x)
    return x


def is_retired(x: str | None) -> bool:
    return str(x or "").strip() in RETIRED


def display_name(text: str | None) -> str | None:
    """A stored or catalogue name with the legacy product names replaced by the official ones (text is otherwise unchanged)."""
    if not text:
        return text
    return _NAME_RE.sub(lambda m: _NAME_MAP[m.group(0)], str(text))


# A project or library entry is a network, never a complete product: the old network names map to the component names.
_NETWORK_PAIRS = [("Yaob Origin N119M", "Yaob N119M network"), ("Yaob Next N10K", "Yaob N10K network")]
_NETWORK_RE = re.compile("|".join(re.escape(old) for old, _ in sorted(_NETWORK_PAIRS, key=lambda p: -len(p[0]))))
_NETWORK_MAP = dict(_NETWORK_PAIRS)


def network_display_name(text: str | None) -> str | None:
    """The display name of a project or library network: a stored "Yaob Origin N119M" is shown as "Yaob N119M network"."""
    if not text:
        return text
    return display_name(_NETWORK_RE.sub(lambda m: _NETWORK_MAP[m.group(0)], str(text)))


def configuration(cfg: str | None) -> str | None:
    """The product configuration as the official spelling (N10K-C4B), whichever spelling a package manifest uses."""
    if cfg is None:
        return None
    return _CONFIGURATION_LEGACY.get(str(cfg).strip(), str(cfg).strip())


def is_known_product(x: str | None) -> bool:
    return product_id(x) in PRODUCT_NAMES


def family_name(family: str | None) -> str:
    """The family as a display word: a package's "yaob" is shown as "Yaob"."""
    f = str(family or "").strip()
    return "Yaob" if f.lower() == "yaob" else f


def component_name(mpn_id: str | None, family: str | None = None) -> str:
    """The display name of a network (a component): "Yaob N10K network" for the official components, else family and id."""
    comp = component_id(mpn_id)
    if comp in COMPONENT_NAMES:
        return COMPONENT_NAMES[comp]
    return " ".join(x for x in (family_name(family), str(mpn_id or "").strip()) if x)
