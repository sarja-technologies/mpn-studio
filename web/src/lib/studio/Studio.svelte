<script lang="ts">
  import { onMount } from 'svelte';
  import { desk } from '../desktop.svelte';
  import { studio, type Tab } from './studio.svelte';
  import Models from './Models.svelte';
  import ModelPage from './ModelPage.svelte';
  import Cores from './Cores.svelte';
  import Networks from './Networks.svelte';
  import Tests from './Tests.svelte';
  import Explorer from './Explorer.svelte';

  const TABS: { id: Tab; label: string }[] = [
    { id: 'models', label: 'Models' },
    { id: 'cores', label: 'Cognitive cores' },
    { id: 'networks', label: 'Node networks' },
    { id: 'tests', label: 'Tests' },
    { id: 'mpnnet', label: 'MPN Network' },
  ];
  const editing = $derived(desk.info?.project ?? null);

  onMount(() => {
    studio.load();
    const t = setInterval(() => !document.hidden && studio.load(), 8000);
    return () => clearInterval(t);
  });
  function go(t: Tab) {
    studio.tab = t;
    studio.model = null;
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && editing && !document.querySelector('dialog[open]')) desk.home = false;
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
    {#if editing}
      <button class="btn" title="Back to the network editor (Esc)" onclick={() => (desk.home = false)}>Edit {editing.name}</button>
    {/if}
    <button class="btn quiet" onclick={() => (desk.settings = 'storage')}>Settings</button>
  </header>

  <main class="scroll" class:fill={studio.tab === 'mpnnet'}>
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
  main :global(.wrap) {
    width: min(1080px, 100%);
    margin: 0 auto;
    padding: 24px 24px 40px;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  main :global(h1) {
    font-size: 20px;
    line-height: 26px;
    font-weight: 600;
  }
  main :global(h2) {
    font-size: 15px;
    line-height: 20px;
    font-weight: 500;
  }
  main :global(.small) {
    font-size: 12px;
  }
  main :global(table.list) {
    width: 100%;
    border-collapse: collapse;
  }
  main :global(table.list th) {
    color: var(--muted);
    font-weight: 400;
    text-align: left;
    padding: 0 10px 6px;
    border-bottom: 1px solid var(--line);
    white-space: nowrap;
  }
  main :global(table.list td) {
    padding: 9px 10px;
    border-bottom: 1px solid var(--line-soft);
    vertical-align: middle;
  }
  main :global(table.list .r) {
    text-align: right;
  }
  main :global(table.list .act) {
    white-space: nowrap;
    text-align: right;
  }
  main :global(table.list .act > *) {
    display: inline-flex;
    vertical-align: middle;
    margin-left: 6px;
  }
  main :global(.rowname) {
    border: 0;
    background: none;
    padding: 0;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    text-align: left;
    color: var(--white);
  }
  main :global(.rowname:hover) {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  main :global(.anchor) {
    position: relative;
  }
  main :global(.anchor .menu) {
    position: absolute;
    right: 0;
    top: calc(100% + 4px);
    z-index: 5;
  }
  main :global(.bar) {
    height: 4px;
    background: var(--line-soft);
    border-radius: 2px;
    overflow: hidden;
  }
  main :global(.bar > i) {
    display: block;
    height: 100%;
    background: var(--white);
  }
  @media (max-width: 760px) {
    .name {
      display: none;
    }
    main :global(.wrap) {
      padding: 16px 12px 32px;
    }
    main :global(table.list .hide-narrow) {
      display: none;
    }
  }
</style>
