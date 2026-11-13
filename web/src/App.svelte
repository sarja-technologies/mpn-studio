<script lang="ts">
  import { onMount } from 'svelte';
  import { app, PANELS, type PanelId } from './lib/state.svelte';
  import { fmtInt } from './lib/api';
  import Board from './lib/board/Board.svelte';
  import Dock from './lib/panels/Dock.svelte';
  import FloatWindow from './lib/panels/FloatWindow.svelte';
  import Search from './lib/ui/Search.svelte';
  import ContextMenu from './lib/ui/ContextMenu.svelte';
  import Dialogs from './lib/ui/Dialogs.svelte';
  import Toasts from './lib/ui/Toasts.svelte';
  import Icon from './lib/ui/Icon.svelte';
  import { clickOutside } from './lib/ui/actions';
  import MenuBar from './lib/ui/MenuBar.svelte';
  import Studio from './lib/studio/Studio.svelte';
  import { studio } from './lib/studio/studio.svelte';
  import SettingsDialog from './lib/ui/SettingsDialog.svelte';
  import HelpDialog from './lib/ui/HelpDialog.svelte';
  import { bootBackend, desk, errText, isTauri, native } from './lib/desktop.svelte';
  import { exportProject, newProject, openFilesFromSystem, quitApp, saveProject } from './lib/desktop-actions';

  let search = $state<ReturnType<typeof Search> | null>(null);
  let panelsOpen = $state(false);
  const floats = $derived(PANELS.filter((p) => app.panels.items[p.id].where === 'float').map((p) => p.id));
  const st = $derived(app.status);
  const where = (id: PanelId) => {
    const w = app.panels.items[id].where;
    return w === 'float' ? 'window' : w === 'closed' ? '' : w + ' dock';
  };

  function editable(t: EventTarget | null) {
    const el = t as HTMLElement | null;
    return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
  }
  function onKey(e: KeyboardEvent) {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      search?.focus();
      return;
    }
    if (mod && !e.altKey) {
      const k = e.key.toLowerCase();
      if (k === 'n' && !e.shiftKey) return (e.preventDefault(), newProject());
      if (k === 'o') return (e.preventDefault(), (studio.tab = 'networks'), (desk.home = true));
      if (k === 's' && !e.shiftKey) return (e.preventDefault(), void saveProject());
      if (k === 'e' && e.shiftKey) return (e.preventDefault(), void exportProject());
      if (k === ',') return (e.preventDefault(), (desk.settings = 'storage'));
      if (k === 'q') return (e.preventDefault(), void quitApp());
    }
    if (e.key === 'F1') return (e.preventDefault(), (desk.help = 'names'));
    if (app.dialog) return;
    if (e.key === '/' && !mod && !editable(e.target)) {
      e.preventDefault();
      search?.focus();
      return;
    }
    if (mod && e.key.toLowerCase() === 'z' && !editable(e.target)) {
      e.preventDefault();
      if (e.shiftKey) app.redo();
      else app.undo();
    } else if (mod && e.key.toLowerCase() === 'y' && !editable(e.target)) {
      e.preventDefault();
      app.redo();
    }
  }
  // A layout saved on a wider screen must not squeeze the board away: the board keeps at least half the window (never under 420px).
  function fitDocks() {
    const room = Math.max(440, window.innerWidth - Math.max(420, Math.round(window.innerWidth * 0.5)));
    const l = app.panels.left.open ? app.panels.left.width : 0;
    const r = app.panels.right.open ? app.panels.right.width : 0;
    if (l + r <= room) return;
    const k = room / (l + r);
    if (l) app.panels.left.width = Math.max(220, Math.floor(l * k));
    if (r) app.panels.right.width = Math.max(220, Math.floor(r * k));
  }
  function onResize() {
    fitDocks();
    const narrow = window.innerWidth < 900;
    if (narrow && !app.narrow) {
      app.panels.left.open = false;
      app.panels.right.open = false;
    }
    app.narrow = narrow;
    for (const id of floats) app.clampFloat(id);
  }

  // Links to the outside world (provenance sources) open in the system browser, only when clicked. The window itself never navigates away.
  function onLink(e: MouseEvent) {
    const a = (e.target as HTMLElement | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
    if (!a || !/^https?:/i.test(a.getAttribute('href') ?? '')) return;
    e.preventDefault();
    if (isTauri()) native('open_external', { url: a.href }).catch((err) => app.toast(errText(err), 'error'));
  }

  onMount(() => {
    app.narrow = window.innerWidth < 900;
    document.addEventListener('click', onLink, true);
    window.addEventListener('mpn-open-files', openFilesFromSystem);
    (async () => {
      try {
        await bootBackend();
        await desk.refresh();
      } catch (e) {
        desk.bootError = errText(e);
        return;
      }
      desk.ready = true;
      openFilesFromSystem();
      if (!desk.project) return;
      await Promise.all([app.loadBoard(), app.loadStatus(), app.loadMpns(), app.loadModelName()]);
      if (app.narrow) {
        app.panels.left.open = false;
        app.panels.right.open = false;
      }
      fitDocks();
      for (const p of PANELS) if (app.panels.items[p.id].where === 'float') app.clampFloat(p.id);
    })();
    const poll = setInterval(() => !document.hidden && desk.project && app.loadStatus(), 5000);
    const flush = () => app.flushView(true);
    window.addEventListener('pagehide', flush);
    return () => {
      document.removeEventListener('click', onLink, true);
      window.removeEventListener('mpn-open-files', openFilesFromSystem);
      clearInterval(poll);
      window.removeEventListener('pagehide', flush);
    };
  });
  // when the model revision moves (an edit here or elsewhere), re-read what depends on it
  let seenHead = 0;
  $effect(() => {
    const h = app.head;
    if (seenHead && h !== seenHead) {
      app.loadBoard();
      app.loadMpns();
    }
    seenHead = h;
  });
</script>

<svelte:window onkeydown={onKey} onresize={onResize} />

{#if desk.bootError}
  <div class="boot">
    <p class="lead">MPN Studio's local service did not start.</p>
    <p class="error-line">{desk.bootError}</p>
    <button class="btn" onclick={() => location.reload()}>Try again</button>
  </div>
{:else if !desk.ready}
  <div class="boot"><p class="muted">Starting MPN Studio</p></div>
{:else if desk.needsProject}
  <Studio />
  <Toasts />
{:else}
<div class="app">
  <header class="top">
    <div class="brand" data-brand title="MPN Studio, Modular Persistent Network">
      <img src="brand/fylume/fylume-symbol-micro-white.svg" width="20" height="20" alt="Fylume" />
      <span class="name">MPN Studio</span>
    </div>
    <MenuBar onsearch={() => search?.focus()} />
    <button class="studio-btn btn" title="Models, cores and networks" onclick={() => (desk.home = true)}>Studio</button>
    <button class="proj" title="Switch network (Ctrl+O)" onclick={() => ((studio.tab = 'networks'), (desk.home = true))}>{desk.project?.name}</button>
    <Search bind:this={search} />
    <div class="spacer"></div>
    <div class="rev">
      <button class="btn icon quiet" aria-label="Undo the last model edit" title={st?.undo ? `Undo revision ${st.undo} (Ctrl+Z)` : 'Nothing to undo'} disabled={!st?.undo} onclick={() => app.undo()}><Icon name="undo" /></button>
      <button class="btn icon quiet" aria-label="Redo the undone edit" title={st?.redo ? 'Redo (Ctrl+Shift+Z)' : 'Nothing to redo'} disabled={!st?.redo} onclick={() => app.redo()}><Icon name="redo" /></button>
      <span class="head mono" title="Head revision of the model">rev {app.head}</span>
    </div>
    <div class="anchor" use:clickOutside={() => (panelsOpen ? ((panelsOpen = false), true) : false)}>
      <button class="btn" aria-haspopup="menu" aria-expanded={panelsOpen} onclick={() => (panelsOpen = !panelsOpen)}><Icon name="panels" /><span class="lbl-wide">Panels</span></button>
      {#if panelsOpen}
        <div class="pop menu panelmenu" role="menu">
          {#each PANELS as p (p.id)}
            <button role="menuitemcheckbox" aria-checked={app.isOpen(p.id)} onclick={() => ((panelsOpen = false), app.isOpen(p.id) ? app.closePanel(p.id) : app.showPanel(p.id))}>
              <span class="tick">{#if app.isOpen(p.id)}<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 6.5 5 9.5 10 3" /></svg>{/if}</span>
              {p.title}
              <span class="why">{where(p.id)}</span>
            </button>
          {/each}
        </div>
      {/if}
    </div>
  </header>

  <div class="main">
    <Dock side="left" />
    <Board />
    <Dock side="right" />
  </div>

  <footer class="status">
    <span class="num" title={desk.info?.settings?.compute?.mode === 'remote' ? 'Remote compute is switched on. Editing still works locally.' : 'Everything runs on this computer.'}>{desk.info?.settings?.compute?.mode === 'remote' ? 'Remote compute' : 'Local'}</span>
    {#if app.offline}
      <span class="warn">{app.offline}</span>
    {:else if st}
      <span class="num">{fmtInt(st.counts.canonical_live_nodes)} live nodes</span>
      {#if st.dirty.to_index > 0}<span class="num attn" title="Nodes whose content is not in the meaning index yet">{fmtInt(st.dirty.to_index)} to index</span>{/if}
      {#if st.dirty.trained_stale > 0}<span class="num attn" title="Nodes edited after training; their trained state is ignored until retrained">{fmtInt(st.dirty.trained_stale)} stale</span>{/if}
      {#if !st.runtime.available}<span title={st.runtime.reason}>Runtime not running</span>{/if}
      {#if st.jobs_active > 0}<button class="link" onclick={() => app.showPanel('jobs')}>{st.jobs_active} {st.jobs_active === 1 ? 'job' : 'jobs'} active</button>{/if}
    {/if}
    <span class="hint ellipsis">{app.hint ?? ''}</span>
  </footer>

  {#each floats as id (id)}
    <FloatWindow {id} />
  {/each}
  <ContextMenu />
  <Dialogs />
  <Toasts />
</div>
{/if}
{#if desk.ready && desk.project && desk.home}<Studio />{/if}
<SettingsDialog />
<HelpDialog />
{#if desk.busy}<div class="busy" role="status">{desk.busy}</div>{/if}

<style>
  .boot {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 24px;
    text-align: center;
  }
  .boot .lead {
    font-size: 15px;
    font-weight: 500;
  }
  .proj {
    max-width: 220px;
    height: var(--ctl);
    padding: 0 10px;
    margin-right: 8px;
    border: 1px solid var(--line);
    border-radius: 2px;
    background: transparent;
    color: var(--white);
    font-weight: 500;
    cursor: pointer;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .proj:hover {
    background: var(--raise);
    border-color: #4a4a4a;
  }
  .busy {
    position: fixed;
    left: 50%;
    bottom: 36px;
    transform: translateX(-50%);
    z-index: 100;
    padding: 6px 14px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 2px;
  }
  .app {
    display: flex;
    flex-direction: column;
    height: 100%;
    width: 100%;
    overflow: hidden;
  }
  .top {
    flex: none;
    height: var(--bar);
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 8px 0 10px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
    position: relative;
    z-index: 25;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
    flex: none;
    margin-right: 4px;
  }
  .brand img {
    display: block;
  }
  .name {
    font-weight: 500;
    font-size: 14px;
    letter-spacing: -0.01em;
    white-space: nowrap;
  }
  .spacer {
    flex: 1 1 0;
    min-width: 0;
  }
  .rev {
    display: flex;
    align-items: center;
    gap: 2px;
    flex: none;
  }
  .head {
    margin-left: 6px;
    color: var(--muted);
    white-space: nowrap;
  }
  .anchor {
    position: relative;
    flex: none;
  }
  .panelmenu {
    position: absolute;
    right: 0;
    top: 33px;
    z-index: 60;
    min-width: 230px;
  }
  .tick {
    width: 12px;
    height: 12px;
    display: inline-flex;
  }
  .main {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    position: relative;
  }
  .status {
    flex: none;
    height: 24px;
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 0 10px;
    background: var(--surface);
    border-top: 1px solid var(--line);
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
  }
  .status .attn {
    color: var(--white);
    padding-left: 12px;
    position: relative;
  }
  /* a ring, not only a colour, marks "needs attention" */
  .status .attn::before {
    content: '';
    position: absolute;
    left: 0;
    top: 50%;
    width: 7px;
    height: 7px;
    margin-top: -4.5px;
    border: 1.5px solid var(--gold);
    border-radius: 50%;
  }
  .status .warn {
    color: var(--white);
    text-decoration: underline;
    text-decoration-color: var(--ember);
    text-underline-offset: 3px;
  }
  .status .hint {
    margin-left: auto;
    color: var(--white);
    min-width: 0;
  }
  .link {
    border: 0;
    background: transparent;
    padding: 0;
    color: var(--white);
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
    font-size: 12px;
  }
  @media (max-width: 720px) {
    .top {
      gap: 6px;
    }
    .head,
    .lbl-wide {
      display: none;
    }
    .status {
      gap: 10px;
    }
  }
  @media (max-width: 440px) {
    .name {
      display: none;
    }
  }
</style>
