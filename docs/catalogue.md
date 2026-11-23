# Cores, networks and official releases (MPN Studio 0.5)

A **network** and a **cognitive core** are separate things. A network is data (an MPN, never model weights). A core is a
language model. A **pairing** joins one of each, plus settings. Studio configures, binds and runs the pairing; it never
changes a network when the core changes, and never changes a core when the network changes.

| Thing | Where it comes from | Kept as |
|---|---|---|
| MPN network | a project (blank, imported `.mpn` network, store file), an official release, an FG-MPN folder | project folder, or `<data>/releases/mpn/<id>/<version>/` |
| Cognitive core on a server | any OpenAI-compatible server you run or rent | `<data>/core-servers/<id>.json`; key in `<config>/secrets.json` (mode 0600) |
| Cognitive core served by Studio | a GGUF file (imported, converted, or an official release) | `<data>/cores/<sha16>/model.gguf`, run by llama.cpp as before |
| Pairing (what 0.4 called a model) | you, or an official `model` release | `<data>/models/<id>/model.json`; `build/` holds the network snapshot and index |

`.mpn` files are unchanged: a complete `.mpn` still bundles core, encoder, network and router. A pairing whose core is on a
server has no weights, so it cannot be exported as a `.mpn` (Studio says so); every other `.mpn` feature works as before.

## Changing a core

A pairing's build (snapshot of the network, retrieval index, router choice) does not depend on the core. Choosing another
core marks the pairing "The core changed since the last test. Apply it: the network is kept." **Apply core**
(`POST /api/library/models/{id}/rebind`, `mpn model rebind ID`) rewrites the runtime manifest, connects to the new core and asks
the same test question. It takes about a second for a server core. If the new core does not answer, nothing changes. If the
network, encoder or settings changed too, it refuses and asks for a rebuild.

## Cores on servers

Fields: name, server address (the OpenAI base URL; `http://host:port` gets `/v1` appended), model id, optional API key (or the
name of an environment variable to read it from), optional context size, output limit and timeout.

* The key is stored only in `secrets.json`. APIs return `has_key` and where it comes from, never the key. It is not written to a
  pairing's `MANIFEST.json`, a log, an error message or an export; a runtime process gets it as `MPN_CORE_API_KEY` in its environment.
* **Test connection** lists the server's models, then asks the model for a few tokens. Failures say what to do:
  `unreachable` (nothing listening, or unknown host), `timeout`, `auth` (no key, or a rejected key), `model_not_found` (with the
  models the server offers), `bad_url` (the chat route is not at that address), `not_openai`, `request_rejected`. It also
  reads the context size from llama.cpp `/props` or vLLM `max_model_len`.
* The engine sends llama.cpp-only fields to no server. Sampling extras and `chat_template_kwargs` are sent; if a server answers 400
  or 422 the request repeats with plain OpenAI fields. Without `/tokenize` and `/apply-template`, prompt sizes are estimated high
  so a prompt is never packed past the context. The retrieval encoder is still a local GGUF.

## The catalogue

Setting `catalogue.url` in `settings.json` (Settings, Releases), or the environment variable `MPN_STUDIO_CATALOGUE_URL`.
Default `https://fylu.me/api/catalogue.json`, the website's catalogue. It may also be a file path. `catalogue.hf_endpoint` (or `HF_ENDPOINT`) replaces
`https://huggingface.co`; `HF_TOKEN` is sent only to that host. Two shapes are read, and may be mixed with imported files.

**The website's own** (`schema: "fylume-catalogue/1"`, `{updated_at, lang, models[], mpns[], apps[], cores[]}`), read natively (`mpnstudio/catalogue/site.py`):

| Entry field | Used for |
|---|---|
| `id`, `name`, `version`, `released_at`, `status`, `summary` | listing (`id` must be a lowercase slug) |
| `licence` (free text) | required for anything installable; the first 48 characters are shown, the whole text on hover |
| `download {type, url, filename, bytes, sha256}`, `links {release, docs}` | only for an entry Studio cannot install: shown as a download link |
| `files[] {name, url, bytes, sha256, role, format, mirrors}` | the files to download, from their own pinned `url`s; a file with no exact `bytes` and `sha256` (a `SHA256SUMS`) is never downloaded; `name` is the path inside the install and must be relative |
| `mpns[].mpn {format, formats, format_version, manifest_format, nodes, relationships, read_only, studio_min}` | compatibility, payload choice, display |
| `cores[].core {formats[{kind, quant, file}], parameters, architecture, derivation, context_length, base_model}` | which GGUF to download (`file` matches a `files[].name`, or its basename) |
| `models[].model {pairing, mpn{name,nodes,version}, core{name}, components[{kind, id, name, version, download}], studio_min}` | a prebuilt pairing; `components[]` say which `mpns[]`/`cores[]` entries it is made of (`kind` mpn, core or encoder) |
| `apps[]`, `previous_versions[]`, `install_md`, `slug`, `lang` | ignored |

