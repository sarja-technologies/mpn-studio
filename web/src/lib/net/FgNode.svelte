<script lang="ts">
  // FG-MPN networks only (the overview says engine "fgmpn"): what FG-MPN proves about the selected node from stored
  // facts, with each proof and its independent check, and how the node is stored compared with the original (v1).
  import { errText, fmtInt, get } from '../api';
  import { net } from './net.svelte';

  let { id }: { id: string } = $props();

  let fg = $state.raw<any>(null);
  let err = $state('');
  let cmp = $state.raw<any>(null);
  let cmpBusy = $state(false);
  let cmpErr = $state('');

  $effect(() => {
    const nid = id;
    fg = null;
    err = '';
    cmp = null;
    cmpErr = '';
    const ac = new AbortController();
    get<any>(`api/ws/fgmpn/node/${encodeURIComponent(nid)}`, ac.signal)
      .then((r) => { if (nid === id) fg = r; })
      .catch((e) => { if (e?.name !== 'AbortError' && nid === id) err = errText(e); });
    return () => ac.abort();
  });

  async function compare() {
    cmpBusy = true;
    cmpErr = '';
    try {
      cmp = await get<any>(`api/ws/fgmpn/node/${encodeURIComponent(id)}/compare`);
    } catch (e) {
      cmpErr = errText(e);
    }
    cmpBusy = false;
  }

  const open = (nid: string | null | undefined) => nid && net.select(nid, net.canvas?.has(nid) ? 'none' : 'expand');
  const label = (s: any) => s.label.charAt(0).toUpperCase() + s.label.slice(1);
  const yesNo = (v: boolean) => (v ? 'Same' : 'Different');
  const proved = $derived<any[]>((fg?.derived ?? []).filter((s: any) => s.status === 'DERIVED' || s.status === 'STORED'));
  const rejected = $derived<any[]>((fg?.derived ?? []).filter((s: any) => s.status !== 'DERIVED' && s.status !== 'STORED'));
</script>

