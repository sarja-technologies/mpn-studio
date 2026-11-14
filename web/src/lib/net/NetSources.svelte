<script lang="ts">
  // The places this network can be read from: which one is in use, which answer, and adding another.
  import { pickFolder } from '../desktop.svelte';
  import { isTauri } from '../desktop.svelte';
  import { net } from './net.svelte';

  let adding = $state(false);
  let mode = $state<'local' | 'remote'>('local');
  let target = $state('');
  let label = $state('');

  const using = $derived(net.state?.source?.index ?? null);

  async function choose() {
    const dir = await pickFolder('Choose the folder that holds this network (it contains parts/)');
    if (dir) target = dir;
  }
  async function add(e: SubmitEvent) {
    e.preventDefault();
    if (!target.trim()) return;
    if (await net.addSource(mode, target.trim(), label.trim())) {
      adding = false;
      target = label = '';
    }
  }
  const where = (s: { mode: string; target: string }) => (s.mode === 'local' ? 'Folder ' : 'Address ') + s.target;
</script>

<ul class="srcs">
  {#each net.sources as s (s.index)}
    <li>
      <div class="grow">
        <div class="ellipsis">{s.label}</div>
        <div class="muted small ellipsis" title={s.target}>{where(s)}</div>
        {#if using === s.index}
          <div class="small">In use</div>
        {:else if s.available === false}
          <div class="small why">{s.reason ?? 'Not reachable.'}</div>
        {:else if s.available === true && s.ms != null && using !== s.index}
          <div class="muted small">Answered in {Math.round(s.ms)} ms</div>
        {:else if s.available == null}
          <div class="muted small">Not checked yet</div>
        {/if}
      </div>
      {#if using !== s.index}
        <button class="btn" disabled={net.busy || s.available === false} onclick={() => net.useSource(s.index)}
          title={s.available === false ? 'This source did not answer. Check again first.' : ''}>Use</button>
      {/if}
    </li>
  {/each}
</ul>
<div class="row acts">
  <button class="btn" disabled={net.busy} onclick={() => net.probe()}>{net.busy ? 'Checking…' : 'Check again'}</button>
  {#if !adding}<button class="btn" onclick={() => (adding = true)}>Add a source…</button>{/if}
</div>
{#if adding}
  <form class="add" onsubmit={add}>
    <div class="row">
      <select class="field" bind:value={mode} aria-label="Kind of source">
        <option value="local">A folder on this computer</option>
        <option value="remote">Another computer's address</option>
      </select>
    </div>
    <div class="row">
      <input class="field grow" bind:value={target} placeholder={mode === 'local' ? '/full/path/to/the/network' : 'http://computer.local:8844'}
        aria-label={mode === 'local' ? 'Folder' : 'Address'} spellcheck="false" autocomplete="off" />
      {#if mode === 'local' && isTauri()}<button type="button" class="btn" onclick={choose}>Choose…</button>{/if}
    </div>
    <input class="field" bind:value={label} placeholder="Name (optional), for example Office server" aria-label="Name" />
    <div class="row">
      <button class="btn primary" disabled={!target.trim() || net.busy}>Add source</button>
      <button type="button" class="btn" onclick={() => (adding = false)}>Cancel</button>
    </div>
  </form>
{/if}

<style>
  .srcs {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
  }
  .srcs li {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid var(--line-soft);
    min-width: 0;
  }
  .srcs li:first-child {
    padding-top: 0;
  }
  .small {
    font-size: 12px;
  }
  .why {
    color: #ffb9a6;
  }
  .acts {
    margin-top: 8px;
  }
  .add {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-top: 8px;
  }
</style>
