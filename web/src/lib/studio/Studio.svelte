<script lang="ts">
  import { onMount } from 'svelte';
  import { desk } from '../desktop.svelte';
  import { work } from '../work/work.svelte';
  import { studio, type Tab } from './studio.svelte';
  import Models from './Models.svelte';
  import ModelPage from './ModelPage.svelte';
  import Cores from './Cores.svelte';
  import Networks from './Networks.svelte';
  import Tests from './Tests.svelte';
  import Explorer from './Explorer.svelte';
  import { catalogue } from './catalogue.svelte';

  const TABS: { id: Tab; label: string }[] = [
    { id: 'models', label: 'Pairings' },
    { id: 'cores', label: 'Cognitive cores' },
    { id: 'networks', label: 'MPN networks' },
    { id: 'tests', label: 'Tests' },
    { id: 'mpnnet', label: 'MPN Network' },
  ];
  const editing = $derived(desk.info?.project ?? null);

  onMount(() => {
    studio.load();
    catalogue.load().then(() => {            // the catalogue is read when Studio opens if it never was, or not for six hours
      const f = catalogue.data?.source.fetched_at;
      if (!f || Date.now() / 1000 - f > 6 * 3600) catalogue.refresh();
    });
    const t = setInterval(() => !document.hidden && studio.load(), 8000);
    return () => clearInterval(t);
  });
  function go(t: Tab) {
    studio.tab = t;
    studio.model = null;
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && !document.querySelector('dialog[open]')) work.library = false;
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="studio">
  <header class="top">
    <div class="brand" data-brand>
      <img src="brand/fylume/fylume-symbol-micro-white.svg" width="20" height="20" alt="Fylume" />
      <span class="name">MPN Studio</span>
    </div>
    <nav aria-label="Studio">
      {#each TABS as t (t.id)}
        <button class="tab" aria-current={studio.tab === t.id ? 'page' : undefined} onclick={() => go(t.id)}>{t.label}</button>
      {/each}
    </nav>
    <span class="grow"></span>
    <button class="btn" title="Close the model library (Esc)" onclick={() => (work.library = false)}>{editing && !desk.home ? `Back to ${editing.name}` : 'Close'}</button>
    <button class="btn quiet" onclick={() => (desk.settings = 'storage')}>Settings</button>
  </header>

  <main class="scroll libpage" class:fill={studio.tab === 'mpnnet'}>
    {#if studio.tab === 'mpnnet'}
      <Explorer />
    {:else if studio.error && !studio.data}
      <div class="wrap"><p class="error-line">{studio.error}</p></div>
    {:else if !studio.data}
      <div class="wrap"><p class="muted">Reading the library</p></div>
    {:else if studio.tab === 'models'}
      {#if studio.model}<ModelPage id={studio.model} />{:else}<Models />{/if}
    {:else if studio.tab === 'cores'}
      <Cores />
    {:else if studio.tab === 'networks'}
      <Networks />
    {:else}
      <Tests />
    {/if}
  </main>
</div>

<style>
  .studio {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    flex-direction: column;
    background: var(--void);
  }
  .top {
    flex: none;
    height: var(--bar);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 8px 0 10px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-right: 14px;
    flex: none;
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
  nav {
    display: flex;
    gap: 2px;
    height: 100%;
    min-width: 0;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .tab {
    height: 100%;
    padding: 0 10px;
    border: 0;
    border-bottom: 2px solid transparent;
    background: none;
    color: var(--muted);
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
  }
  .tab:hover {
    color: var(--white);
  }
  .tab[aria-current='page'] {
    color: var(--white);
    border-bottom-color: var(--white);
  }
  main.fill {
    position: relative;
    overflow: hidden;
  }
  main {
    flex: 1 1 auto;
    min-height: 0;
  }
  @media (max-width: 760px) {
    .name {
      display: none;
    }
  }
</style>
