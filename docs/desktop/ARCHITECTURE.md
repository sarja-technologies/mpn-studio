# Desktop architecture

```
 MPN Studio (Tauri 2, Rust)  ── one process, one window (WebKitGTK)
   │  loads the bundled UI from tauri://localhost   (no web server, no remote URL)
   │  app commands: backend_info, pick_folder, pick_file, reveal_path, open_external, set_window_title, quit_app
   │
   └─ starts ───►  mpn-backend  (PyInstaller bundle of the unchanged mpnstudio API; no Python needed on the machine)
                    127.0.0.1:<random port>, per-launch token (header x-mpn-token), allowed origin tauri://localhost
                    SQLite project store · board geometry · search · bundles · jobs queue · optional runtime proxy
                      │
                    library: cognitive cores · node networks (projects) · models · tasks (import, build, export, bulk delete)
                      │
                      ├─ per running model: mpn-backend serve-root  (127.0.0.1:<random port>, random API key)
                      │     MPN runtime: routing, MPN tools, evidence, OpenAI-compatible /v1 API
                      │       ├─ llama-server: the cognitive core (+ projector)      runtime/llama/{cuda,vulkan,cpu}
                      │       └─ llama-server: the retrieval encoder, if the model has one
                      ├─ local compute (optional): a Python you choose, with torch + the Yaob 0.1.0 runtime  [Settings, Compute]
                      └─ remote compute (optional): SSH tunnel to a machine running MPN Studio (e.g. DGX Spark)

 mpn  (the same mpn-backend, started as `mpn-studio mpn …` or /usr/bin/mpn)
       run / info / verify .mpn files, core / network / model commands, without a window
```

* **The UI is the original Svelte application** (`web/`), changed only where a desktop needs it: a menu bar, project
  chooser, settings (storage, compute, backups), help with the official names, a Datasets panel, native file dialogs,
  API base/token injection (`lib/api.ts`), and fixes found during testing (dock widths clamped to the window, board
  shortcuts suppressed while an overlay is open, per-project library label).
* **The backend is the original FastAPI app**, plus `mpnstudio/desktop/` (XDG paths, settings, projects, compute) and
  `/api/desktop/*` routes. In desktop mode (`MPN_DESKTOP=1`) it starts with no project open, opens one in-process
  (`open_project` swaps the `Store`; nothing else holds the database), and answers only requests that carry the token.
* **Lifecycle.** The shell picks a free port and a random token, starts the backend with `PR_SET_PDEATHSIG`
  (the backend gets SIGTERM even if the shell is killed), waits for `/api/health`, and on exit sends SIGTERM so
  SQLite is checkpointed (SIGKILL after 8 s). A damaged or missing backend shows a message with the log path
  (`~/.local/state/mpn-studio/logs/backend.log`) instead of a blank window. A second launch focuses the first.
* **The window can only show the bundled UI.** Navigation elsewhere is refused; provenance links open in the system
  browser only when clicked. CSP allows connections to `127.0.0.1` only.
* **Nothing is fetched at runtime**: fonts, scripts and icons are bundled; no telemetry, licence check or account.

## Model builder

A **model** is a cognitive core, a node network and the settings that join them. The Studio (Models, Cognitive cores,
Node networks, Tests) manages the three; the board stays the network editor.

* **Cognitive cores** are GGUF files, stored once under the first 16 hex digits of their SHA-256 and shared by every
  model that uses them (`cores/<sha16>/model.gguf` + `core.json` with the GGUF metadata: architecture, parameters,
  quantization, context length, licence). *Reference in place* keeps a large file where it is and records its hash. A
  Hugging Face folder is converted to GGUF by llama.cpp's converter in a Python you choose (Settings, Compute). An
  embedding GGUF is a **retrieval encoder**; a vision projector attaches to its core.
* **Node networks** are the projects. Duplicate, specialise (copy the nodes that match a filter), merge (by `uid`, with
  conflicts skipped or kept as both), compare, statistics and bulk delete run in the backend (`mpnstudio/store/netops.py`).
  Bulk delete is one revision whatever the count, runs as a cancellable task, and is undone as one step.
