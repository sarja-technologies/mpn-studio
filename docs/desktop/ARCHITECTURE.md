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
                      ├─ local compute (optional): a Python you choose, with torch + the Yaob runtime  [Settings, Compute]
                      └─ remote compute (optional): SSH tunnel to a machine running MPN Studio (e.g. DGX Spark)
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

## Data

| Location | Contents |
|---|---|
| `~/.config/mpn-studio/settings.json` | last project, large-asset folders, compute settings |
| `~/.local/share/mpn-studio/projects/<id>/` | `project.json` + `store.sqlite` (+ `banks/`, `bundles/`, `packages/` on demand) |
| `~/.local/share/mpn-studio/backups/<id>/*.mpnbak` | checksummed backups (tar.gz) |
| `~/.local/share/mpn-studio/trash/` | "deleted" projects (moved, not destroyed) |
| `~/.cache/mpn-studio/` | disposable |
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
