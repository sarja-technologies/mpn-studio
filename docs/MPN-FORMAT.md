# The .mpn model file (`mpn-model/1`)

A `.mpn` file holds one MPN model: a cognitive core, a node network, and everything the MPN runtime needs to
answer from both. MPN Studio writes it with **Export** on a model's page; `mpn run` and MPN Studio's
**Open .mpn file** read it. The reference implementation is [`mpnstudio/library/mpnfile.py`](../mpnstudio/library/mpnfile.py).

A `.mpn` file is not a GGUF file, and llama.cpp alone cannot run it. The MPN runtime opens it, gives the GGUF members
to llama.cpp's `llama-server`, and does the retrieval over the node network itself.

## Two kinds

| Kind | Contains | Use |
|---|---|---|
| Complete | the core (and encoder and projector, if the model has them) plus everything below | One file that runs on any computer with MPN Studio or the `mpn` command, offline |
| Lightweight | everything except the GGUF weights, which are named by SHA-256 | Small. Runs where the same core is already in MPN Studio's library, or is passed with `--core` |

A lightweight file never runs with a different core: the runtime hashes the core it finds (or is given) and refuses
it if the SHA-256 differs from the one in the manifest.

## Layout

All integers are little-endian.

```
offset 0     header, 64 bytes
               magic          8 bytes  "MPNMODEL"
               version        u16      1
               flags          u16      bit 0 set: lightweight
               reserved       4 bytes  zero
               index_offset   u64
               index_length   u64
               index_sha256   32 bytes SHA-256 of the index bytes
offset 64…   members, stored uncompressed, each starting on a 64 KiB boundary
end          index: UTF-8 JSON
```

Members are aligned and uncompressed so a reader can memory-map a GGUF member, or clone it block for block on a
filesystem with reflinks, without copying it through memory.

The index is

```json
{
  "format": "mpn-model/1",
  "manifest": { … },
  "members": [ {"name": "core/model.gguf", "kind": "core", "offset": 65536, "size": 4610580384, "sha256": "…"}, … ]
}
```

## Members

| Name | Kind | Present |
|---|---|---|
| `model.json` | `config` | always: the same manifest as the index, for tools that only extract files |
| `NOTICE.txt` | `notice` | always: licences and attributions of the core, encoder and network sources |
| `network/network.sqlite` | `network` | always: the node network, a read-only snapshot of an MPN Studio store at one revision |
| `core/model.gguf` | `core` | complete files |
| `core/projector.gguf` | `projector` | complete files of cores that have a vision projector |
| `encoder/model.gguf` | `encoder` | complete files of models that use an embedding encoder for retrieval |
| `router/manifest.json`, `router/router.safetensors`, `router/node_emb.f16.npy`, `router/nodes.json`, `router/shards/*.safetensors` | `router` | models with a trained router (Yaob); others route by retrieval |

Member names are relative paths with `/` separators. A reader rejects names that are absolute, contain `..`, or
repeat.

## Manifest

The fields a reader relies on:

| Field | Meaning |
|---|---|
| `name`, `model_id`, `version`, `description` | The model's display name, a stable identifier, its version, a description |
| `identity` | The instruction that tells the model who it is |
| `core` | `name`, `sha256`, `bytes`, `architecture`, `parameters`, `quantization`, `context_length`, `license`, `license_link`, `base_models`, `included`, `member`, and `projector` (same shape) when there is one |
| `encoder` | Same fields as `core`, or `null` when retrieval is text search only |
| `network` | `member`, `id`, `revision`, `nodes`, `relations` |
| `router` | Description of the trained router, or `null` |
| `retrieval` | `routing` (`auto`, `router`, `retrieval`), `retrieval_candidates`, `max_active_mpns`, `tool_rounds`, `selected_nodes`, `text_nodes` |
| `context` | `tokens`, `parallel`, `output_tokens` for the server |
| `requirements` | `runtime` (`mpn-model/1` reader), `llama_cpp_architecture` (from the GGUF), `llama_cpp` (the build tested) |
| `licence` | The licence the model's author chose and notes, plus `notices_member` |
| `provenance` | How it was built: build time and device, and the build's test question, answer, evidence nodes and timing |

Readers ignore fields they do not know. A change that an older reader would misread raises `version`.

## Integrity

- Opening a file reads the header and the index and checks `index_sha256`. That is cheap and happens every time.
- `mpn verify FILE` hashes every member against the index.
- Extracting a member hashes it as it is written and deletes the result on a mismatch. `mpn run` extracts into a cache
  keyed by the file's id, and checks the cache before using it.
- Nothing in a `.mpn` file is executed. Members are data: GGUF weights, a SQLite database opened read-only,
  safetensors and NumPy arrays loaded without pickle, JSON and text.

## Running one

```sh
mpn info  Model.mpn                       # what is inside, without extracting
mpn verify Model.mpn                      # hash every member
mpn run   Model.mpn --ask "question"      # one answer, then exit
mpn run   Model.mpn --chat                # chat in the terminal
mpn run   Model.mpn                       # OpenAI-compatible API on http://127.0.0.1:8850/v1
mpn run   Model-lightweight.mpn --core /path/to/core.gguf
```

The API listens on `127.0.0.1` unless `--host` says otherwise; any other address needs `--api-key` (or `MPN_API_KEY`),
and then every request must send `Authorization: Bearer <key>`. `--device` picks `cuda`, `vulkan` or `cpu`; the default
picks the first that works.

In the installed app the command is `mpn` (the `.deb` installs `/usr/bin/mpn`) or `MPN-Studio_*.AppImage mpn …`.
