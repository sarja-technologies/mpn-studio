# Projects (MPN Studio 0.4)

Everything a person makes or opens in MPN Studio is a **project**. The home screen lists them; nobody has to know what
is inside the folder. This page is for developers: what a project is on disk, and what each action does to it.

Projects live in `~/.local/share/mpn-studio/projects/<slug>/` (or `$MPN_STUDIO_DATA_DIR/projects`). Each folder holds
`project.json` and, depending on its kind, one data file.

## Three kinds

| Kind | Shown as | Folder holds | Edits |
|---|---|---|---|
| `network` | Knowledge network / Model with the X core | `project.json` (`mpn-studio-project/1`), `store.sqlite` | yes, in the store |
| `linked` | Protected original | `project.json` (`mpn-studio-project/2`) only | never |
| `fork` | Editable fork of X | `project.json` (`mpn-studio-project/2`), `overlay.sqlite` | yes, in the change file |

**network**: a network small enough to own: a blank project, Yaob N10K, an imported bundle or store file. Its whole
history is in `store.sqlite` (revisions, undo, redo). The manifest format is unchanged from 0.3, so 0.3 still opens
these projects, and 0.4 opens every 0.3 project as it is: there is no migration step.

**linked**: a network too large to copy, such as Yaob Origin N119M (119,176,842 nodes, 129 GiB). The project names
the network and where to read it, and nothing else:

```json
{
  "format": "mpn-studio-project/2", "kind": "linked", "name": "Yaob Origin N119M",
  "network": {
    "identity": {"name": "...", "pack_ids": ["fylume-mpn", "mpn14b-live"], "nodes": 119176842, "storage_bytes": 138107195392},
    "sources": [
      {"mode": "local",  "target": "/data/mpn/fylume-mpn", "label": null},
      {"mode": "remote", "target": "http://mpn-host.local:8844", "label": "Lab server"}
    ],
    "preferred": 0, "protected": true
  }
}
```

Sources are tried in order (the preferred one first). Before a source is used, its identity is checked against the
project's: a folder or address that holds a different network is refused, and a change in node count is shown as a
warning instead of being hidden (a live, still-growing copy can hold more nodes than the published pack). A local source is
opened read-only (`mode=ro` SQLite URIs). No code path in the workspace receives a handle that can write to the base.

**fork**: an editable version of a linked network (or of another fork) made by **Duplicate**. It references the same
base and sources and keeps only its own changes in `overlay.sqlite` (format `mpn-overlay/1`):

| Table | Holds |
|---|---|
| `revisions` | one row per edit, undo or redo |
| `node_ops` | `(rev, nid) -> put / del / base`; a `put` carries the whole edited node |
| `edge_ops` | `(rev, src, rel, dst) -> add / del / base` |
| `cur_nodes`, `cur_edges` | the state at the head revision, for reads (only nodes the fork touched) |
| `ofts` | full-text index over the fork's own nodes |
| `versions` | named revisions (Save version) |

Reads merge the change file over the base: a node the fork edited or deleted is answered from the change file, every
other node from the base. Search merges the base's results with the fork's own index. New nodes get ids `f0…` that
cannot collide with base ids. Undo and redo are revisions, so history is never rewritten.

A new fork of Origin took 0.03 s and 91 KB on disk; after one edit its folder was 121 KiB. The base's part files kept
their size and modification time.

## Exploring a large network on the canvas

The canvas never draws the whole network. It shows clusters and opens them one level at a time:

1. **Network**: one circle per domain, sized by its number of nodes.
2. **Domain**: the kinds of node in it (concepts, works, people, claims…).
3. **Kind**: the datasets those nodes come from. A level with a single cluster inside is passed straight through.
4. **Nodes**: 60 real nodes of the cluster with their titles and the relationships among them; Previous and Next page
   through the rest with the network's own cursor.

Click a cluster or zoom into it to open it; zoom out, press Backspace or Escape, or use the breadcrumb to go back.
Click a node to inspect it, double-click to open its neighbourhood. A search result opens its node's neighbourhood
directly, under the breadcrumb of its domain and kind. A claim's neighbourhood shows the node the claim is about.

Counts come from each partition's count table (`GET /api/ws/clusters`, about a millisecond on Origin), never from a
scan of the nodes. Links among the nodes on a page come from one bounded relationship page per node
(`POST /api/ws/links`, at most 120 nodes). A server that does not report counts inside a domain (the Spark's
`mpn-network/1` API) lists the domain's nodes directly. In a fork, its own new nodes form a cluster of their own.

Many Wikidata nodes in Origin carry claims but no stored relationships; the canvas shows them alone and says so.

## Actions

| Action | network | linked | fork |
|---|---|---|---|
| Duplicate | copies `store.sqlite` (with history) | new fork, nothing copied | new fork with a copy of the change file |
| Save | checkpoints the store | (read only) | checkpoints the change file |
| Save version | row in the store's `releases` table | (read only) | row in `versions` |
| Compare | any two of: start, versions, now | | any two of: the original network, versions, now |
| Open version as new project | store copy whose history ends at that version | | fork whose change file ends at that version |
| Export | `.mpn` model file, bundle folder, backup | | `.mpnfork` file, backup |
| Back up | `.mpnbak` with the store | `.mpnbak` with `project.json` | `.mpnbak` with the change file |
| Move to trash | moves the folder to `trash/` | removes the link only | moves the folder to `trash/` |

Nothing is deleted outright: Move to trash renames the folder into `<data>/trash/<slug>-<stamp>` and asks for the
project's name first. Trashing a linked network never touches the network itself.

## Files people pass around

- **`.mpn`** (unchanged): a finished model, network plus cognitive core. See [../MPN-FORMAT.md](../MPN-FORMAT.md).
- **`.mpnfork`** (`mpn-fork/1`): a fork's changes for someone who has the same network. A gzip tar of `manifest.json`
  (the network's identity and sources, the head revision, change counts, the change file's SHA-256) and
  `overlay.sqlite`. Opening one checks the checksum and creates a new fork project; it does not contain the network.
- **`.mpnbak`**: a backup of one project (`project.json`, its data file if it has one, and a checksum record). Restoring
  one always creates a new project.

## Where the code is

| | |
|---|---|
| `mpnstudio/desktop/projects.py` | kinds, create / duplicate / link / fork, backup, restore, trash |
| `mpnstudio/workspace/network.py` | `Workspace`: sources with fallback, identity check, merged reads and edits |
| `mpnstudio/workspace/overlay.py` | the change file |
| `mpnstudio/workspace/versions.py` | versions and compare for network projects |
| `mpnstudio/workspace/forkfile.py` | `.mpnfork` export, verify, import |
| `mpnstudio/workspace/routes.py` | `/api/projects/*`, `/api/ws/*`, `/api/versions/*` |
| `web/src/lib/home/` | home screen and its dialogs |
| `web/src/lib/work/` | the project top bar, Versions, Export, Model |
| `web/src/lib/net/` | the workspace for linked networks and forks |
