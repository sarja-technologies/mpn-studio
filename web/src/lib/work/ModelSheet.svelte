<script lang="ts">
  // The open project's model: this network plus a cognitive core (an existing open-weight language model that answers by
  // reading the nodes; attaching one does not train it). Build, test and export it here.
  import { onMount } from 'svelte';
  import { errText, fmtBytes } from '../api';
  import { desk } from '../desktop.svelte';
  import { studio } from '../studio/studio.svelte';
  import ModelPage from '../studio/ModelPage.svelte';
  import { work } from './work.svelte';

  const pid = desk.project?.id ?? '';
  const model = $derived(studio.data?.models.find((m) => m.network?.id === pid) ?? null);
  let coreId = $state('');
  let busy = $state(false);
  let error = $state<string | null>(null);
  let importing = $state<string | null>(null);

  onMount(() => {
    studio.load().then(() => (coreId ||= studio.cores.find((c) => c.present)?.id ?? ''));
    const t = setInterval(() => !document.hidden && studio.load(), 8000);
    return () => clearInterval(t);
  });
  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && !document.querySelector('dialog[open]')) work.dialog = null;
  }
  async function attach() {
    busy = true;
    error = null;
    try {
      await studio.createModel({ name: desk.project?.name ?? 'Model', core_id: coreId, network_id: pid });
    } catch (e) {
      error = errText(e);
    }
    busy = false;
  }
  async function importCore() {
    error = null;
    try {
      const c = await studio.importGguf((t) => (importing = t.progress.label || t.title));
      if (c) coreId = c.id;
    } catch (e) {
      error = errText(e);
    }
    importing = null;
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="sheet" role="region" aria-label="Model">
  <header class="bar">
    <button class="btn quiet" onclick={() => (work.dialog = null)}>Back to {desk.project?.name}</button>
    <span class="grow"></span>
    <button class="btn quiet" onclick={() => ((work.dialog = null), (work.library = true))}>Model library…</button>
  </header>
  <main class="scroll libpage">
    {#if !studio.data}
      <div class="wrap"><p class="muted">{studio.error ?? 'Reading the model library'}</p></div>
    {:else if model}
      <ModelPage id={model.id} embedded />
    {:else}
      <div class="wrap narrow">
        <h1>Make {desk.project?.name} a model you can talk to</h1>
        <p class="muted">
          Choose a cognitive core: an existing open-weight language model in GGUF format. It answers by reading this
          project's nodes. Nothing is trained, and the network is not copied.
        </p>
        {#if studio.cores.length}
          <label class="lab" for="ms-core">Cognitive core</label>
          <select id="ms-core" class="field" bind:value={coreId}>
            {#each studio.cores as c (c.id)}
              <option value={c.id} disabled={!c.present}>{c.name}{c.bytes ? ` (${fmtBytes(c.bytes)})` : ''}{c.present ? '' : ', file missing'}</option>
            {/each}
          </select>
          <div class="row acts">
            <button class="btn primary" disabled={!coreId || busy} onclick={attach}>{busy ? 'Attaching…' : 'Attach core'}</button>
            <button class="btn" onclick={importCore} disabled={!!importing}>Add a GGUF file…</button>
          </div>
        {:else}
          <p>No cognitive cores are installed yet.</p>
          <div class="row acts"><button class="btn primary" onclick={importCore} disabled={!!importing}>Add a GGUF file…</button></div>
        {/if}
        {#if importing}<p class="muted">{importing}</p>{/if}
        {#if error}<p class="error-line">{error}</p>{/if}
      </div>
    {/if}
  </main>
</div>

<style>
  .sheet {
    position: fixed;
    inset: 0;
    z-index: 40;
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
  main {
    flex: 1 1 auto;
    min-height: 0;
  }
  .wrap {
    max-width: 1100px;
    margin: 0 auto;
    padding: 28px 32px;
  }
  .narrow {
    max-width: 620px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  h1 {
    font-size: 20px;
    line-height: 28px;
    font-weight: 500;
  }
  .lab {
    margin-bottom: -6px;
    color: var(--muted);
  }
  .acts {
    gap: 8px;
  }
</style>
