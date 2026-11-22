<script lang="ts">
  // FG-MPN networks only: the bytes the network takes on disk (by part, and against the original when the server has it),
  // and a lookup for one derived statement by its address.
  import { errText, fmtBytes, fmtInt, get } from '../api';
  import { net } from './net.svelte';

  let storage = $state.raw<any>(null);
  let err = $state('');
  let addr = $state('');
  let res = $state.raw<any>(null);
  let resBusy = $state(false);
  let resErr = $state('');

  const PART: Record<string, string> = {
    blocks: 'Records (compressed blocks)', post: 'Search postings', terms: 'Search terms', nidx: 'Node id index',
    xidx: 'Alias index', hubpages: 'Relationship pages of large nodes', hubpg: 'Page index of large nodes',
    hubdir: 'Large-node directory', meta: 'Segment description', dir: 'Section directory', nprefix: 'Node id prefixes',
    nfence: 'Node id fences', xfence: 'Alias fences',
  };

  $effect(() => {
    const ac = new AbortController();
    get<any>('api/ws/fgmpn/storage', ac.signal)
      .then((r) => (storage = r))
      .catch((e) => { if (e?.name !== 'AbortError') err = errText(e); });
    return () => ac.abort();
  });

  async function resolve(e: SubmitEvent) {
    e.preventDefault();
    const a = addr.trim();
    if (!a) return;
    resBusy = true;
    resErr = '';
    res = null;
    try {
      res = await get<any>(`api/ws/fgmpn/resolve?address=${encodeURIComponent(a)}`);
    } catch (x) {
      resErr = errText(x);
    }
    resBusy = false;
  }

  const open = (nid: string | null | undefined) => nid && net.select(nid, net.canvas?.has(nid) ? 'none' : 'expand');
  const parts = $derived<[string, number][]>(Object.entries(storage?.fgmpn?.by_section ?? {}) as [string, number][]);
  const STATUS: Record<string, string> = {
    DERIVED: 'Derived and checked', STORED: 'Stored', UNKNOWN: 'Unknown', UNAUTHORIZED: 'Not visible here', INVALID: 'Not a valid address',
  };
</script>

<section class="sec">
  <h3>Storage</h3>
  {#if err}
    <p class="error-line">{err}</p>
  {:else if !storage}
    <p class="muted">Reading…</p>
  {:else}
    <dl class="kv">
      <dt>On disk</dt><dd class="num">{fmtBytes(storage.fgmpn.deployed_bytes)} in {fmtInt(storage.fgmpn.segments)} {storage.fgmpn.segments === 1 ? 'segment' : 'segments'}</dd>
      {#if storage.v1}
        <dt>Original network</dt><dd class="num">{fmtBytes(storage.v1.bytes)}{storage.ratio_v1_over_fgmpn ? `, ${storage.ratio_v1_over_fgmpn.toFixed(1)} times as much` : ''}</dd>
      {/if}
    </dl>
    <ul class="parts">
      {#each parts as [k, v] (k)}
        <li><span class="grow">{PART[k] ?? k}</span><span class="num">{fmtBytes(v)}</span></li>
      {/each}
    </ul>
  {/if}
</section>

<section class="sec">
  <h3>Look up a derived statement</h3>
  <form class="look" onsubmit={resolve}>
    <label class="sr-only" for="fg-addr">FG-MPN address</label>
    <input id="fg-addr" class="field mono" bind:value={addr} placeholder="fg://infer/closure.P131@2/wd:Q…/wd:Q…" spellcheck="false" autocomplete="off" />
    <button class="btn" type="submit" disabled={resBusy || !addr.trim()}>{resBusy ? 'Looking up…' : 'Look up'}</button>
  </form>
  {#if resErr}<p class="error-line">{resErr}</p>{/if}
  {#if res}
    <p>{STATUS[res.status] ?? res.status}{res.reason ? `: ${res.reason}` : ''}</p>
    {#if res.proof?.steps}
      <ol class="steps">
        {#each res.proof.steps as p, k (k)}
          <li>
            <button class="link" onclick={() => open(p.s_id)} title={p.s}>{p.s_title ?? p.s}</button>
            <span class="muted">{p.p_label ?? p.p}</span>
            <button class="link" onclick={() => open(p.o_id)} title={p.o}>{p.o_title ?? p.o}</button>
          </li>
        {/each}
      </ol>
      <dl class="kv">
        <dt>Rule</dt><dd class="mono">{res.proof.rule}</dd>
        {#if res.check}<dt>Check</dt><dd>{res.check.detail}</dd>{/if}
      </dl>
    {/if}
  {/if}
</section>

<style>
  .sec {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px 14px 12px;
    border-top: 1px solid var(--line-soft);
    min-width: 0;
  }
  h3 {
    font-weight: 500;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .parts li {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 2px 0;
  }
  .kv {
    display: grid;
    grid-template-columns: 150px minmax(0, 1fr);
    gap: 3px 12px;
    margin: 0;
  }
  .kv dt {
    color: var(--muted);
  }
  .kv dd {
    margin: 0;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .look {
    display: flex;
    gap: 6px;
    min-width: 0;
  }
  .look input {
    flex: 1;
    min-width: 0;
    font-size: 12px;
  }
  .steps {
    margin: 0;
    padding-left: 18px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .steps li > * + * {
    margin-left: 6px;
  }
  .link {
    background: none;
    border: 0;
    padding: 0;
    color: var(--white);
    cursor: pointer;
    text-align: left;
    font: inherit;
  }
  .link:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
</style>
