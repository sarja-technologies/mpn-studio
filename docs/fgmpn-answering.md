# Answering from an FG-MPN network (Studio 0.6)

A pairing can now answer from a native FG-MPN store, for example the 119.7 million record Yaob Origin N119M (39.86 GB), with any cognitive core:
the Qwen3.5-4B Q8_0 of Yaob Next, or any other OpenAI-compatible core. Nothing is converted, copied or embedded.

## What is different from a Studio network (N10K)

| | Studio network (N10K) | FG-MPN network (N119M) |
|---|---|---|
| Stored as | one SQLite file; a snapshot is taken into the pairing at build | the store folder, memory-mapped **where it is**; the pairing records its path and the SHA-256 of its manifest |
| Retrieval | learned router (with its encoder) or encoder and text search | **entity linking over the store's own indexes. No embeddings, no encoder, no index to build** |
| Build | snapshot, retrieval index, test question | open the store, ask one test question; seconds, nothing copied |
| Changing the core | Apply core (rebind) | Apply core (rebind): the network's identity does not change |
| Editing | yes | no: read-only |
| `.mpn` export | yes | no (there is no network file to put in it) |

A network release that carries both payloads (N10K) still answers from its Studio payload, so N10K behaves exactly as in 0.5. A release with only an
FG-MPN store (N119M) is read natively. Requirements: the public FG-MPN runtime 0.2.x (carried by the app; native reader on x86-64 and ARM64).

## How a question is answered

1. **Link.** Spans of the question's words, longest first, are looked up by exact title through the store's exact-title index (microseconds per segment,
   whatever the word's frequency). A parenthetical description ("(Dutch noblewoman)") is a hint, not a name. A name with no exact title is searched in
   titles and aliases. Records with one title are ranked by how many of the question's other words their description holds, then by kind, dataset and
   Wikipedia coverage; when the best two tie, both are given. A 32-hex record id in the question is an anchor of its own.
2. **Read.** A linked record becomes a node: its description and the facts of its **validated** claims. A value that is a bare Wikidata id whose label the
   network does not hold is left out: it cannot be read or cited.
3. **Follow.** One or two hops along facts whose value is another record: when the relation's wording matches the question ("born" is "place of birth"),
   or when the question asks for something the first record lacks ("the country of the place where X was born"). Fan-out 3 at the first hop, 1 at the
   second; at most the *nodes read per answer* setting plus two nodes. The core can also call `mpn_search` and `mpn_follow` (the same tools as with N10K),
   served by the same reads.
4. **Supply and cite.** Nodes are numbered [1], [2], ... in the order given to the core. A citation [n] maps to that node's id, key (`wd:Q...`),
   dataset, record, revision, licence and URL (`validation.citations`). Citations that name no supplied node, or whose sentence the node does not
   support, are removed, as with N10K.

Nothing iterates the store. Every read is an index read: the exact-title index, the text index, the id and alias index, a subject's claim range.

## Status: validated and quarantined

The N119M store holds 556,467 quarantined records beside its 119,176,842 validated ones (all quarantined records are Wikidata claims, for example
links to Commons categories). They are **kept**, and:

* returned by explicit id, with their status (`GET /api/fgmpn/v1/node/<id>`, and a question that contains the id: the answer is told the record is quarantined);
* never found by title or search, never listed as a subject's claim, never followed from an edge, never given to the core as evidence, never cited.

## Settings that matter

*Nodes read per answer* (default 4, plus up to two linked nodes) and *Tool rounds* (default 2) are the same as for N10K. With tool rounds 0 the core
answers from the nodes supplied up front.

## Package import

`mpn package import <folder-or-zip>` (Settings, Releases, Pairings: **Import package folder** / **Import package zip**) takes a Yaob package
(`manifest.json`, schema `fylume-package/1`), verifies its checksums, registers the network and the GGUF files where they are, and creates the pairing.
A zip is extracted once into Studio's folder. `--verify size` checks files and sizes only. See `mpnstudio/catalogue/package.py`.

## Verifying on request

`mpn release verify` re-hashes every segment of a registered store against its manifest. Registration, build and rebind check only the manifest and sizes.

## The Origin network's relationship count

Yaob Origin N119M stores **280,844,705 edge rows**: 18,742,569 attached to nodes, and 262,102,136 in-edges whose source is not a stored node.
The figure of 1,693,590,449 relationships that appeared in earlier release text is withdrawn; it was not a count of stored rows.

## Measured

On the NVIDIA DGX Spark, with Yaob Origin N119M read where it is (119,733,309 records, 39.86 GB) and two llama.cpp servers as cores (Qwen3.5-4B Q8_0 and
Qwen3-4B-Instruct-2507 Q8_0), 334 questions, each core alone and with the network:

| | Qwen3.5-4B | Qwen3-4B-Instruct |
|---|---|---|
| correct alone | 93 | 104 |
| correct with the network | 320 | 317 |
| citations (all name a supplied, validated node) | 327 | 289 |
| answer time with the network, p50 / p99 | 2.08 s / 14.6 s | 1.70 s / 8.2 s |
| retrieval, p50 | 15.0 ms | 18.8 ms |

Quarantined records were in no context given to a core. Retrieval alone, with part of the store's pages dropped from the page cache first: cold p50 9 ms, p99 297 ms; warm p50 1.5 ms.
The backend's resident set peaked at 7.0 GiB, 5.3 GiB of it file pages of the store; opening the store takes 3.3 s. A package of 131 files (42.9 GB, the store and core linked, not copied)
imported with `--verify full` in 66 s using 46 MiB, built in 8.1 s, answered, and was rebound to a second core with the network's hash unchanged.
Not measured: CUDA or Vulkan inference with Studio's own llama.cpp, x86-64 with this store, other cores, languages other than English. The question sets, raw rows and scripts are not published in this repository.
