<script lang="ts">
  // The first screen: create, open or reopen a project. Every project on this computer is a card; double-click opens it.
  import { onMount } from 'svelte';
  import { errText, fmtBytes, fmtInt, when } from '../api';
  import { desk } from '../desktop.svelte';
  import { app } from '../state.svelte';
  import { clickOutside } from '../ui/actions';
  import Icon from '../ui/Icon.svelte';
  import { home, kindLine, type Project } from './home.svelte';
  import CreateProject from './CreateProject.svelte';
  import OpenProject from './OpenProject.svelte';
  import ProjectDialogs from './ProjectDialogs.svelte';

  let recentOpen = $state(false);
  let menuFor = $state<string | null>(null);
  let selected = $state<string | null>(null);

  onMount(() => {
    home.load();
    const t = setInterval(() => !document.hidden && !home.dialog && home.load(), 15000);
    return () => clearInterval(t);
  });

  async function open(p: Project) {
    try {
      await home.open(p.id);
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }
  function onCardKey(e: KeyboardEvent, p: Project) {
    if (e.key === 'Enter') open(p);
  }
  function opened(t: number | null | undefined) {
    if (!t) return '';
    return new Date(t * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
  function storage(p: Project): string {
    if (p.kind === 'network') return fmtBytes(p.bytes ?? 0);
    const base = p.network?.storage_bytes;
    return base ? `${fmtBytes(base)} where it is, ${fmtBytes(p.bytes ?? 0)} here` : fmtBytes(p.bytes ?? 0);
  }
  function changes(p: Project): string {
    const c = p.changes;
    if (!c) return '';
    const parts = [];
    if (c.nodes_added) parts.push(`${fmtInt(c.nodes_added)} added`);
    if (c.nodes_edited) parts.push(`${fmtInt(c.nodes_edited)} edited`);
    if (c.nodes_deleted) parts.push(`${fmtInt(c.nodes_deleted)} deleted`);
    return parts.length ? parts.join(', ') : 'None yet';
  }
  function status(p: Project): { text: string; bad?: boolean } | null {
    if (p.damaged) return { text: 'Damaged: restore it from a backup', bad: true };
    if (p.kind === 'network') {
      const m = p.models?.[0];
      if (!m) return null;
      return m.state === 'ready' ? { text: 'Built, ready to test' } : m.state === 'needs build' ? { text: 'Needs a build' } : { text: 'Needs a cognitive core' };
    }
    const a = home.availability[p.id];
    if (!a) return null;
    if (a === 'checking') return { text: 'Checking where it can be read' };
    const use = a.sources.find((s) => s.available);
    if (use) return { text: use.mode === 'local' ? 'Read from this computer' : `Read from ${use.label}` };
    return { text: a.sources.length === 1 ? `${a.sources[0].label} is not reachable` : 'None of its locations is reachable', bad: true };
  }
</script>

<div class="home">
  <header class="bar">
    <div class="brand" data-brand>
      <img src="brand/fylume/fylume-symbol-micro-white.svg" width="20" height="20" alt="Fylume" />
      <span class="name">MPN Studio</span>
    </div>
    <span class="grow"></span>
    {#if desk.project}<button class="btn" onclick={() => (desk.home = false)}>Back to {desk.project.name}</button>{/if}
    <button class="btn quiet" onclick={() => (desk.help = 'names')}>Help</button>
    <button class="btn quiet" onclick={() => (desk.settings = 'storage')}>Settings</button>
  </header>

  <main class="scroll">
    <div class="wrap">
      <div class="actions">
        <button class="btn primary big" onclick={() => (home.dialog = { kind: 'create' })}><Icon name="plus" />Create project</button>
        <button class="btn big" onclick={() => (home.dialog = { kind: 'open' })}>Open project…</button>
        <div class="anchor" use:clickOutside={() => (recentOpen ? ((recentOpen = false), true) : false)}>
          <button class="btn big" aria-haspopup="menu" aria-expanded={recentOpen} disabled={!home.recent.length} onclick={() => (recentOpen = !recentOpen)}>
            Recent projects <Icon name="chevron" />
          </button>
          {#if recentOpen}
            <div class="pop menu recent" role="menu">
              {#each home.recent as r (r.id)}
                <button role="menuitem" onclick={() => ((recentOpen = false), open(r))}>
                  <span class="ellipsis">{r.name}</span>
                  <span class="why">{opened(r.opened)}</span>
                </button>
              {/each}
            </div>
          {/if}
        </div>
      </div>

      {#if home.error}
        <p class="error-line">{home.error}</p>
      {:else if home.loaded && !home.projects.length}
        <div class="empty">
          <p>No projects yet.</p>
          <p class="muted">Create a project to start a knowledge network of your own, or open one you already have.</p>
        </div>
      {:else}
        <ul class="grid" aria-label="Projects">
          {#each home.projects as p (p.id)}
            {@const s = status(p)}
            <li>
              <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
              <div class="card" class:sel={selected === p.id} tabindex="0" role="group" aria-label={p.name}
                onclick={() => (selected = p.id)} ondblclick={() => open(p)} onkeydown={(e) => onCardKey(e, p)}>
                <div class="head">
                  <h2 class="ellipsis" title={p.name}>{p.name}</h2>
                  <div class="anchor" use:clickOutside={() => (menuFor === p.id ? ((menuFor = null), true) : false)}>
                    <button class="btn icon quiet" aria-label="More for {p.name}" aria-haspopup="menu" aria-expanded={menuFor === p.id}
                      onclick={(e) => (e.stopPropagation(), (menuFor = menuFor === p.id ? null : p.id))}><Icon name="more" /></button>
                    {#if menuFor === p.id}
                      <div class="pop menu cardmenu" role="menu">
                        <button role="menuitem" onclick={() => ((menuFor = null), open(p))}>Open</button>
                        <button role="menuitem" onclick={() => ((menuFor = null), (home.dialog = { kind: 'create', tab: 'duplicate', from: p.id }))}>Duplicate…</button>
                        <button role="menuitem" onclick={() => ((menuFor = null), (home.dialog = { kind: 'rename', project: p }))}>Rename…</button>
                        <hr />
                        <button role="menuitem" onclick={() => ((menuFor = null), (home.dialog = { kind: 'trash', project: p }))}>Move to trash…</button>
                      </div>
                    {/if}
                  </div>
                </div>
                <p class="kind">{kindLine(p, home.projects)}</p>
                <dl>
                  <dt>Nodes</dt>
                  <dd class="num">{p.nodes != null ? fmtInt(p.nodes) : 'Unknown'}</dd>
                  <dt>Storage</dt>
                  <dd class="num">{storage(p)}</dd>
                  {#if p.kind === 'fork'}
                    <dt>Changes</dt>
                    <dd>{changes(p)}</dd>
                  {/if}
                  <dt>Modified</dt>
                  <dd class="num">{when(p.modified)}</dd>
                </dl>
                <div class="foot">
                  {#if s}<p class="status ellipsis" class:bad={s.bad} title={s.text}>{s.text}</p>{:else}<span></span>{/if}
                  <button class="btn" onclick={(e) => (e.stopPropagation(), open(p))}>Open</button>
                </div>
              </div>
            </li>
          {/each}
        </ul>
        <p class="where muted">Projects are kept in <span class="mono">{home.location}</span>. Linked networks stay where they are and are never copied.</p>
      {/if}
    </div>
  </main>
</div>

{#if home.dialog?.kind === 'create'}<CreateProject />{/if}
{#if home.dialog?.kind === 'open'}<OpenProject />{/if}
{#if home.dialog?.kind === 'rename' || home.dialog?.kind === 'trash'}<ProjectDialogs />{/if}

<style>
  .home {
    position: fixed;
    inset: 0;
    display: flex;
    flex-direction: column;
    background: var(--void);
  }
  .bar {
    flex: none;
    height: var(--bar);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 10px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
  }
  .name {
    font-weight: 500;
    font-size: 14px;
  }
  main {
    flex: 1 1 auto;
    min-height: 0;
  }
  .wrap {
    max-width: 1180px;
    margin: 0 auto;
    padding: 28px 32px 40px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 28px;
  }
  .btn.big {
    height: 34px;
    padding: 0 14px;
    font-size: 14px;
  }
  .anchor {
    position: relative;
  }
  .recent {
    position: absolute;
    top: 38px;
    left: 0;
    z-index: 30;
    min-width: 300px;
    max-width: 420px;
  }
  .recent button {
    display: flex;
    gap: 16px;
  }
  .recent .ellipsis {
    flex: 1;
    min-width: 0;
  }
  .grid {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 14px;
  }
  .card {
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px 14px 12px 16px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 2px;
    cursor: default;
    user-select: none;
  }
  .card:hover {
    border-color: #4a4a4a;
  }
  .card.sel {
    border-color: var(--white);
  }
  .head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h2 {
    flex: 1;
    min-width: 0;
    font-size: 16px;
    line-height: 22px;
    font-weight: 500;
  }
  .kind {
    margin-top: -6px;
    color: var(--muted);
  }
  dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 3px 14px;
    margin: 0;
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .foot {
    margin-top: auto;
    padding-top: 4px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }
  .status {
    min-width: 0;
    color: var(--muted);
  }
  .status.bad {
    color: var(--white);
    text-decoration: underline;
    text-decoration-color: var(--ember);
    text-underline-offset: 3px;
  }
  .cardmenu {
    position: absolute;
    right: 0;
    top: 30px;
    z-index: 30;
    min-width: 180px;
  }
  .empty {
    padding: 48px 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 15px;
  }
  .where {
    margin-top: 22px;
  }
  @media (max-width: 640px) {
    .wrap {
      padding: 20px 16px 32px;
    }
  }
</style>
