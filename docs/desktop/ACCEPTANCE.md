# Acceptance evidence (2026-11-12/13, extended 2026-11-13)

## 1. Offline end-to-end test of the installed application

`scripts/offline-acceptance.sh` runs the **installed** app (`/usr/bin/mpn-studio` from the .deb, and separately the
AppImage) inside a private network namespace (`bwrap --unshare-net`): the only interface is loopback, so a LAN compute
host (set `MPN_ACCEPT_LAN_HOST` to assert one) and the internet are unreachable. The host's networking is not touched. It drives the
real WebKitGTK window in a virtual X display with synthesized mouse/keyboard input and a native GTK file dialog,
checks the project file on disk, and records every `connect()` the app and its children make (strace).

Result on the final packages (13 Nov 2026): **37 passed, 0 failed, 1 skipped** for the .deb and **35 passed, 0 failed,
1 skipped** for the AppImage (two deb-only checks of the installed desktop entry and MIME type do not apply to an
AppImage) (`acceptance/results-deb.txt`, `acceptance/results-appimage.txt`).

The skipped check is the native GTK folder dialog of *File, Export*. On the final run it opened but did not accept the
typed folder (it stayed in its "Recent" view with *Open* disabled), although it had in the earlier run of the previous
build and the app's dialog code did not change; the cause was not determined. The test cancels the dialog and exports
through the same API call the menu makes, so the import, backup and `.mpnbak` steps after it still ran, and the result
says so. The dialog itself is therefore unverified on the final packages. Screenshots 01, 03, 07, 14 and 15 are from the
earlier run of the previous build.

| # | Requested step | Verified by |
|---|---|---|
| 1-4 | Shut down; no Spark; offline; launch from the installed entry | window opens; first run shows the project selector with the migrated network (screenshot 01; home-directory paths in screenshots 01, 17 and 18 are pixelated) |
| 5 | Open an existing locally migrated network | Open: project `yaob-n10k`, rev 8, 10,000 nodes, 19 MPNs |
| 6 | Inspect nodes and cells | search jumps to *Cache (computing)*, Knowledge cell and provenance shown (03) |
| 7-8 | Modify a node, save | statement edited in the inspector, **Save**, revision 9; Ctrl+S |
| 9 | Close | File > Quit shortcut; process exits; **no backend left behind** |
| 10-11 | Reopen offline; changes persisted | marker found in `store.sqlite` after shutdown (head rev 9); app reopens straight into the last project at rev 9 and shows the edit (07) |
| 12 | Create a new network | Ctrl+N, name typed, project created and opened; MPN and node added |
| 13 | Export | Ctrl+Shift+E, native folder chooser, `yaob-bundle/1` written |
| 14 | Import into another local workspace | second HOME/XDG tree: bundle imported, reopened into it, node found (14) |
| 15 | No mandatory remote calls, no remotely hosted assets | strace: 0 connects to non-loopback addresses in every app run (8 of them) (only the UI-to-backend link and the backend's loopback probe for a local runtime that is not running). Shipped JS/CSS/HTML/SVG reference no hosts other than XML namespaces, a font-licence comment and Svelte error-message strings; no external `<script>`, `<link>` or `@import` |
| 16 | A `.mpnbak` backup opens from the system | the deb registers `application/x-mpn-studio-backup` for `*.mpnbak` (`xdg-mime` reports it) and the desktop entry is `Exec=mpn-studio %F`; launching the app with a backup path opens it as a **new** project (15); a second launch hands its file to the running window |
| 17 | Start and stop the local runtime from Settings | the installed app, a profile pointing at a CPU-torch Python: **Start** runs the runtime from the sources inside the package, status shows the real model-load error for an empty weights folder (17); **Stop** returns to idle (18) and no runtime process remains |

"Spark unreachable" is covered by the same run: it cannot be reached from the namespace, and the app never needed it.

## 2. Original whiteboard test suite, before vs after

The 20 original Playwright tests (system Chrome) were run against the **unmodified baseline commit** and against the
desktop backend, both on the same real N10K snapshot: **identical results, 12 pass / 8 fail, the same 8** (tests 2, 3, 7,
8, 10, 13, 16, 19). The 8 were written for the synthetic fixture store ("Synthetic topic 12", "Arithmetic mean", exact
layout positions) or need a running job worker; they fail on the original with real data too. **No regression
from the migration.** They remain as a harness (`web/e2e`) and are not claimed as passing here.

## 3. Other checks

* `pytest`: 71 pass, 29 skipped (need torch): 16 new tests cover XDG paths, project lifecycle, WAL-safe import,
  backup/verify/restore (tampering detected), manifest migration with backup-first, newer-schema refusal, checkpoint
  relocation, session token and origin rules, project switching and persistence, bundle export/import round trip.
* Real window on the user's Wayland session: starts, the WebKit web process stays up, and the access log shows the
  board, viewport tiles and node requests served by the local backend.
* Remote compute against the real Spark (read-only GETs through an SSH tunnel): connect, API health (rev 8), runtime
  reported off with its reason, the Spark's 3 jobs listed, clean disconnect (no tunnel left), clean error for an
  unresolvable host, local mode unaffected.
* Killing the shell leaves no orphaned backend (`PR_SET_PDEATHSIG`); normal quit shuts the backend down cleanly.
* Bugs found by this testing and fixed: `rt()` helper shadowed by a local (500 on `/api/status`); corrupted backup
  raised a raw JSON error; typed letters leaked to board shortcuts while the project chooser was open; saved dock
  widths could squeeze the board to zero, and a project that carries dock widths saved on a larger screen (the migrated network) left the board only 420 px: restored docks now leave it at least half the window; the library header said "N10K" for every project; "1 nodes, 1 MPNs";
  jobs/resume could queue work that nothing would run.

## 4. What is still incomplete

* **Local inference and local training need your own Python environment and weights.** The packaged app contains no
  torch and no weights. *Settings, Compute* can start the Yaob runtime (and, with a usable CUDA GPU, the job worker)
  from a Python you choose; it refuses with the reason when that Python lacks the model stack, the weights folder is
  not set, or no project is open. What was verified: the launcher against a real Python 3.14 environment with CPU-only
  torch 2.15 (`scripts/runtime-smoke.py`): the real runtime process starts, the app reaches it through `/api/runtime`, it
  reports the real model load failure for an empty weights folder, the real worker runs a job to the end
  (`--cpu-worker`, built-in `sleep` job), and Stop leaves no process or open port. **Not verified**: loading
  the real Qwen weights, GPU inference, and training; this workstation has no weights and a CPU-only test environment.
  Run `scripts/runtime-smoke.py --python <env python> --models <weights folder>` once with your real environment.
* **No sync** between the local project and the Spark's store; remote answers come from the Spark's own network.
* Model weights, checkpoints and job state were deliberately not copied (see ARCHITECTURE.md); the project refers to its
  checkpoint by id and shows it as not present here.
* Not done: multi-window detachment of panels into OS windows (panels float inside the window, as in the original),
  file associations for `.mpnbak`/bundles, an ARM64 build, backend slimming (~100 MB), signed packages, public release.
* 8 original e2e tests do not pass on real data (above) and were not rewritten.
* The pre-existing `svelte-check` crashes (segfault) in this environment, so type checking relied on the Vite compile.
