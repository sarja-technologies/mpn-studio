<script lang="ts">
  import { onMount } from 'svelte';
  import { desk, errText, get, openInFileManager, pickFolder, put } from '../desktop.svelte';
  import { fmtBytes, fmtInt } from '../api';
  import { app } from '../state.svelte';

  let d = $state.raw<any>(null);
  let error = $state<string | null>(null);
  async function load() {
    try {
      d = await get('api/desktop/datasets');
      error = null;
    } catch (e) {
      error = errText(e);
    }
  }
  $effect(() => {
    app.head;
    load();
  });
  async function choose() {
    const p = await pickFolder('Choose the folder that holds datasets');
    if (!p) return;
    try {
      await put('api/desktop/settings', { storage: { datasets_dir: p } });
      await load();
    } catch (e) {
      error = errText(e);
    }
  }
</script>

<div class="ds">
  {#if error}<p class="error-line">{error}</p>{/if}
  {#if !d && !error}<p class="muted">Reading the project</p>{/if}
  {#if d}
    <section>
      <h3>Built from</h3>
      {#if d.corpus}
        <dl>
          <dt>Nodes</dt><dd class="num">{fmtInt(d.corpus.nodes)}</dd>
          <dt>Relations</dt><dd class="num">{fmtInt(d.corpus.edges)}</dd>
          <dt>Sources</dt><dd class="num">{fmtInt(d.corpus.sources)}</dd>
        </dl>
      {:else}
        <p class="muted">This project did not come from the reference corpus.</p>
      {/if}
      {#if d.sources_by_license.length}
        <table>
          <thead><tr><th class="l">Licence</th><th class="l">Kind</th><th>Sources</th></tr></thead>
          <tbody>{#each d.sources_by_license as r (r.license + r.kind)}<tr><td class="l">{r.license}</td><td class="l">{r.kind}</td><td class="num">{fmtInt(r.count)}</td></tr>{/each}</tbody>
        </table>
      {/if}
    </section>

    <section>
      <h3>Dataset folder</h3>
      <div class="row">
        <span class="mono ellipsis grow" class:muted={!d.folder} title={d.folder ?? ''}>{d.folder ?? 'Not set'}</span>
        <button class="btn" onclick={choose}>Choose...</button>
        {#if d.folder}<button class="btn" onclick={() => openInFileManager(d.folder)}>Show</button>{/if}
      </div>
      {#if d.error}<p class="error-line">{d.error}</p>
      {:else if d.folder && d.entries.length === 0}<p class="muted">The folder is empty.</p>
      {:else if d.entries.length}
        <table>
          <tbody>{#each d.entries as e (e.name)}<tr><td class="l ellipsis">{e.name}{e.dir ? '/' : ''}</td><td class="num">{e.bytes != null ? fmtBytes(e.bytes) : ''}</td></tr>{/each}</tbody>
        </table>
      {:else}
        <p class="muted">Datasets stay where they are. MPN Studio lists them and never copies them.</p>
      {/if}
    </section>
  {/if}
</div>

<style>
  .ds {
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  h3 {
    font-weight: 500;
  }
  dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 2px 16px;
    margin: 0;
  }
  dd {
    margin: 0;
    text-align: right;
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  th {
    font-weight: 400;
    color: var(--muted);
    text-align: right;
    padding: 0 6px 4px;
    border-bottom: 1px solid var(--line);
  }
  td {
    padding: 3px 6px;
    border-bottom: 1px solid var(--line-soft);
    text-align: right;
    max-width: 260px;
  }
  th.l,
  td.l {
    text-align: left;
  }
</style>