Legacy entries (a `.tar.gz` network with no `mpn.format`, a single `.mpn` bundle, a GitHub release page; `models[]` with no `components`) are kept and shown
as "Download only, not installable by Studio" with their link. They are not errors; installing one answers HTTP 422 `download_only`.

**A list of manifests** (`{"schema": "fylume-catalogue/1", "releases": [...]}`, a bare list, or one bare manifest), each a `fylume-release/1` manifest or `{manifest_url, manifest_sha256}`:

```json
{ "schema": "fylume-catalogue/1", "generated_at": "2026-11-22",
  "releases": [ { "schema": "fylume-release/1", "...": "a whole manifest" },
                { "manifest_url": "https://.../fylume-release.json", "manifest_sha256": "..." } ] }
```

A catalogue file saved from the site can be imported with **Import manifest file**; each entry is kept on its own.
The last good copy is cached, so the catalogue still lists releases offline. Studio reads the catalogue when it opens if it was never read or is over six hours old.

### Two payloads in one network release

An official network carries both payloads in one repository: `mpn.format = "fgmpn"` (primary, read-only, explored) and `mpn.formats = ["fgmpn", "studio-store"]`.
Each `files[]` entry may carry `format`: `"fgmpn"`, `"studio-store"`, or nothing (documents). Any other value (the website also uses free-text descriptions there) is ignored.

* `fgmpn` files are the store: `CURRENT`, `manifests/`, `segments/` (and `stats/`, `index/`), at the repo root or in a folder such as `store/`.
* `studio-store` files are `studio/network/yaob-n10k.sqlite` and `studio/network/checkpoint/**`, so `mpn network import-yaob <repo>/studio` works as it is.

Studio installs only the payload it needs, plus small documents: the Releases view offers "Explore, read-only (FG-MPN)" or "Use in pairings (Studio network)" when both exist
(default: the primary one); a pairing install always takes `studio-store`. The two installs are separate and can coexist (`releases/mpn/<id>/<version>` and `<version>.studio-store`);
the Studio payload becomes an ordinary network project, with its router attached. The trained router only works with the encoder it was trained with: when a pairing pins an encoder
(`model.encoder`, same shape as `model.core`) Studio installs that GGUF into the encoder slot of the library, records its SHA-256 as the router's encoder (the same effect as placing it
at `studio/models/encoder/encoder.gguf` before `import-yaob`) and puts it in the pairing. A pairing that pins an mpn takes its `studio-store` payload when it has one (N10K: the learned router and encoder) and otherwise its `fgmpn` store, which it reads where it is
installed, natively, with no embeddings (N119M; see [fgmpn-answering.md](fgmpn-answering.md)). A release with neither is refused before anything is downloaded.

### Manifest fields Studio relies on (`fylume-release/1`, see the website's `docs/release-manifest-spec.md`)

| Field | Used for |
|---|---|
| `schema`, `kind` (`mpn` `core` `model`), `id`, `name`, `version`, `released_at` | listing; `id` must be a lowercase slug |
| `licence.spdx` | required: a release without one is shown as invalid, not installable |
| `hosting.primary` `{type: huggingface, repo, revision}` | download from `{hf_endpoint}/{repo}/resolve/{revision}/{path}`; `revision` must be a commit sha |
| `hosting.primary` `{type: github, repo, tag}` / `{type: https, base_url}` | `releases/download/{tag}/{basename}` / `{base_url}/{path}` |
| `hosting.mirrors[]` | tried in order if the primary lacks a file or serves a damaged one |
| `files[] {path, bytes, sha256}` (`role`) | every file is downloaded with Range resume and checked for exact size and SHA-256; paths must be relative, without `..` |
| `mpn.format`, `mpn.formats[]` (`fgmpn`, `studio-store`), `mpn.format_version`, `mpn.manifest_format`, `files[].format` | payload choice and compatibility. FG-MPN: `0.2.x`, manifest `fgmpn-manifest/1` or `/2` |
| `mpn.nodes`, `mpn.relationships`, `mpn.read_only`, `mpn.studio_min`, `mpn.verification` | shown, and `studio_min` is enforced |
| `core.formats[] {kind: gguf, quant, file}` | which file to download (`file` must be in `files`); other kinds are listed, not installable |
| `core.parameters`, `architecture`, `derivation`, `context_length`, `base_model` | shown; `derivation: adapter` is not installable here |
| `model.pairing`, `model.mpn`/`model.core` `{id, version, repo, revision, manifest_sha256}` | a prebuilt pairing; `manifest_sha256` is the SHA-256 of the pinned `fylume-release.json` bytes |
| `model.encoder` | optional pinned embedding model (same shape as `model.core`), installed into the encoder slot |
| `model.defaults.model_id`, `defaults.settings` (`retrieval`, `context`, `identity`) | the new pairing's settings |
| `model.runtime.studio_min` | enforced |

