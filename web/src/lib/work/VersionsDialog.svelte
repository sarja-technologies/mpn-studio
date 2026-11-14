<script lang="ts">
  // Versions of the open project: save the current state under a name, compare any two (the original, saved versions,
  // the current state), and open a version as a new project. A fork's versions hold only its own changes.
  import { onMount } from 'svelte';
  import { errText, get, post } from '../api';
  import { desk } from '../desktop.svelte';
  import { saveView } from '../desktop.svelte';
  import { autofocus } from '../ui/actions';
  import Modal from '../ui/Modal.svelte';
  import { net } from '../net/net.svelte';
  import { work } from './work.svelte';

  interface Version { name: string; rev: number; created: string | number; note?: string }
  const fork = work.kind === 'fork';
  const base = fork ? 'api/ws' : 'api';

  let versions = $state.raw<Version[]>([]);
  let error = $state<string | null>(null);
  let name = $state('');
  let note = $state('');
  let busy = $state<string | null>(null);
  let from = $state('original');
  let to = $state('current');
  let diff = $state.raw<any>(null);
  let diffErr = $state<string | null>(null);
  let open = $state<string | null>(null);

  async function load() {
    try {
      versions = (await get<{ versions: Version[] }>(`${base}/versions`)).versions;
      error = null;
    } catch (e) {
      error = errText(e);
    }
  }
  async function compare() {
    diffErr = null;
    diff = null;
    try {
      diff = await get(`${base}/${fork ? 'compare' : 'versions/compare'}?a=${encodeURIComponent(from)}&b=${encodeURIComponent(to)}`);
    } catch (e) {
      diffErr = errText(e);
    }
  }
  onMount(async () => {
    await load();
    compare();
  });

  async function save() {
    busy = 'Saving';
    error = null;
    try {
      await post(`${base}/versions`, { name: name.trim(), note: note.trim() });
      name = '';
      note = '';
      await load();
      if (fork) net.reload();
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
  async function openAsProject(v: Version) {
    busy = 'Creating the project';
    error = null;
    try {
      await post(`${base}/versions/open`, { version: v.name });
      saveView('editor');
      location.reload();
    } catch (e) {
      error = errText(e);
      busy = null;
    }
  }
  function when(t: string | number) {
    const d = typeof t === 'number' ? new Date(t * 1000) : new Date(t);
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }
  const label = (v: string) => (v === 'original' ? (fork ? 'The original network' : 'The start of the project') : v === 'current' ? 'Now' : v);
  function summary(d: any): string {
    const c: Record<string, number> = {};
    for (const n of d.nodes) c[n.change] = (c[n.change] ?? 0) + 1;
    const parts = Object.entries(c).map(([k, v]) => `${v.toLocaleString()} ${k}`);
    const r = d.relationships.length;
    if (r) parts.push(`${r.toLocaleString()} ${r === 1 ? 'relationship' : 'relationships'} changed`);
    return (d.truncated ? 'At least ' : '') + parts.join(', ');
  }
  const relName = (e: any) => e.label ?? e.rel ?? e.type ?? 'related to';
  const close = () => !busy && (work.dialog = null);
  const changeWord: Record<string, string> = { added: 'Added', deleted: 'Deleted', edited: 'Edited', restored: 'Restored', removed: 'Removed' };
  const fieldWord: Record<string, string> = { title: 'title', summary: 'summary', aliases: 'other names', domain: 'domain', kind: 'kind', notes: 'notes', statements: 'knowledge', cells: 'knowledge', type: 'kind' };
</script>

<Modal title="Versions of {desk.project?.name}" onclose={close} width={780}>
  <div class="saveform">
    <input class="field grow" placeholder="Name this version, for example Before the biology import" bind:value={name} use:autofocus maxlength="120"
      aria-label="Version name" onkeydown={(e) => e.key === 'Enter' && name.trim() && save()} />
    <input class="field note" placeholder="Note (optional)" bind:value={note} maxlength="400" aria-label="Note" />
    <button class="btn primary" disabled={!name.trim() || !!busy} onclick={save}>Save version</button>
  </div>
  {#if error}<p class="error-line">{error}</p>{/if}

  <div class="cols">
    <section class="list">
      <h3>Saved versions</h3>
      {#if !versions.length}
        <p class="muted">None yet. A version records the project exactly as it is now, so you can compare with it or go back to it.</p>
      {:else}
        <ul>
          {#each versions as v (v.name)}
            <li>
              <div class="vhead">
                <span class="ellipsis vname" title={v.name}>{v.name}</span>
                <span class="muted num">{when(v.created)}</span>
              </div>
              {#if v.note}<p class="muted vnote">{v.note}</p>{/if}
              <div class="vact">
                <button class="btn" onclick={() => ((from = v.name), (to = 'current'), compare())}>Compare with now</button>
                <button class="btn" disabled={!!busy} onclick={() => openAsProject(v)}>Open as new project</button>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section class="cmp">
      <h3>Compare</h3>
      <div class="pick">
        <select class="field" bind:value={from} onchange={compare} aria-label="From">
          <option value="original">{label('original')}</option>
          {#each versions as v (v.name)}<option value={v.name}>{v.name}</option>{/each}
        </select>
        <span class="muted">to</span>
        <select class="field" bind:value={to} onchange={compare} aria-label="To">
          <option value="current">{label('current')}</option>
          {#each versions as v (v.name)}<option value={v.name}>{v.name}</option>{/each}
        </select>
      </div>
      {#if diffErr}
        <p class="error-line">{diffErr}</p>
      {:else if !diff}
        <p class="muted">Comparing</p>
      {:else if !diff.nodes.length && !diff.relationships.length}
        <p class="muted">No differences.</p>
      {:else}
        <p>{summary(diff)}</p>
        <div class="diff scroll">
          {#each diff.nodes as n (n.id)}
            <div class="ch">
              <button class="chhead" aria-expanded={open === n.id} onclick={() => (open = open === n.id ? null : n.id)}>
                <span class="cw">{changeWord[n.change] ?? n.change}</span>
                <span class="ellipsis">{n.title ?? n.after?.title ?? n.before?.title ?? 'Untitled node'}</span>
                {#if n.fields?.length}<span class="muted ellipsis fields">{n.fields.map((f: string) => fieldWord[f] ?? f).join(', ')}</span>{/if}
              </button>
              {#if open === n.id && (n.before || n.after)}
                <div class="ba">
                  <div><p class="muted">Before</p>{#if n.before}<p>{n.before.summary || n.before.title}</p>{#if n.before.text}<p class="muted">{n.before.text}</p>{/if}{#each n.before.statements ?? [] as s}<p class="muted">{s}</p>{/each}{:else}<p class="muted">Not there</p>{/if}</div>
                  <div><p class="muted">After</p>{#if n.after}<p>{n.after.summary || n.after.title}</p>{#if n.after.text}<p class="muted">{n.after.text}</p>{/if}{#each n.after.statements ?? [] as s}<p class="muted">{s}</p>{/each}{:else}<p class="muted">Not there</p>{/if}</div>
                </div>
              {/if}
            </div>
          {/each}
          {#each diff.relationships as e, i (i)}
            <div class="ch rel">
              <span class="cw">{e.change === 'added' ? 'Connected' : e.change === 'removed' ? 'Disconnected' : e.change}</span>
              <span class="ellipsis">{e.src_title ?? e.src} <span class="muted">{relName(e)}</span> {e.dst_title ?? e.dst}</span>
            </div>
          {/each}
          {#if diff.truncated}<p class="muted more">Only the first 2,000 differences are listed.</p>{/if}
        </div>
      {/if}
    </section>
  </div>
  {#if busy}<p class="muted">{busy}…</p>{/if}
  <div class="row end"><button class="btn" onclick={close} disabled={!!busy}>Close</button></div>
</Modal>

<style>
  .saveform {
    display: flex;
    gap: 8px;
  }
  .saveform .note {
    width: 200px;
  }
  .cols {
    display: grid;
    grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
    gap: 20px;
    min-height: 320px;
  }
  section {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
  }
  h3 {
    font-weight: 500;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0;
    max-height: 420px;
    overflow: auto;
  }
  li {
    padding: 10px 0;
    border-top: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .vhead {
    display: flex;
    gap: 12px;
    align-items: baseline;
  }
  .vname {
    flex: 1;
    min-width: 0;
    font-weight: 500;
  }
  .vact {
    display: flex;
    gap: 8px;
  }
  .pick {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .pick select {
    flex: 1;
    min-width: 0;
  }
  .diff {
    max-height: 420px;
    border-top: 1px solid var(--line);
  }
  .ch {
    border-bottom: 1px solid var(--line);
  }
  .chhead,
  .rel {
    width: 100%;
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 7px 2px;
    background: none;
    border: 0;
    color: var(--white);
    text-align: left;
    font: inherit;
  }
  .chhead {
    cursor: pointer;
  }
  .chhead:hover {
    background: var(--raise);
  }
  .cw {
    flex: none;
    width: 86px;
    color: var(--muted);
  }
  .fields {
    margin-left: auto;
    max-width: 40%;
  }
  .ba {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    padding: 4px 2px 10px 98px;
  }
  .ba div {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .more {
    padding: 8px 2px;
  }
  .end {
    justify-content: flex-end;
  }
  @media (max-width: 760px) {
    .cols {
      grid-template-columns: 1fr;
    }
    .saveform {
      flex-wrap: wrap;
    }
  }
</style>
