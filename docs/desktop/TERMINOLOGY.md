# Terminology and compatibility

| Name | Meaning |
|---|---|
| Fylume | research organisation |
| Yaob | model family |
| MPN | **Modular Persistent Network** (renamed from *Modular Parameter Network*, 2026-11) |
| MPN Studio | this desktop application |
| KTR | Knowledge, Thinking, Reasoning (the three cells of a node) |
| 3CN | Three Cell Node |
| C4B | cognitive core of about 4 billion parameters |
| N10K | the 10,000-node network configuration |

Where it is shown: window title and header tooltip, project selector subtitle, *Help, Names and terms*, *About*,
`project.json` (`architecture`), the package metadata, and `docs/`.

## Old identifiers that stay (and why)

Existing stores, bundles and tooling depend on these, so only the *displayed* name changed:

| Identifier | Where | Stays because |
|---|---|---|
| `mpn_v`, `mpn_*` tables/columns, `mpn_id` | SQLite store | stored networks must open unchanged |
| `/api/mpns`, `mpn` JSON fields | HTTP API, bundles | clients and bundles use them |
| `yaob-mpn-package/1`, `yaob-bundle/1` | portable formats | verified on import |
| `MPN_DATA`, `MPN_DB`, `MPN_MODELS`, `MPN_*` | environment variables | existing deployments (the Spark units) use them |
| `mpnstudio` | Python package | importable name |

`mpnstudio/__init__.py` exposes `ARCHITECTURE_NAME` and `LEGACY_ARCHITECTURE_NAME`. A project manifest written
before the rename is upgraded in place (after a backup) and keeps `legacy_architecture_name`.
