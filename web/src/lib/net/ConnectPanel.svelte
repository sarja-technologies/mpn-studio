<script lang="ts">
  // Connect the selected node to another: click the target on the canvas or find it here, then name the relationship.
  import { onMount } from 'svelte';
  import { errText, get } from '../api';
  import { net, REL_SUGGESTIONS, word, type Card } from './net.svelte';

  let q = $state('');
  let found = $state.raw<Card[]>([]);
  let searching = $state(false);
  let err = $state<string | null>(null);
  let rel = $state('related to');
  let saving = $state(false);
  let ctl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let qEl = $state<HTMLInputElement | null>(null);
  let relEl = $state<HTMLInputElement | null>(null);

  const c = $derived(net.connecting);

  $effect(() => {
    const text = q.trim();
    if (timer) clearTimeout(timer);
    ctl?.abort();
    err = null;
    if (!text) {
      found = [];
      searching = false;
      return;
    }
    searching = true;
    timer = setTimeout(async () => {
      const my = (ctl = new AbortController());
      try {
        const r = await get<any>(`api/ws/search?q=${encodeURIComponent(text)}&limit=8`, my.signal);
        found = r.results.filter((x: Card) => x.id !== net.connecting?.src);
      } catch (e: any) {
        if (e?.name !== 'AbortError') err = errText(e);
      } finally {
        if (ctl === my) searching = false;
      }
    }, 250);
  });

  function pick(x: Card) {
    if (!net.connecting) return;
    net.connecting = { ...net.connecting, dst: x.id, dstTitle: x.title };
    q = '';
    queueMicrotask(() => relEl?.focus());
  }
  function change() {
    if (!net.connecting) return;
    net.connecting = { ...net.connecting, dst: null, dstTitle: null };
    queueMicrotask(() => qEl?.focus());
  }
  async function go(e: SubmitEvent) {
    e.preventDefault();
    if (!c?.dst || !rel.trim()) return;
    const { src, dst } = c;
    saving = true;
    const ok = await net.connect(src, dst, rel.trim());
    saving = false;
    if (ok) net.connecting = null;
  }
  function key(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      net.connecting = null;
    }
  }
  $effect(() => {
    if (c?.dst) relEl?.focus();
  });
  onMount(() => {
    qEl?.focus();
    return () => {
      ctl?.abort();
      if (timer) clearTimeout(timer);
    };
  });
</script>

{#if c}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <form class="cp pop" onsubmit={go} onkeydown={key} aria-label="Connect two nodes">
    <p class="h">Connect “{c.srcTitle}” to</p>
    {#if c.dst}
      <div class="row">
        <span class="grow ellipsis target">{c.dstTitle}</span>
        <button type="button" class="btn quiet" onclick={change}>Change</button>
      </div>
      <div>
        <label class="lbl" for="cp-rel">Relationship</label>
        <input id="cp-rel" class="field" bind:this={relEl} bind:value={rel} maxlength="80" autocomplete="off" />
        <div class="sugg">
          {#each REL_SUGGESTIONS as s (s)}<button type="button" class="btn quiet" aria-pressed={rel === s} onclick={() => (rel = s)}>{s}</button>{/each}
        </div>
      </div>
    {:else}
      <p class="muted">Click a node on the canvas, or find one:</p>
      <input class="field" bind:this={qEl} bind:value={q} type="search" placeholder="Title, alias or id" aria-label="Find the node to connect to" autocomplete="off" />
      {#if searching}<p class="muted">Searching…</p>{/if}
      {#if err}<p class="error-line">{err}</p>{/if}
      {#if found.length}
        <ul class="found scroll">
          {#each found as x (x.id)}
            <li><button type="button" onclick={() => pick(x)}><span class="ellipsis">{x.title}</span><span class="muted ellipsis">{word(x.kind)} in {word(x.domain)}</span></button></li>
          {/each}
        </ul>
      {:else if q.trim() && !searching && !err}
        <p class="muted">Nothing matches.</p>
      {/if}
    {/if}
    <div class="row end">
      <button type="button" class="btn" onclick={() => (net.connecting = null)}>Cancel</button>
      <button class="btn primary" disabled={!c.dst || !rel.trim() || saving}>{saving ? 'Connecting…' : 'Connect'}</button>
    </div>
  </form>
{/if}

<style>
  .cp {
    position: absolute;
    top: 48px;
    right: 10px;
    width: 340px;
    max-width: calc(100% - 20px);
    max-height: calc(100% - 96px);
    overflow: auto;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    z-index: 5;
  }
  .h {
    font-weight: 500;
    overflow-wrap: anywhere;
  }
  .target {
    padding: 4px 8px;
    border: 1px solid var(--line);
    border-radius: 2px;
    background: var(--void);
  }
  .sugg {
    display: flex;
    flex-wrap: wrap;
    gap: 2px;
    margin-top: 6px;
  }
  .sugg .btn {
    height: 24px;
    padding: 0 6px;
    color: var(--muted);
    font-weight: 400;
  }
  .sugg .btn[aria-pressed='true'] {
    color: var(--white);
  }
  .found {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 240px;
  }
  .found button {
    display: grid;
    width: 100%;
    padding: 4px 6px;
    border: 0;
    border-radius: 2px;
    background: transparent;
    text-align: left;
    cursor: pointer;
    min-width: 0;
  }
  .found button:hover {
    background: var(--raise);
  }
  .found .muted {
    font-size: 12px;
  }
  .end {
    justify-content: flex-end;
  }
</style>
