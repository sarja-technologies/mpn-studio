<script lang="ts">
  import { desk, errText, post, del, openInFileManager, type ProjectRow } from '../desktop.svelte';
  import { importBundle, importStore, openBackupFile } from '../desktop-actions';
  import { fmtInt, fmtBytes } from '../api';
  import { app } from '../state.svelte';

  let { overlay = false }: { overlay?: boolean } = $props();
  const autofocus = (el: HTMLElement) => el.focus();
  const rows = $derived(desk.info?.projects ?? []);
  const current = $derived(desk.info?.project?.id ?? null);
  let wantsNew = $state(false);
  const creating = $derived(wantsNew || rows.length === 0);
  let name = $state('');
  let error = $state<string | null>(null);
  let renaming = $state<string | null>(null);
  let renameTo = $state('');
  let menuFor = $state<string | null>(null);

  async function create() {
    error = null;
    try {
      await desk.createProject(name.trim());
    } catch (e) {
      error = errText(e);
    }
  }
  async function open(p: ProjectRow) {
    error = null;
    try {
      if (p.id === current) desk.home = false;
      else await desk.openProject(p.id);
    } catch (e) {
      error = errText(e);
    }
  }
  async function rename(p: ProjectRow) {
    try {
      await post(`api/desktop/projects/${p.id}/rename`, { name: renameTo });
      renaming = null;
      await desk.refresh();
    } catch (e) {
      error = errText(e);
    }
  }
  async function trash(p: ProjectRow) {
    menuFor = null;
    try {
      const r = await del<{ moved_to: string }>(`api/desktop/projects/${p.id}`);
      app.toast(`${p.name} was moved to ${r.moved_to}. Nothing was deleted.`);
      if (p.id === current) desk.reloadApp();
      else await desk.refresh();
    } catch (e) {
      error = errText(e);
    }
  }
  const origin = (p: ProjectRow) =>
    p.origin?.kind === 'spark-snapshot' ? 'Imported from a DGX Spark snapshot' : p.origin?.kind === 'bundle' ? 'Imported from a bundle' : p.origin?.kind === 'restore' ? 'Restored from a backup' : p.origin?.kind === 'store-file' ? 'Imported from a store file' : '';
  const when = (s?: string) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && overlay) desk.home = false;
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="home" class:overlay data-overlay>
  <div class="sheet" role="dialog" aria-label="Projects">
    <header>
      <img src="brand/fylume/fylume-symbol-white.svg" width="28" height="28" alt="Fylume" data-brand />
      <div>
        <h1>MPN Studio</h1>
        <p class="muted">Modular Persistent Network</p>
      </div>
      <span class="grow"></span>
      {#if overlay}<button class="btn" onclick={() => (desk.home = false)}>Back to {desk.info?.project?.name ?? 'the project'}</button>{/if}
    </header>

    {#if error}<p class="error-line">{error}</p>{/if}

    {#if rows.length}
      <table>
        <thead>
          <tr><th class="l">Project</th><th>Nodes</th><th>MPNs</th><th>Revision</th><th>Size</th><th class="l">Last changed</th><th></th></tr>
        </thead>
        <tbody>
          {#each rows as p (p.id)}
            <tr class:cur={p.id === current}>
              <td class="l">
                {#if renaming === p.id}
                  <form class="row" onsubmit={(e) => (e.preventDefault(), rename(p))}>
                    <input class="field" bind:value={renameTo} aria-label="Project name" />
                    <button class="btn" type="submit">Rename</button>
                    <button class="btn quiet" type="button" onclick={() => (renaming = null)}>Cancel</button>
                  </form>
                {:else}
                  <button class="name" onclick={() => open(p)}>{p.name}{#if p.id === current}<span class="muted"> (open)</span>{/if}</button>
                  {#if origin(p)}<div class="muted small">{origin(p)}</div>{/if}
                {/if}
              </td>
              <td class="num">{p.damaged ? '' : fmtInt(p.nodes ?? 0)}</td>
              <td class="num">{p.damaged ? '' : fmtInt(p.mpns ?? 0)}</td>
              <td class="num">{p.head ?? ''}</td>
              <td class="num">{fmtBytes(p.bytes ?? 0)}</td>
              <td class="l muted">{when(p.modified)}</td>
              <td class="act">
                <button class="btn primary" onclick={() => open(p)} disabled={p.damaged}>{p.id === current ? 'Continue' : 'Open'}</button>
                <div class="anchor">
                  <button class="btn icon" aria-label="More actions for {p.name}" aria-haspopup="menu" aria-expanded={menuFor === p.id} onclick={() => (menuFor = menuFor === p.id ? null : p.id)}>
                    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
                  </button>
                  {#if menuFor === p.id}
                    <div class="pop menu" role="menu">
                      <button role="menuitem" onclick={() => ((renaming = p.id), (renameTo = p.name), (menuFor = null))}>Rename</button>
                      <button role="menuitem" onclick={() => ((menuFor = null), openInFileManager(`${desk.info?.paths.projects}/${p.id}`))}>Show in file manager</button>
                      <hr />
                      <button role="menuitem" onclick={() => trash(p)}>Move to trash</button>
                    </div>
                  {/if}
                </div>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    {:else}
      <p class="lead">No projects yet.</p>
      <p class="muted">Create an empty network, or import one you already have.</p>
    {/if}

    <section class="new">
      {#if creating}
        <form class="row" onsubmit={(e) => (e.preventDefault(), create())}>
          <input class="field" placeholder="Project name" bind:value={name} aria-label="New project name" use:autofocus />
          <button class="btn primary" type="submit" disabled={!name.trim()}>Create project</button>
          {#if rows.length}<button class="btn quiet" type="button" onclick={() => (wantsNew = false)}>Cancel</button>{/if}
        </form>
      {:else}
        <div class="row">
          <button class="btn" onclick={() => (wantsNew = true)}>New project</button>
          <button class="btn" onclick={importBundle}>Import bundle folder</button>
          <button class="btn" onclick={importStore}>Import store file</button>
          <button class="btn" onclick={() => openBackupFile()}>Open backup file</button>
        </div>
      {/if}
      {#if creating && !rows.length}
        <div class="row alt">
          <button class="btn" onclick={importBundle}>Import bundle folder</button>
          <button class="btn" onclick={importStore}>Import store file</button>
          <button class="btn" onclick={() => openBackupFile()}>Open backup file</button>
        </div>
      {/if}
    </section>
    <p class="muted small where">Projects are stored in {desk.info?.paths.projects}. Everything works without a network connection.</p>
  </div>
</div>

<style>
  .home {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding: 8vh 24px 24px;
    background: var(--void);
    overflow: auto;
  }
  .home.overlay {
    background: rgba(5, 5, 5, 0.86);
  }
  .sheet {
    width: min(920px, 100%);
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 2px;
    padding: 20px 24px 22px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  h1 {
    font-size: 20px;
    line-height: 24px;
    font-weight: 600;
  }
  .lead {
    font-size: 15px;
    font-weight: 500;
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  th {
    color: var(--muted);
    font-weight: 400;
    text-align: right;
    padding: 0 10px 6px;
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
  }
  td {
    padding: 9px 10px;
    border-bottom: 1px solid var(--line-soft);
    text-align: right;
    vertical-align: middle;
  }
  th.l,
  td.l {
    text-align: left;
  }
  tr.cur td {
    background: #151515;
  }
  .name {
    border: 0;
    background: none;
    padding: 0;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    text-align: left;
  }
  .name:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .small {
    font-size: 12px;
  }
  .act {
    white-space: nowrap;
  }
  .act > :global(*) {
    display: inline-flex;
    vertical-align: middle;
    margin-left: 6px;
  }
  .anchor {
    position: relative;
  }
  .anchor .menu {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 5;
  }
  .new {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .new .field {
    max-width: 320px;
  }
  .where {
    margin-top: 2px;
  }
  @media (max-width: 760px) {
    th:nth-child(4), td:nth-child(4), th:nth-child(5), td:nth-child(5), th:nth-child(6), td:nth-child(6) {
      display: none;
    }
  }
</style>
