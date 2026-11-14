<script lang="ts">
  // Left pane: search (debounced, cancellable, paged), the domain list, and browsing one domain with a cursor.
  import { onMount } from 'svelte';
  import { errText, fmtInt, get } from '../api';
  import Icon from '../ui/Icon.svelte';
  import { DOMAINS, FILTER_KINDS, net, word, type Card } from './net.svelte';

  let list = $state.raw<Card[]>([]);
  let mode = $state<'search' | 'browse' | null>(null);
  let page = $state(0);
  let more = $state(false);
  let cursor = $state<string | null>(null);
  let ms = $state<number | null>(null);
  let busy = $state(false);
  let moreBusy = $state(false);
  let err = $state<string | null>(null);
  let input: HTMLInputElement;
  let ctl: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const qs = (o: Record<string, any>) =>
    Object.entries(o)
      .filter(([, v]) => v !== '' && v !== null && v !== undefined)
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join('&');

  const domains = $derived<{ domain: string; nodes: number }[]>(net.ov?.domains ?? []);
  const query = $derived(net.q.trim());
  const showDomains = $derived(!query && !net.domain && !net.kind);
  const ready = $derived(!!net.ov);

  // every change of the query or a filter: cancel what is running, wait 250 ms, ask again
  $effect(() => {
    const q = query, d = net.domain, k = net.kind;
    if (timer) clearTimeout(timer);
    ctl?.abort();
    ctl = null;
    err = null;
    if (!ready) return;
    if (!q && !d && !k) {
      busy = false;
      list = [];
      mode = null;
      return;
    }
    busy = true;
    timer = setTimeout(() => (q ? search(q, d, k, 0) : browse(d, k, null)), q ? 250 : 0);
  });

  async function search(q: string, d: string, k: string, p: number) {
    const c = (ctl = new AbortController());
    try {
      const r = await get<any>(`api/ws/search?${qs({ q, domain: d, kind: k, page: p, limit: 25 })}`, c.signal);
      if (c.signal.aborted) return;
      list = p === 0 ? r.results : [...list, ...r.results];
      more = !!r.more;
      page = p;
      ms = r.ms ?? null;
      mode = 'search';
      if (p === 0 && r.mode === 'id' && r.results.length === 1) net.select(r.results[0].id, 'root');
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      err = errText(e);
      if (p === 0) list = [];
    } finally {
      if (ctl === c) {
        busy = false;
        moreBusy = false;
      }
    }
  }
  async function browse(d: string, k: string, cur: string | null) {
    const c = (ctl = new AbortController());
    try {
      const r = await get<any>(`api/ws/browse?${qs({ domain: d, kind: k, cursor: cur, limit: 30 })}`, c.signal);
      if (c.signal.aborted) return;
      const seen = new Set(cur ? list.map((x) => x.id) : []);
      list = cur ? [...list, ...r.results.filter((x: Card) => !seen.has(x.id))] : r.results;
      cursor = r.cursor ?? null;
      more = !!r.cursor;
      ms = r.ms ?? null;
      mode = 'browse';
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      err = errText(e);
      if (!cur) list = [];
    } finally {
      if (ctl === c) {
        busy = false;
        moreBusy = false;
      }
    }
  }
  function loadMore() {
    moreBusy = true;
    if (mode === 'search') search(query, net.domain, net.kind, page + 1);
    else browse(net.domain, net.kind, cursor);
  }
  const secs = (v: number) => (v < 1 ? 'under 1 ms' : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`);

  onMount(() => {
    net.searchEl = input;
    return () => {
      ctl?.abort();
      if (timer) clearTimeout(timer);
      if (net.searchEl === input) net.searchEl = null;
    };
  });
</script>

<aside class="ex" aria-label="Explore">
  <div class="top">
    <div class="q">
      <span class="ic"><Icon name="search" size={14} /></span>
      <input bind:this={input} class="field" type="search" bind:value={net.q} placeholder="Title, alias, node id, Q42 or DOI" aria-label="Search the network"
        spellcheck="false" autocomplete="off" onkeydown={(e) => e.key === 'Escape' && net.q && (e.stopPropagation(), (net.q = ''))} />
    </div>
    <div class="filters">
      <select class="field" bind:value={net.domain} aria-label="Domain">
        <option value="">Every domain</option>
        {#each DOMAINS as d (d)}<option value={d}>{word(d)}</option>{/each}
      </select>
      <select class="field" bind:value={net.kind} aria-label="Kind">
        <option value="">Every kind</option>
        {#each FILTER_KINDS as k (k)}<option value={k}>{word(k)}</option>{/each}
      </select>
    </div>
    {#if !showDomains}
      <div class="meta" aria-live="polite">
        {#if busy}
          <span class="muted">{query ? 'Searching…' : 'Reading…'}</span>
        {:else if err}
          <span class="err">{err}</span>
        {:else if mode}
          <span class="muted num">{fmtInt(list.length)}{more ? '+' : ''} {list.length === 1 && !more ? 'result' : 'results'}{ms != null ? ` in ${secs(ms)}` : ''}</span>
        {/if}
        {#if !query && (net.domain || net.kind)}
          <button class="btn quiet back" onclick={() => ((net.domain = ''), (net.kind = ''))}><Icon name="back" size={14} />All domains</button>
        {/if}
      </div>
    {/if}
  </div>

  <div class="list scroll" aria-busy={busy}>
    {#if showDomains}
      {#if net.ov}
        <h2>Domains</h2>
        <ul class="domains">
          {#each domains as d (d.domain)}
            <li><button onclick={() => net.browse(d.domain)}><span class="ellipsis">{word(d.domain)}</span><span class="num muted">{fmtInt(d.nodes)}</span></button></li>
          {/each}
        </ul>
      {:else if net.ovErr}
        <p class="muted pad">Connect a source to search this network.</p>
      {:else}
        <p class="muted pad">Reading the network…</p>
      {/if}
    {:else}
      {#if !busy && !err && mode && !list.length}
        <p class="muted pad">{query ? `Nothing matches “${query}”. Try fewer words or another filter.` : 'No nodes here. Choose another domain or kind.'}</p>
      {/if}
      <ul class="results" class:dim={busy}>
        {#each list as n (n.id)}
          <li>
            <button class="item" aria-current={net.sel === n.id} onclick={() => net.select(n.id, 'root')}>
              <span class="t ellipsis">{n.title || 'Untitled'}</span>
              <span class="m muted ellipsis">{word(n.kind)} in {word(n.domain)}{n.edited ? ', changed in this fork' : ''}</span>
              {#if n.summary}<span class="s muted">{n.summary}</span>{/if}
            </button>
          </li>
        {/each}
      </ul>
      {#if more && !busy}
        <div class="more"><button class="btn" disabled={moreBusy} onclick={loadMore}>{moreBusy ? 'Reading…' : 'More results'}</button></div>
      {/if}
    {/if}
  </div>
</aside>

<style>
  .ex {
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
    height: 100%;
    background: var(--void);
  }
  .top {
    flex: none;
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px 10px 8px;
    border-bottom: 1px solid var(--line-soft);
  }
  .q {
    position: relative;
  }
  .q .ic {
    position: absolute;
    left: 8px;
    top: 7px;
    color: var(--muted);
    pointer-events: none;
    display: flex;
  }
  .q .field {
    padding-left: 28px;
  }
  .filters {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }
  .meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-height: 24px;
    font-size: 12px;
  }
  .meta .err {
    color: #ffb9a6;
  }
  .back {
    height: 24px;
    padding: 0 6px;
    gap: 4px;
    color: var(--muted);
    margin-left: auto;
  }
  .back:hover {
    color: var(--white);
  }
  .list {
    flex: 1;
    min-height: 0;
  }
  .pad {
    padding: 12px;
  }
  h2 {
    font-weight: 500;
    padding: 10px 12px 4px;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .domains {
    padding: 0 4px 8px;
  }
  .domains button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    height: 28px;
    padding: 0 8px;
    border: 0;
    border-radius: 2px;
    background: transparent;
    text-align: left;
    cursor: pointer;
  }
  .domains button:hover {
    background: var(--raise);
  }
  .results {
    padding: 4px;
  }
  .results.dim {
    opacity: 0.55;
  }
  .item {
    display: grid;
    gap: 1px;
    width: 100%;
    padding: 6px 8px;
    border: 0;
    border-radius: 2px;
    background: transparent;
    text-align: left;
    cursor: pointer;
    min-width: 0;
  }
  .item:hover {
    background: var(--raise);
  }
  .item[aria-current='true'] {
    background: #1f1f1f;
    box-shadow: inset 2px 0 0 var(--ember);
  }
  .m {
    font-size: 12px;
  }
  .s {
    font-size: 12px;
    line-height: 16px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .more {
    padding: 4px 12px 12px;
  }
  .more .btn {
    width: 100%;
  }
</style>