* **Build** (`mpnstudio/library/models.py`) checks that core, encoder and network fit, takes a read-only snapshot of the
  network at its current revision, picks routing (the network's learned router when it has one that matches; otherwise
  retrieval over the encoder's embeddings and the full-text index; otherwise text search only), starts the model, asks a
  test question chosen from the network, and records whether the answer came from the expected node. A model whose core,
  network or settings changed shows **Needs a build**.
* **Run.** Each running model is its own `mpn-backend serve-root` process with its own loopback port and API key. It
  starts `llama-server` for the core (and one for the encoder) from the first of `cuda`, `vulkan` and `cpu` that lists a
  usable device (`llama-server --list-devices`; a software Vulkan device does not count), and tries the next if it fails
  to start. The Studio's chat goes through the backend to that process and shows the evidence: the nodes read, cited and
  checked, tool calls, timing and the process tree's measured memory. *Connect another app* shows the base URL, model
  name and key for any OpenAI-compatible client.
* **Export** writes one `.mpn` file ([`docs/MPN-FORMAT.md`](../MPN-FORMAT.md)): complete (with the GGUF files) or
  lightweight (the core named by hash). `mpn run` and *Open .mpn file* read it back.
* **Bench** (Tests) asks a built model a list of questions and writes the answers, evidence and timings to
  `bench/*.json` and `*.csv`; it refuses a model that is not built.

## Data

| Location | Contents |
|---|---|
| `~/.config/mpn-studio/settings.json` | last project, large-asset folders, compute settings |
| `~/.local/share/mpn-studio/projects/<id>/` | `project.json` + `store.sqlite` (+ `banks/`, `bundles/`, `packages/` on demand) |
| `~/.local/share/mpn-studio/cores/<sha16>/` | cognitive cores and encoders (`model.gguf`, optional `projector.gguf`, `core.json`) |
| `~/.local/share/mpn-studio/models/<id>/` | `model.json` (core, network, settings, identity, last build) and `build/` (network snapshot, router, build report) |
| `~/.local/share/mpn-studio/bench/` | test runs (`.json` and `.csv`) |
| `~/.local/share/mpn-studio/backups/<id>/*.mpnbak` | checksummed backups (tar.gz) |
| `~/.local/share/mpn-studio/trash/` | "deleted" projects (moved, not destroyed) |
| `~/.cache/mpn-studio/` | disposable: models extracted from opened `.mpn` files |
| `~/.local/state/mpn-studio/logs/` | backend log |

`XDG_*_HOME` apply; `MPN_STUDIO_{CONFIG,DATA,CACHE,STATE}_DIR` override one location.

* **Save.** Every edit is a revision committed as it is made; *Save* folds the write-ahead log into the file and
  records a manifest summary. *Undo/redo* are revisions (unchanged from the original).
* **Migration.** Opening a project whose manifest or store schema is older makes a verified backup first, then upgrades;
  a store from a *newer* MPN Studio is refused rather than guessed at. `format: mpn-studio-project/1`.
* **Interchange** uses the existing verified formats (`yaob-bundle/1`, `yaob-mpn-package/1`), not database files.
  *Import store file* adopts a raw store (WAL-safe copy, integrity-checked).
* **Canonical nodes vs appearances** is unchanged: a node is one row with several memberships; the board stores only
  geometry, so a shared node is never duplicated.

## Local vs remote compute

Local mode is the default and needs nothing. *Editing, search, inspection, import/export and backups* always work.
*Local inference* and *local training* are shown as **unavailable, with the reason**, unless a Python environment with
torch, transformers, peft and safetensors, and the weights, are configured and found. With those, **Start local
runtime** (Settings, Compute) runs `python -m mpnstudio.runtime.server` from the plain sources shipped in
`backend/pysrc` (cwd = that folder; no install into your environment) on a random loopback port, with the open
project's store and data folder, your models folder, and the Hugging Face hub in offline mode. The job worker
(`python -m mpnstudio.train.jobs worker`) starts with it only if that Python sees a CUDA GPU. Both stop on Stop, on
switching or closing the project, and on quitting; a training job that is already running is its own detached process
and keeps going. Until the worker runs, job submit/resume answer 409 with the reason. The app never falls back to a
fixed runtime port on this computer.
Remote mode is an SSH tunnel (key auth through your ssh config,
`BatchMode`, no password stored) that forwards the remote API and runtime ports to loopback; the local API then proxies
Run/chat to the remote runtime, and the remote job list is shown read-only. Remote answers come from **the remote
machine's own network**, not from the open local project. Disconnecting, or the remote being down, falls back to local.
