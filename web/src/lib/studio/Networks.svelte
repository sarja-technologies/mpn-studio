<script lang="ts">
  import { work } from '../work/work.svelte';
  import { app } from '../state.svelte';
  import { desk, openInFileManager } from '../desktop.svelte';
  import { openBackupFile } from '../desktop-actions';
  import { del, errText, fmtBytes, fmtInt, get, patch, post } from '../api';
  import Modal from '../ui/Modal.svelte';
  import { studio, type NetworkRow, type TaskView } from './studio.svelte';

  const rows = $derived(studio.data?.networks ?? []);
  const openId = $derived(studio.data?.open_network ?? null);
  let menuFor = $state<string | null>(null);
  let importMenu = $state(false);
  let error = $state<string | null>(null);
  let busy = $state<string | null>(null);

  type Dlg =
    | { kind: 'new' }
    | { kind: 'rename'; n: NetworkRow }
    | { kind: 'duplicate'; n: NetworkRow }
    | { kind: 'part'; n: NetworkRow }
    | { kind: 'merge'; n: NetworkRow }
    | { kind: 'compare'; n: NetworkRow }
    | { kind: 'stats'; n: NetworkRow }
    | null;
  let dlg = $state.raw<Dlg>(null);
  let name = $state('');
  let other = $state('');
  let conflict = $state<'skip' | 'both'>('skip');
  let partQ = $state('');
  let partType = $state('');
  let partMpns = $state<string[]>([]);
  let stats = $state.raw<any>(null);
  let result = $state.raw<any>(null);
  let dErr = $state<string | null>(null);
  let dBusy = $state<string | null>(null);

  function show(d: Dlg) {
    menuFor = null;
    dlg = d;
    dErr = null;
    result = null;
    stats = null;
    dBusy = null;
    other = rows.find((r) => d && 'n' in d && r.id !== d.n.id && !r.damaged)?.id ?? '';
    if (d && 'n' in d) {
      name = d.kind === 'rename' ? d.n.name : d.kind === 'duplicate' ? `${d.n.name} copy` : d.kind === 'part' ? `${d.n.name} part` : '';
      if (d.kind === 'part' || d.kind === 'stats') loadStats(d.n.id);
    } else name = '';
    partQ = partType = '';
    partMpns = [];
  }
  async function loadStats(id: string) {
    try {
      stats = await get(`api/library/networks/${id}/stats`);
    } catch (e) {
      dErr = errText(e);
    }
  }
  async function task(start: Promise<TaskView>, label: string) {
    dBusy = label;
    dErr = null;
    const t = await studio.follow(start).catch((e) => ({ state: 'failed', error: errText(e) }) as TaskView);
    dBusy = null;
    await studio.load();
    if (t.state !== 'done') {
      dErr = t.error ?? 'It did not finish.';
      return null;
    }
    return t.result;
  }

  async function open(n: NetworkRow) {
    error = null;
    try {
      if (n.id === openId && desk.project) {
        work.library = false;
        desk.home = false;
      }
      else await desk.openProject(n.id);
    } catch (e) {
      error = errText(e);
    }
  }
  async function create() {
    try {
      await post('api/library/networks', { name: name.trim() });
      dlg = null;
      await studio.load();
    } catch (e) {
      dErr = errText(e);
    }
  }
  async function rename(n: NetworkRow) {
    try {
      await patch(`api/library/networks/${n.id}`, { name: name.trim() });
      dlg = null;
      await Promise.all([studio.load(), desk.refresh()]);
    } catch (e) {
      dErr = errText(e);
    }
  }
  async function duplicate(n: NetworkRow) {
    const r = await task(post(`api/library/networks/${n.id}/duplicate`, { name: name.trim() }), 'Copying');
    if (r) {
      dlg = null;
      app.toast(`${r.name} is an independent copy of ${n.name}.`);
    }
  }
  const partFilter = () => ({ ...(partQ.trim() ? { q: partQ.trim() } : {}), ...(partType ? { type: partType } : {}), ...(partMpns.length ? { mpn: partMpns } : {}) });
  async function part(n: NetworkRow) {
    const r = await task(post(`api/library/networks/${n.id}/specialise`, { name: name.trim(), filter: partFilter() }), 'Creating');
    if (r) {
      dlg = null;
      app.toast(`${r.name} holds ${fmtInt(r.nodes)} nodes from ${n.name}.`);
    }
  }
  async function merge(n: NetworkRow) {
    result = await task(post(`api/library/networks/${n.id}/merge`, { source: other, on_conflict: conflict }), 'Merging');
    if (result && n.id === openId) app.afterEdit({ rev: result.rev });
  }
  async function compare(n: NetworkRow) {
    dBusy = 'Comparing';
    try {
      result = await get(`api/library/networks/${n.id}/compare/${other}`);
    } catch (e) {
      dErr = errText(e);
    }
    dBusy = null;
  }
  async function trash(n: NetworkRow) {
    menuFor = null;
    try {
      const r = await del<{ moved_to: string }>(`api/library/networks/${n.id}`);
      app.toast(`${n.name} was moved to ${r.moved_to}. Nothing was deleted.`);
      await studio.load();
    } catch (e) {
      error = errText(e);
    }
  }
  async function closeOpen() {
    await desk.closeProject();
  }
  async function imp(kind: 'auto' | 'store') {
    importMenu = false;
    error = null;
    busy = 'Importing';
    try {
      const n = await studio.importNetwork(kind);
      if (n) app.toast(`Imported ${n.name}: ${fmtInt(n.nodes ?? 0)} nodes.`);
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
  const origin = (n: NetworkRow) =>
    ({ 'spark-snapshot': 'Imported from a DGX Spark snapshot', bundle: 'Imported from a bundle', restore: 'Restored from a backup', 'store-file': 'Imported from a store file', 'yaob-release': 'Imported from a Yaob release', duplicate: `Copy of ${n.origin?.of_name ?? ''}`, specialised: `Part of ${n.origin?.of_name ?? ''}` })[n.origin?.kind as string] ?? '';
  const when = (s?: string) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
  const others = (n: NetworkRow) => rows.filter((r) => r.id !== n.id && !r.damaged);
</script>

<div class="wrap">
  <div class="row head">
    <h1>Node networks</h1>
    <span class="grow"></span>
    <div class="anchor">
      <button class="btn" aria-haspopup="menu" aria-expanded={importMenu} disabled={!!busy} onclick={() => (importMenu = !importMenu)}>Import</button>
      {#if importMenu}
        <div class="pop menu" role="menu">
          <button role="menuitem" onclick={() => imp('auto')}>Network folder (bundle or Yaob release)</button>
          <button role="menuitem" onclick={() => imp('store')}>Store file</button>
          <button role="menuitem" onclick={() => ((importMenu = false), openBackupFile())}>Backup file</button>
        </div>
      {/if}
    </div>
    <button class="btn primary" onclick={() => show({ kind: 'new' })}>New network</button>
  </div>
  {#if busy}<p class="muted">{busy}</p>{/if}
  {#if error}<p class="error-line">{error}</p>{/if}

  {#if rows.length}
    <table class="list">
      <thead><tr><th>Network</th><th class="r">Nodes</th><th class="r hide-narrow">MPNs</th><th class="r hide-narrow">Size</th><th class="hide-narrow">Last changed</th><th></th></tr></thead>
      <tbody>
        {#each rows as n (n.id)}
          <tr class:cur={n.id === openId}>
            <td>
              <button class="rowname" onclick={() => open(n)} disabled={n.damaged}>{n.name}</button>{#if n.id === openId}<span class="muted"> (open)</span>{/if}
              <div class="muted small">{origin(n)}{n.router ? `${origin(n) ? ', ' : ''}learned router` : ''}</div>
            </td>
            <td class="r num">{n.damaged ? 'damaged' : fmtInt(n.nodes ?? 0)}</td>
            <td class="r num hide-narrow">{n.damaged ? '' : fmtInt(n.mpns ?? 0)}</td>
            <td class="r num hide-narrow">{fmtBytes(n.bytes ?? 0)}</td>
            <td class="muted hide-narrow">{when(n.modified)}</td>
            <td class="act">
              <button class="btn primary" disabled={n.damaged} onclick={() => open(n)}>{n.id === openId ? 'Continue editing' : 'Edit'}</button>
              <div class="anchor">
                <button class="btn icon" aria-label="More actions for {n.name}" aria-haspopup="menu" aria-expanded={menuFor === n.id} onclick={() => (menuFor = menuFor === n.id ? null : n.id)}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
                </button>
                {#if menuFor === n.id}
                  <div class="pop menu" role="menu">
                    <button role="menuitem" onclick={() => show({ kind: 'stats', n })}>Statistics</button>
                    <button role="menuitem" onclick={() => show({ kind: 'duplicate', n })}>Duplicate</button>
                    <button role="menuitem" onclick={() => show({ kind: 'part', n })}>New network from part of it</button>
                    <button role="menuitem" disabled={!others(n).length} onclick={() => show({ kind: 'merge', n })}>Merge another network into it</button>
                    <button role="menuitem" disabled={!others(n).length} onclick={() => show({ kind: 'compare', n })}>Compare with another</button>
                    <hr />
                    <button role="menuitem" onclick={() => show({ kind: 'rename', n })}>Rename</button>
                    <button role="menuitem" onclick={() => ((menuFor = null), openInFileManager(`${desk.info?.paths.projects}/${n.id}`))}>Show in file manager</button>
                    {#if n.id === openId}<button role="menuitem" onclick={closeOpen}>Close</button>{/if}
                    <hr />
                    <button role="menuitem" disabled={n.id === openId} onclick={() => trash(n)}>Move to trash</button>
                  </div>
                {/if}
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {:else}
    <div class="empty"><p>No networks yet. Create an empty one, or import one you already have.</p></div>
  {/if}
  <p class="muted small where">Networks are stored in {desk.info?.paths.projects}.</p>
</div>

{#if dlg?.kind === 'new'}
  <Modal title="New network" onclose={() => (dlg = null)}>
    <form onsubmit={(e) => (e.preventDefault(), create())}>
      <input class="field" placeholder="Network name" aria-label="Network name" bind:value={name} />
      {#if dErr}<p class="error-line">{dErr}</p>{/if}
      <div class="actions"><button type="button" class="btn" onclick={() => (dlg = null)}>Cancel</button><button class="btn primary" disabled={!name.trim()}>Create</button></div>
    </form>
  </Modal>
{:else if dlg?.kind === 'rename'}
  {@const n = dlg.n}
  <Modal title="Rename {n.name}" onclose={() => (dlg = null)}>
    <form onsubmit={(e) => (e.preventDefault(), rename(n))}>
      <input class="field" aria-label="Network name" bind:value={name} />
      {#if dErr}<p class="error-line">{dErr}</p>{/if}
      <div class="actions"><button type="button" class="btn" onclick={() => (dlg = null)}>Cancel</button><button class="btn primary" disabled={!name.trim()}>Rename</button></div>
    </form>
  </Modal>
{:else if dlg?.kind === 'duplicate'}
  {@const n = dlg.n}
  <Modal title="Duplicate {n.name}" onclose={() => (dlg = null)}>
    <form onsubmit={(e) => (e.preventDefault(), duplicate(n))}>
      <p class="muted">An independent copy with its full history{n.router ? ' and its learned router' : ''}. Editing the copy never changes {n.name}.</p>
      <input class="field" aria-label="Name of the copy" bind:value={name} />
      {#if dBusy}<p class="muted">{dBusy} {fmtInt(n.nodes ?? 0)} nodes</p>{/if}
      {#if dErr}<p class="error-line">{dErr}</p>{/if}
      <div class="actions"><button type="button" class="btn" onclick={() => (dlg = null)}>Cancel</button><button class="btn primary" disabled={!name.trim() || !!dBusy}>Duplicate</button></div>
    </form>
  </Modal>
{:else if dlg?.kind === 'part'}
  {@const n = dlg.n}
  <Modal title="New network from part of {n.name}" onclose={() => (dlg = null)} width={520}>
    <form onsubmit={(e) => (e.preventDefault(), part(n))}>
      <div>
        <label class="lbl" for="pt-name">Name</label>
        <input id="pt-name" class="field" bind:value={name} />
      </div>
      <div>
        <span class="lbl">MPNs to take (none chosen: all)</span>
        <div class="mpns scroll">
          {#each stats?.domains ?? [] as d (d.id)}
            <label class="ck"><input type="checkbox" value={d.id} bind:group={partMpns} /><span class="grow ellipsis">{d.name}</span><span class="muted num">{fmtInt(d.nodes)}</span></label>
          {:else}
            <p class="muted">Reading the MPNs</p>
          {/each}
        </div>
      </div>
      <div class="row">
        <div class="grow">
          <label class="lbl" for="pt-q">Only nodes containing</label>
          <input id="pt-q" class="field" bind:value={partQ} placeholder="Any text" />
        </div>
        <div>
          <label class="lbl" for="pt-type">Type</label>
          <select id="pt-type" class="field" bind:value={partType}><option value="">Any</option><option value="knowledge">Knowledge</option><option value="procedure">Procedure</option></select>
        </div>
      </div>
      <p class="muted">Relations among the chosen nodes come along{n.router ? ', and so does the learned router' : ''}. {n.name} is not changed.</p>
      {#if dBusy}<p class="muted">{dBusy}</p>{/if}
      {#if dErr}<p class="error-line">{dErr}</p>{/if}
      <div class="actions"><button type="button" class="btn" onclick={() => (dlg = null)}>Cancel</button><button class="btn primary" disabled={!name.trim() || !!dBusy}>Create network</button></div>
    </form>
  </Modal>
{:else if dlg?.kind === 'merge'}
  {@const n = dlg.n}
  <Modal title="Merge into {n.name}" onclose={() => (dlg = null)} width={480}>
    <form onsubmit={(e) => (e.preventDefault(), merge(n))}>
      {#if result}
        <p>Copied {fmtInt(result.copied)} nodes and {fmtInt(result.edges)} relations in revision {result.rev}.{result.skipped ? ` ${fmtInt(result.skipped)} nodes were already there and left out.` : ''}</p>
        {#if result.mpns_created.length}<p class="muted">New MPNs: {result.mpns_created.join(', ')}</p>{/if}
        <p class="muted">Open {n.name} and use Undo to take the merge back.</p>
        <div class="actions"><button type="button" class="btn primary" onclick={() => (dlg = null)}>Done</button></div>
      {:else}
        <div>
          <label class="lbl" for="mg-src">Copy the nodes of</label>
          <select id="mg-src" class="field" bind:value={other}>{#each others(n) as o (o.id)}<option value={o.id}>{o.name} ({fmtInt(o.nodes ?? 0)} nodes)</option>{/each}</select>
        </div>
        <div>
          <span class="lbl">When a node with the same title and type is already in {n.name}</span>
          <label class="ck"><input type="radio" value="skip" bind:group={conflict} /> Leave it out</label>
          <label class="ck"><input type="radio" value="both" bind:group={conflict} /> Copy it anyway, keeping both</label>
        </div>
        <p class="muted">The merge is one revision of {n.name}. The other network is not changed.</p>
        {#if dBusy}<p class="muted">{dBusy}</p>{/if}
        {#if dErr}<p class="error-line">{dErr}</p>{/if}
        <div class="actions"><button type="button" class="btn" onclick={() => (dlg = null)}>Cancel</button><button class="btn primary" disabled={!other || !!dBusy}>Merge</button></div>
      {/if}
    </form>
  </Modal>
{:else if dlg?.kind === 'compare'}
  {@const n = dlg.n}
  <Modal title="Compare {n.name}" onclose={() => (dlg = null)} width={560}>
    <form onsubmit={(e) => (e.preventDefault(), compare(n))}>
      <div class="row">
        <select class="field grow" aria-label="Network to compare with" bind:value={other}>{#each others(n) as o (o.id)}<option value={o.id}>{o.name}</option>{/each}</select>
        <button class="btn primary" disabled={!other || !!dBusy}>Compare</button>
      </div>
      {#if dErr}<p class="error-line">{dErr}</p>{/if}
      {#if result}
        {@const o = studio.network(other)}
        <p>{fmtInt(result.unchanged)} nodes are the same in both. Compared with {n.name}, {o?.name} has {fmtInt(result.added)} added, {fmtInt(result.removed)} missing and {fmtInt(result.changed)} changed nodes.</p>
        {#each [['Added', result.added_sample], ['Missing', result.removed_sample], ['Changed', result.changed_sample]] as [label, list]}
          {#if list.length}<p><span class="muted">{label}:</span> {list.slice(0, 12).map((x: any) => x.title).join(', ')}{list.length > 12 ? ', …' : ''}</p>{/if}
        {/each}
      {/if}
    </form>
  </Modal>
{:else if dlg?.kind === 'stats'}
  {@const n = dlg.n}
  <Modal title={n.name} onclose={() => (dlg = null)} width={520}>
    {#if dErr}<p class="error-line">{dErr}</p>{:else if !stats}<p class="muted">Reading the network</p>{:else}
      <table class="facts">
        <tbody>
          <tr><td class="muted">Live nodes</td><td class="num">{fmtInt(stats.nodes)}{#each Object.entries(stats.by_type) as [k, v]}<span class="muted">, {fmtInt(v as number)} {k}</span>{/each}</td></tr>
          <tr><td class="muted">Disabled, deleted</td><td class="num">{fmtInt(stats.nodes_disabled)}, {fmtInt(stats.nodes_deleted)}</td></tr>
          <tr><td class="muted">Relations</td><td class="num">{fmtInt(stats.relationships)}{stats.invalid_relationships ? `, ${fmtInt(stats.invalid_relationships)} pointing at deleted nodes` : ''}</td></tr>
          <tr><td class="muted">Nodes without relations</td><td class="num">{fmtInt(stats.orphan_nodes)}</td></tr>
          <tr><td class="muted">Nodes in no MPN</td><td class="num">{fmtInt(stats.unassigned_nodes)}</td></tr>
          <tr><td class="muted">MPNs</td><td class="num">{fmtInt(stats.domains.length)}</td></tr>
          <tr><td class="muted">Revisions</td><td class="num">{fmtInt(stats.revisions)}</td></tr>
          <tr><td class="muted">Last changed</td><td>{when(stats.last_modified)}</td></tr>
          <tr><td class="muted">On disk</td><td class="num">{fmtBytes(stats.folder_bytes)}, of which the store {fmtBytes(stats.file_bytes)}</td></tr>
          <tr><td class="muted">Routing</td><td>{stats.router ? `Learned router for ${stats.router.encoder?.name ?? 'its encoder'}` : 'Retrieval (no learned router)'}</td></tr>
        </tbody>
      </table>
    {/if}
    <div class="actions"><button class="btn" onclick={() => (dlg = null)}>Close</button></div>
  </Modal>
{/if}

<style>
  .head {
    gap: 8px;
  }
  tr.cur td {
    background: #151515;
  }
  .where {
    overflow-wrap: anywhere;
  }
  .empty {
    padding: 48px 0;
    text-align: center;
  }
  form {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .mpns {
    max-height: 220px;
    border: 1px solid var(--line);
    border-radius: 2px;
    padding: 4px 8px;
  }
  .ck {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 26px;
    cursor: pointer;
  }
  .facts {
    border-collapse: collapse;
  }
  .facts td {
    padding: 4px 16px 4px 0;
    vertical-align: top;
  }
</style>