Only the `studio-store` payload can answer in a pairing today; `fgmpn` is explored read-only (below). A single-payload manifest with `mpn.format = studio-store` and one `.sqlite` file still installs. Unknown values are never invented: a missing field is shown as missing.

### Install

`<data>/releases/<kind>/<id>/<version>.partial/` is where files are downloaded and verified; the folder is renamed into place only
when every listed file matches and the store itself checks out (FG-MPN: `CURRENT`, its manifest, every segment present). A
stopped install resumes from the partial folder. Free space is checked before downloading (needed bytes plus 64 MiB).
Errors are named: `disk_space`, `unreachable`, `timeout`, `auth`, `not_found`, `http`, `size_mismatch`, `checksum_mismatch`,
`incompatible` (studio_min, FG-MPN version), `missing_component`, `revision_mismatch`, `cancelled`. A damaged file is deleted,
never installed.

A prebuilt pairing resolves the pinned manifests from the catalogue when their SHA-256 equals the pin, otherwise from the pinned
repository and revision, and refuses any manifest that is not the pinned bytes. It installs the network and the core (or, with
"Use my server", only the network), then creates a pairing that refers to them. The installed network stays an ordinary
network: usable with any other core. A core installs into the core library like an imported GGUF, without a second copy.

## FG-MPN (read-only)

Studio installs, verifies (every segment against the store's own manifest) and lists FG-MPN stores without any FG-MPN code. To
explore one it starts its own reader (`mpnstudio/mpnnet/fgmpn_net.py`, run as `mpn-backend fgmpn-serve` in the packaged app) on a
loopback port and opens it as a protected linked project, so search, inspector and canvas are the ones every network uses, plus
the FG-MPN panel (`fg://` address lookup with its checked proof, storage). The reader is Studio's (Apache-2.0); it reads the store
with the public FG-MPN runtime, the `fgmpn` package (<https://github.com/sarja-technologies/fgmpn>, Fylume Source-Available Terms
1.0), which the packaged app **carries** (the 0.2.2 wheel for the machine, SHA-256 pinned in `scripts/fetch-fgmpn.sh`; notices in
the app's `licenses/fgmpn/`). From source, install `fgmpn` 0.2.x in the Python Studio runs in. The native reader (Linux x86-64
glibc 2.17+, Linux ARM64 glibc 2.34+) is used when it imports, the Python engine otherwise; `FGMPN_ENGINE=auto|native|python` chooses.

Counts by domain, kind and dataset come from the store's `stats/gen-N.json` (published stores ship it); a store without one is counted
once when Studio installed it (never written into a folder you keep, and not above two million nodes). The public runtime has no
network server and no explainer: the node panel does not list derived statements, and "Compare with the original" is not offered
(there is no original network to compare with); an `fg://` address is looked up, with its proof checked independently, under Storage.
A release may keep its store in a folder (the published Yaob N10K packages use `store/`); Studio finds it by its `CURRENT` file.

An FG-MPN network cannot be used by a pairing yet: the answering engine reads Studio stores. A pairing that pins an mpn without a Studio payload is refused with that explanation.

## Command line

```
mpn core add-server NAME --url URL --model-id ID [--api-key-env VAR | --api-key-stdin] [--context N] [--max-output N]
mpn core test SERVER_ID | servers | remove-server SERVER_ID
mpn model create NAME --core endpoint:SERVER_ID --network ID ; mpn model build ID ; mpn model rebind ID
mpn catalogue list|refresh|url|import FILE|install ID [--version V] [--format QUANT] [--server SERVER_ID]
mpn release list|verify ID VERSION|remove KIND ID VERSION
mpn network import-mpn FILE.mpn [NAME] | import-fgmpn FOLDER [--copy]
```

## Where the code is

| | |
|---|---|
| `mpnstudio/catalogue/` | `manifest.py`, `source.py`, `download.py`, `install.py` |
| `mpnstudio/library/coreconf.py` | core servers, secrets, Test connection |
| `mpnstudio/engine/extcore.py` | the engine's view of a core on a server |
| `mpnstudio/library/models.py` | pairings: `build`, `rebind`, `create_from_release` |
| `mpnstudio/mpnnet/fgmpn_local.py`, `fgmpn_serve.py` | FG-MPN stores on this computer |
| `mpnstudio/api/catalogue_routes.py` | `/api/catalogue`, `/api/releases`, `/api/core-servers` |
| `web/src/lib/studio/` | Cores, MPN networks, Pairings, Releases, CoreServerDialog |
| `tests/test_catalogue*.py`, `test_release_install.py`, `test_core_servers.py`, `test_fgmpn_*.py` | with `relhost.py` (a Hugging Face stand-in) and `fakecore.py` |