{#if err}
  <section class="sec"><h3>Derived from stored facts</h3><p class="error-line">{err}</p></section>
{:else if !fg}
  <section class="sec"><h3>Derived from stored facts</h3><p class="muted">Reading…</p></section>
{:else if fg.status === 'STORED'}
  <section class="sec">
    <h3>Derived from stored facts</h3>
    {#if fg.derived_listed === false}
      <p class="muted">This reader does not list derived statements for a node. Look one up by its address under the network's Storage.</p>
    {:else if !proved.length}
      <p class="muted">Nothing. FG-MPN derives administrative location, birth and death places and superclasses, and only from single-valued chains of stored claims.</p>
    {/if}
    {#each proved as s, i (s.address ?? i)}
      <div class="stmt">
        <h4>{label(s)}</h4>
        {#if s.via}
          <p class="muted">from the stored {s.via.label}: <button class="link" onclick={() => open(s.via.id)}>{s.via.title ?? s.via.key}</button></p>
        {/if}
        <p class="chain">
          {#each s.chain as c, j (c.id)}{#if j}<span class="sep muted" aria-hidden="true">›</span>{/if}<button class="link" onclick={() => open(c.id)} title={c.key}>{c.title ?? c.key}</button>{/each}
        </p>
        {#if s.status === 'DERIVED' && s.proof}
          <details>
            <summary>Proof: {fmtInt(s.proof.steps.length)} stored claims, {s.check?.ok ? 'checked' : 'not checked'}</summary>
            <ol class="steps">
              {#each s.proof.steps as p, k (k)}
                <li>
                  <button class="link" onclick={() => open(p.s_id)} title={p.s}>{p.s_title ?? p.s}</button>
                  <span class="muted">{p.p_label ?? p.p}</span>
                  <button class="link" onclick={() => open(p.o_id)} title={p.o}>{p.o_title ?? p.o}</button>
                  <span class="mono muted claim" title="Stored claim">{p.claim}</span>
                </li>
              {/each}
            </ol>
            <dl class="kv">
              <dt>Rule</dt><dd class="mono">{s.proof.rule}</dd>
              {#if s.justification}<dt>Why it holds</dt><dd>{s.justification}</dd>{/if}
              {#if s.precondition}<dt>Precondition</dt><dd>{s.precondition}</dd>{/if}
              <dt>Check</dt><dd>{s.check?.detail}</dd>
              <dt>Address</dt><dd class="mono">{s.address}</dd>
            </dl>
          </details>
        {:else if s.status === 'STORED'}
          <p class="muted">Stated directly by a stored claim.</p>
        {/if}
      </div>
    {/each}
    {#if rejected.length}
      <p class="muted">{fmtInt(rejected.length)} candidate {rejected.length === 1 ? 'statement was' : 'statements were'} not served because the proof did not check.</p>
    {/if}
  </section>

  <section class="sec">
    <h3>Stored in FG-MPN</h3>
    <dl class="kv">
      <dt>Status</dt><dd>Stored record{fg.provenance?.length ? ` from ${fg.provenance.map((p: any) => p.dataset).join(', ')}` : ''}</dd>
      <dt>Generation</dt><dd class="num">{fmtInt(fg.generation)}</dd>
      <dt>Segment</dt><dd class="mono">{fg.segment}</dd>
    </dl>
    {#if fg.compare === false}
      <!-- the original (v1) network is not here to compare with -->
    {:else if !cmp}
      <button class="btn more" onclick={compare} disabled={cmpBusy}>{cmpBusy ? 'Comparing…' : 'Compare with the original'}</button>
      {#if cmpErr}<p class="error-line">{cmpErr}</p>{/if}
    {:else}
      <dl class="kv">
        <dt>Body</dt><dd>{cmp.fgmpn.body.encoding}, {fmtInt(cmp.fgmpn.body.stored_bytes_before_block_compression)} bytes before block compression ({fmtInt(cmp.fgmpn.body.json_bytes)} bytes as JSON)</dd>
        <dt>Block</dt><dd>{fmtInt(cmp.fgmpn.records_per_block)} records compressed together</dd>
        {#if cmp.v1?.present}
          <dt>Original body</dt><dd>{fmtInt(cmp.v1.body_zlib_bytes)} bytes, compressed one record at a time</dd>
          <dt>Record</dt><dd>{yesNo(cmp.equal.row)}</dd>
          <dt>Relationships</dt><dd>{yesNo(cmp.equal.edges_out && cmp.equal.edges_in)} ({fmtInt(cmp.v1.edges)} in the original, {fmtInt(cmp.fgmpn.edges)} here)</dd>
          <dt>Claims</dt><dd>{yesNo(cmp.equal.claims)} ({fmtInt(cmp.v1.claims)})</dd>
        {:else if cmp.v1 && !cmp.v1.present}
          <dt>Original</dt><dd>Not in the original network</dd>
        {:else}
          <dt>Original</dt><dd class="muted">This server was started without the original network, so nothing is compared.</dd>
        {/if}
      </dl>
    {/if}
  </section>
{:else}
  <section class="sec"><h3>Stored in FG-MPN</h3><p>{fg.status === 'UNAUTHORIZED' ? 'Not visible to this server’s readers.' : 'Not stored.'} <span class="muted">{fg.reason}</span></p></section>
{/if}

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
  h4 {
    margin: 4px 0 0;
    font-size: 13px;
    font-weight: 400;
    color: var(--muted);
  }
  .stmt {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .chain {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 2px 6px;
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
  details {
    min-width: 0;
  }
  summary {
    cursor: pointer;
    color: var(--muted);
    width: fit-content;
  }
  summary:hover {
    color: var(--white);
  }
  .steps {
    margin: 6px 0;
    padding-left: 18px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .steps li {
    overflow-wrap: anywhere;
  }
  .steps li > * + * {
    margin-left: 6px;
  }
  .claim {
    font-size: 11px;
  }
  .kv {
    display: grid;
    grid-template-columns: 110px minmax(0, 1fr);
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
  .more {
    align-self: flex-start;
  }
</style>
