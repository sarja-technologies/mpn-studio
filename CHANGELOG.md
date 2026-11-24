# Changelog

## 0.6.1 (2026-11-24)

Official Yaob names.

- **Yaob N10K-C4B and Yaob N119M-C4B.** The two official products are named as in the release contract: the 10,000-node network with the
  4-billion-parameter core, and the 119-million-record network with the same core. Every displayed name, the default name of an imported
  release, the advertised model ids and the Releases view use these names. Components are named "Yaob N10K network" and "Yaob N119M network".
- **Old names still open.** A project, pairing, model or installed release that stored an earlier id (`yaob-origin-n119m`, `yaob-c4b-n10k`,
  `yaob-next-*`, `yaob-mpn14b`) resolves to its product, and a request that names an old model id is answered. Stored names are not rewritten;
  the names shown are mapped when they are read. Nothing is written to a pack or to a network store.
- **Pairings keep installing.** A pairing installed before 0.6.1 still matches its catalogue release when the catalogue uses the new ids.
- **The retired original.** Yaob N10K (`yaob-n10k`, 0.1.0) is no longer offered in the catalogue. Projects that were made from it still open.
- **Package configuration.** A package manifest may say `N10K-C4B` or the earlier `C4B-N10K`; both register the same product.

## 0.6.0 (2026-11-23)

Pairings answer from native FG-MPN networks.

- **Yaob Origin N119M answers.** A pairing whose network is an FG-MPN store (119.7 million records, 39.86 GB) finds the entities a question names through the
  store's own indexes, follows relations one or two hops, supplies the records as numbered, cited nodes and asks the core, which is the same OpenAI-compatible
  core path as for N10K. No embeddings, no copy of the store, no row is iterated. N10K (a Studio network with its learned router) is unchanged.
- **In place.** A pairing records the store's path and the SHA-256 of its manifest; build and rebind check the manifest and file sizes, and "Verify" re-hashes on
  request. Changing the core never touches or rebuilds the network.
- **Status.** Quarantined records are kept and returned by explicit id with their status; they are never found by title, never evidence, never cited.
- **Packages.** `mpn package inspect|import FOLDER_OR_ZIP` (and Settings, Releases, Pairings): a Yaob package (`manifest.json`, `fylume-package/1`) is verified, its
  network and GGUF files are registered where they are, and its pairing is created. A zip is extracted once into Studio's folder.
- A pairing that pins an FG-MPN-only network release no longer says Studio cannot answer from it. `mpn network import-fgmpn --verify size` registers a large store after a manifest and size check.
- Correction: Yaob Origin N119M stores 280,844,705 edge rows (18,742,569 attached to nodes and 262,102,136 in-edges whose source is not a stored node); the earlier figure of 1,693,590,449 relationships is withdrawn.

## 0.5.0 (2026-11-23)

Cognitive cores and official releases.

- **Cognitive cores on servers.** A core can be any OpenAI-compatible server you run or rent (llama.cpp `llama-server`, vLLM, a hosted
  API): address, model id, optional key, **Test connection**. The key stays in a file only you can read and is never put in a pairing,
  a log or an export.
- **Pairings and rebinding.** A network joined to a core is a pairing. Choosing another core keeps the network, its snapshot and its
  index: **Apply core** reconnects and asks the same test question in about a second. The network's hash does not change.
- **Releases.** Settings, Releases reads the live catalogue (https://fylu.me/api/catalogue.json): networks, cores and prebuilt pairings,
  with resumable, SHA-256 checked downloads and atomic installs. A network release has two payloads: `fgmpn` (read-only, explored) and
  `studio-store` (answers in a pairing). A catalogue or manifest file can be imported.
- **FG-MPN networks open out of the box.** The app carries the public FG-MPN runtime 0.2.2
  (https://github.com/sarja-technologies/fgmpn; native reader on x86-64 and ARM64) and its own reader serves a store read-only: search,
  nodes, relationships and the canvas work as for any network. No Python of yours is needed. Notices: `licenses/fgmpn/` in the app.
  The reader does not list derived statements for a node (the explainer is not in the public runtime); an `fg://` address is looked up under Storage.
- **ARM64 again.** 0.5.0 is built for ARM64 (DGX Spark and other ARMv8.2+ Linux machines) as well as x86-64 (0.4 was x86-64 only).
- Fixes found by running the packages against the live data: a network release that keeps its FG-MPN store in `store/`, manifests that cannot name their own commit, a reader that outlived the app.
- Projects made with 0.4.1 open as they are.

## 0.4.1 (2026-11-14)

The canvas drills down from domains to real nodes in large networks.

## 0.4.0 (2026-11-14)

Projects, a home screen, and large networks opened in place with forks.
