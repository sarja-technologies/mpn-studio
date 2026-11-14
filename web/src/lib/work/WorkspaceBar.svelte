<script lang="ts">
  // The open project's top bar: back to the home screen, the project, its everyday actions, and everything advanced
  // behind More.
  import { app, PANELS } from '../state.svelte';
  import { desk } from '../desktop.svelte';
  import { backupNow, quitApp } from '../desktop-actions';
  import { home } from '../home/home.svelte';
  import { net } from '../net/net.svelte';
  import { clickOutside } from '../ui/actions';
  import Icon from '../ui/Icon.svelte';
  import Search from '../ui/Search.svelte';
  import { work } from './work.svelte';

  let { search = $bindable(null) }: { search?: ReturnType<typeof Search> | null } = $props();
  let more = $state(false);
  const p = $derived(desk.project);
  const kind = $derived(work.kind);
  const editable = $derived(kind !== 'linked');

  async function duplicate() {
    await home.load();
    home.dialog = { kind: 'create', tab: 'duplicate', from: p?.id };
  }
  function pick(f: () => unknown) {
    more = false;
    f();
  }
  // Every edit is written to disk by the local service as it is made, so the project is saved unless that service stopped
  // answering (then the last edit may not have been stored).
  const saveState = $derived(
    kind === 'linked' ? null : kind === 'network' ? (app.offline ? 'Not saved: the local service is not answering' : 'All changes saved') : net.ovErr && !net.state ? 'Not connected' : 'All changes saved',
  );
  const kindNote = $derived(
    kind === 'linked' ? 'Protected original, read only' : kind === 'fork' ? `Fork of ${net.state?.identity?.name ?? 'a linked network'}` : '',
  );
</script>

<header class="top">
  <div class="brand" data-brand title="MPN Studio">
    <img src="brand/fylume/fylume-symbol-micro-white.svg" width="20" height="20" alt="Fylume" />
  </div>
  <button class="btn quiet" onclick={() => (desk.home = true)} title="All projects">Home</button>
  <span class="sep" aria-hidden="true"></span>
  <div class="proj">
    <span class="pname ellipsis" title={p?.name}>{p?.name}</span>
    {#if kindNote}<span class="pkind ellipsis" title={kindNote}>{kindNote}</span>{/if}
    {#if saveState}<span class="psave ellipsis" class:bad={saveState !== 'All changes saved'} title="Every edit is written to this computer the moment you make it">{saveState}</span>{/if}
  </div>
  {#if kind === 'network'}<Search bind:this={search} />{/if}
  <span class="grow"></span>

  {#if editable}
    <div class="group">
      <button class="btn icon quiet" aria-label="Undo" title={work.canUndo ? 'Undo (Ctrl+Z)' : 'Nothing to undo'} disabled={!work.canUndo} onclick={() => work.undo()}><Icon name="undo" /></button>
      <button class="btn icon quiet" aria-label="Redo" title={work.canRedo ? 'Redo (Ctrl+Shift+Z)' : 'Nothing to redo'} disabled={!work.canRedo} onclick={() => work.redo()}><Icon name="redo" /></button>
    </div>
    <button class="btn" onclick={() => work.save()} title="Save (Ctrl+S)">Save</button>
  {/if}
  <button class="btn" class:primary={!editable} onclick={duplicate} title={editable ? 'An independent copy of this project' : 'Make an editable fork. The original stays unchanged.'}>
    {editable ? 'Duplicate' : 'Duplicate to edit'}
  </button>
  {#if editable}
    <button class="btn" onclick={() => (work.dialog = 'versions')}>Versions</button>
    <button class="btn" onclick={() => (work.dialog = 'export')}>Export</button>
  {/if}
  {#if kind === 'network'}
    <button class="btn" onclick={() => (work.dialog = 'model')}>Model</button>
  {/if}

  <div class="anchor" use:clickOutside={() => (more ? ((more = false), true) : false)}>
    <button class="btn" aria-haspopup="menu" aria-expanded={more} onclick={() => (more = !more)}>More <Icon name="chevron" /></button>
    {#if more}
      <div class="pop menu moremenu" role="menu">
        {#if kind === 'network'}
          {#each PANELS as q (q.id)}
            <button role="menuitemcheckbox" aria-checked={app.isOpen(q.id)} onclick={() => pick(() => (app.isOpen(q.id) ? app.closePanel(q.id) : app.showPanel(q.id)))}>
              <span class="tick">{#if app.isOpen(q.id)}<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 6.5 5 9.5 10 3" /></svg>{/if}</span>
              {q.title}
            </button>
          {/each}
          <hr />
        {/if}
        {#if kind !== 'linked'}<button role="menuitem" onclick={() => pick(backupNow)}>Back up now</button>{/if}
        <button role="menuitem" onclick={() => pick(() => (desk.settings = kind === 'network' ? 'backups' : 'storage'))}>Backups and storage…</button>
        <button role="menuitem" onclick={() => pick(() => (work.library = true))}>Model library…</button>
        <button role="menuitem" onclick={() => pick(() => (desk.settings = 'compute'))}>Compute…</button>
        <button role="menuitem" onclick={() => pick(() => (desk.settings = 'storage'))}>Settings… <span class="why">Ctrl+,</span></button>
        <hr />
        <button role="menuitem" onclick={() => pick(() => (desk.help = 'names'))}>Names and terms <span class="why">F1</span></button>
        <button role="menuitem" onclick={() => pick(() => (desk.help = 'shortcuts'))}>Keyboard shortcuts</button>
        <button role="menuitem" onclick={() => pick(() => (desk.help = 'about'))}>About MPN Studio</button>
        <hr />
        <button role="menuitem" onclick={() => pick(() => desk.closeProject())}>Close project</button>
        <button role="menuitem" onclick={() => pick(quitApp)}>Quit <span class="why">Ctrl+Q</span></button>
      </div>
    {/if}
  </div>
</header>

<style>
  .top {
    flex: none;
    height: var(--bar);
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 8px 0 10px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
    position: relative;
    z-index: 25;
  }
  .brand {
    display: flex;
    flex: none;
    margin-right: 2px;
  }
  .brand img {
    display: block;
  }
  .sep {
    width: 1px;
    height: 18px;
    background: var(--line);
    margin: 0 4px;
  }
  .proj {
    display: flex;
    align-items: baseline;
    gap: 10px;
    min-width: 0;
    max-width: 40%;
    flex: 0 0 auto;
    margin-right: 10px;
  }
  .pname {
    font-weight: 500;
    font-size: 14px;
    min-width: 0;
    max-width: 280px;
  }
  .psave {
    color: var(--muted);
    min-width: 0;
  }
  .psave.bad {
    color: var(--white);
    text-decoration: underline;
    text-decoration-color: var(--ember);
    text-underline-offset: 3px;
  }
  .pkind {
    color: var(--muted);
    min-width: 0;
    max-width: 260px;
  }
  .group {
    display: flex;
    gap: 2px;
    margin-right: 4px;
  }
  .anchor {
    position: relative;
    flex: none;
  }
  .moremenu {
    position: absolute;
    right: 0;
    top: 33px;
    z-index: 60;
    min-width: 240px;
  }
  .tick {
    width: 12px;
    height: 12px;
    display: inline-flex;
  }
  .btn {
    flex: none;
  }
  @media (max-width: 1100px) {
    .pkind {
      display: none;
    }
  }
  @media (max-width: 1180px) {
    .psave:not(.bad) {
      display: none;
    }
  }
</style>
