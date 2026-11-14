<script lang="ts">
  // Right pane: the selected node in full (and its edit form in a fork), or, with nothing selected, the network itself.
  import { errText, fmtBytes, fmtInt, get } from '../api';
  import { app } from '../state.svelte';
  import Icon from '../ui/Icon.svelte';
  import NetSources from './NetSources.svelte';
  import NodeEdit from './NodeEdit.svelte';
  import { net, word, type Rel } from './net.svelte';

  let { onduplicate }: { onduplicate: () => void } = $props();

  let editing = $state(false);
  let rels = $state.raw<{ out: Rel[]; in: Rel[]; more_out: boolean; more_in: boolean }>({ out: [], in: [], more_out: false, more_in: false });
  let relBusy = $state<'' | 'out' | 'in'>('');
  let pane: HTMLElement;

  const d = $derived(net.detail);
  const n = $derived(d?.node);
  const ov = $derived(net.ov);
  const st = $derived(net.state);

  // a new node, or the same node read again after an edit: start from the first page of relationships
  $effect(() => {
    const x = net.detail;
    rels = x ? { out: x.out ?? [], in: x.in ?? [], more_out: !!x.more_out, more_in: !!x.more_in } : { out: [], in: [], more_out: false, more_in: false };
  });
  let lastSel: string | null = null;
  $effect(() => {
    const s = net.sel;
    if (s !== lastSel) {
      lastSel = s;
      editing = false;
      pane?.scrollTo({ top: 0 });
    }
  });

  async function moreRels(dir: 'out' | 'in') {
    if (!n) return;
    relBusy = dir;
    try {
      const have = rels[dir];
      const r = await get<any>(`api/ws/node/${encodeURIComponent(n.id)}/relationships?direction=${dir}&offset=${have.length}&limit=50`);
      rels = { ...rels, [dir]: [...have, ...r.results], [dir === 'out' ? 'more_out' : 'more_in']: !!r.more };
    } catch (e) {
      app.toast(errText(e), 'error');
    }
    relBusy = '';
  }
  function go(r: Rel) {
    if (!r.present) return;
    net.select(r.node, net.canvas?.has(r.node) ? 'none' : 'expand');
  }
  function unlink(r: Rel, dir: 'out' | 'in') {
    if (!n) return;
    if (dir === 'out') net.disconnect(n.id, r.rel_id, r.node, r.rel);
    else net.disconnect(r.node, r.rel_id, n.id, r.rel);
  }

  const statements = $derived<string[]>((n?.cells?.knowledge?.statements ?? []).map((s: any) => (typeof s === 'string' ? s : s?.text)).filter(Boolean));
  const ids = $derived<[string, string][]>(Object.entries(n?.ids ?? {}).filter(([, v]) => v != null && v !== '') as [string, string][]);
  const prov = $derived(n?.provenance ?? {});
  const datasetName = (v: any) => ov?.datasets?.find((x: any) => x.slug === v || x.id === v)?.name ?? (v === 'fork' ? 'Added in this fork' : v ?? '');
  const ms = (v: number | null | undefined) => (v == null ? '' : v < 1 ? 'under 1 ms' : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`);
  const changes = $derived(st?.changes);
  const rss = $derived(net.res?.process_rss ?? st?.process_rss ?? null);
</script>

<aside class="insp scroll" bind:this={pane} aria-label="Inspector">
  {#if st && st.read_only}
    <div class="protected">
      <p>This is the protected original. Duplicate it to make an editable copy.</p>
      <button class="btn" onclick={onduplicate}>Duplicate…</button>
    </div>
  {/if}

  {#if net.sel}
    {#if net.detailErr}
      <div class="pad">
        <p class="error-line">{net.detailErr}</p>
        <button class="btn" onclick={() => net.select(null)}>Back to the network</button>
      </div>
    {:else if !n}
      <p class="muted pad">Reading the node…</p>
    {:else if editing}
      <div class="pad"><NodeEdit node={n} ondone={() => (editing = false)} /></div>
    {:else}
      <div class="pad head">
        <h2 class="title">{n.title || 'Untitled'}</h2>
        <p class="muted">{word(n.kind)} in {word(n.domain)}{net.detailBusy ? ', reading…' : ''}</p>
        {#if n.edited}<p class="changed">Changed in this fork</p>{/if}
        {#if net.editable}
          <div class="acts">
            <button class="btn" onclick={() => (editing = true)} disabled={net.busy}>Edit</button>
            <button class="btn" onclick={() => net.duplicate(n.id)} disabled={net.busy}>Duplicate node</button>
            <button class="btn danger" onclick={() => (net.dialog = 'delete')} disabled={net.busy}>Delete node</button>
          </div>
        {/if}
        {#if n.summary}<p class="summary">{n.summary}</p>{/if}
      </div>

      {#if statements.length}
        <section class="sec">
          <h3>What it says</h3>
          <ul class="stmts">{#each statements as s, i (i)}<li>{s}</li>{/each}</ul>
        </section>
      {/if}

      {#if n.aliases?.length}
        <section class="sec">
          <h3>Also known as</h3>
          <p>{n.aliases.slice(0, 40).join(', ')}{n.aliases.length > 40 ? ` and ${fmtInt(n.aliases.length - 40)} more` : ''}</p>
        </section>
      {/if}

      {#if n.notes}
        <section class="sec">
          <h3>Notes</h3>
          <p class="pre">{n.notes}</p>
        </section>
      {/if}

      <section class="sec">
        <h3>Relationships</h3>
        {#each [['out', 'From this node'], ['in', 'To this node']] as [dir, label] (dir)}
          {@const rs = rels[dir as 'out' | 'in']}
          {@const more = dir === 'out' ? rels.more_out : rels.more_in}
          <h4>{label} <span class="muted num">{fmtInt(rs.length)}{more ? '+' : ''}</span></h4>
          {#if rs.length}
            <ul class="rels">
              {#each rs as r, i (r.rel_id + r.node + i)}
                <li>
                  <span class="rel muted ellipsis" title={r.rel}>{r.rel}</span>
                  {#if r.present}
                    <button class="link ellipsis" onclick={() => go(r)} title={r.added ? 'Added in this fork' : (r.title ?? '')}>{r.title || 'Untitled'}</button>
                  {:else}
                    <span class="muted ellipsis grow" title={r.node}>Not in this network</span>
                  {/if}
                  {#if r.cross_domain && r.domain}<span class="dom muted ellipsis">{word(r.domain)}</span>{/if}
                  {#if net.editable}
                    <button class="btn icon quiet x" aria-label={`Remove the relationship ${r.rel} ${r.title ?? ''}`} title="Remove this relationship" disabled={net.busy}
                      onclick={() => unlink(r, dir as 'out' | 'in')}><Icon name="close" size={12} /></button>
                  {/if}
                </li>
              {/each}
            </ul>
            {#if more}<button class="btn more" disabled={relBusy === dir} onclick={() => moreRels(dir as 'out' | 'in')}>{relBusy === dir ? 'Reading…' : 'More'}</button>{/if}
          {:else}
            <p class="muted">None.</p>
          {/if}
        {/each}
      </section>

      {#if d.claims?.length}
        <section class="sec">
          <h3>Claims <span class="muted num">{fmtInt(d.claims.length)}{d.more_claims ? '+' : ''}</span></h3>
          <dl class="kv">
            {#each d.claims as c (c.id)}<dt>{c.property}</dt><dd>{c.value}</dd>{/each}
          </dl>
        </section>
      {/if}

      <section class="sec">
        <h3>Identifiers</h3>
        <dl class="kv">
          {#each ids as [k, v] (k)}<dt>{k === 'wikidata' ? 'Wikidata' : k}</dt><dd class="mono">{v}</dd>{/each}
          <dt>Node id</dt><dd class="mono">{n.id}</dd>
        </dl>
      </section>

      <section class="sec">
        <h3>Where this came from</h3>
        <dl class="kv">
          <dt>Dataset</dt><dd>{prov.dataset_name ?? datasetName(n.dataset)}</dd>
          <dt>Licence</dt><dd>{n.licence ?? prov.dataset_licence ?? 'Not recorded'}</dd>
          {#if prov.record}<dt>Record</dt><dd class="mono">{prov.record}</dd>{/if}
          {#if prov.url}<dt>Link</dt><dd><a href={prov.url} target="_blank" rel="noreferrer">{prov.url}</a></dd>{/if}
          {#if prov.wikipedia}<dt>Wikipedia</dt><dd><a href={prov.wikipedia} target="_blank" rel="noreferrer">{prov.wikipedia}</a></dd>{/if}
          {#if prov.attribution}<dt>Credit</dt><dd>{prov.attribution}</dd>{/if}
          {#if prov.note}<dt>Note</dt><dd>{prov.note}</dd>{/if}
        </dl>
        {#if prov.sources?.length}
          <ul class="cites">
            {#each prov.sources as s, i (i)}
              <li>{#if s.url}<a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>{:else}{s.title}{/if}{#if s.licence}<span class="muted">, {s.licence}</span>{/if}</li>
            {/each}
          </ul>
        {/if}
      </section>

      {#if n.validation}
        <section class="sec">
          <h3>Validation</h3>
          <dl class="kv">
            <dt>Status</dt><dd>{word(n.validation.status)}</dd>
            {#if n.validation.confidence != null}<dt>Confidence</dt><dd class="num">{Number(n.validation.confidence).toFixed(2)}</dd>{/if}
            {#if n.validation.method}<dt>Method</dt><dd>{n.validation.method}</dd>{/if}
          </dl>
        </section>
      {/if}
    {/if}
  {:else}
    <div class="pad head">
      <h2 class="title">{ov?.name ?? st?.name ?? 'This network'}</h2>
      {#if st && st.name && ov?.name && st.name !== ov.name}<p class="muted">Opened as {st.name}</p>{/if}
      {#if ov?.description}<p>{ov.description}</p>{/if}
      {#if st?.warning}<p class="problem">{st.warning}</p>{/if}
    </div>

    {#if ov}
      <section class="sec">
        <h3>About this network</h3>
        <dl class="kv">
          <dt>Nodes</dt><dd class="num">{fmtInt(ov.validated_nodes)}</dd>
          <dt>Relationships</dt><dd class="num">{fmtInt(ov.relationships)}</dd>
          {#if ov.storage_bytes}<dt>Storage on disk</dt><dd class="num">{fmtBytes(ov.storage_bytes)}</dd>{/if}
          {#if ov.packs?.length}<dt>Partitions</dt><dd class="num">{fmtInt(ov.packs.reduce((a: number, p: any) => a + (p.partitions || 0), 0))}</dd>{/if}
          {#if ov.version}<dt>Version</dt><dd>{ov.version}</dd>{/if}
        </dl>
      </section>
      {#if ov.datasets?.length}
        <section class="sec">
          <h3>Datasets</h3>
          <ul class="ds">
            {#each ov.datasets as x (x.id)}
              <li><span class="grow">{x.name}<span class="lic muted">{x.licence}</span></span><span class="num">{fmtInt(x.nodes)}</span></li>
            {/each}
          </ul>
        </section>
      {/if}
    {/if}

    {#if changes}
      <section class="sec">
        <h3>Changes in this fork</h3>
        {#if changes.revision === 0}
          <p class="muted">No changes yet. Select a node and choose Edit, or add a node.</p>
        {:else}
          <dl class="kv">
            <dt>Nodes added</dt><dd class="num">{fmtInt(changes.nodes_added)}</dd>
            <dt>Nodes edited</dt><dd class="num">{fmtInt(changes.nodes_edited)}</dd>
            <dt>Nodes deleted</dt><dd class="num">{fmtInt(changes.nodes_deleted)}</dd>
            <dt>Relationships added</dt><dd class="num">{fmtInt(changes.relationships_added)}</dd>
            <dt>Relationships removed</dt><dd class="num">{fmtInt(changes.relationships_removed)}</dd>
            <dt>Revision</dt><dd class="num">{fmtInt(changes.revision)}</dd>
            <dt>Change file</dt><dd class="num">{fmtBytes(changes.bytes)}</dd>
          </dl>
        {/if}
      </section>
    {/if}

    <section class="sec">
      <h3>This computer</h3>
      <dl class="kv">
        {#if st?.source}<dt>Reading from</dt><dd>{st.source.label}</dd>{/if}
        {#if rss != null}<dt>Memory in use</dt><dd class="num">{fmtBytes(rss)}</dd>{/if}
        {#if net.res?.last_query_ms != null}<dt>Last query</dt><dd class="num">{ms(net.res.last_query_ms)}</dd>{/if}
        {#if st}<dt>Project folder</dt><dd class="num">{fmtBytes(st.project_bytes)}</dd>{/if}
      </dl>
    </section>

    {#if net.ov}
      <!-- while the network cannot be read, the sources are shown in the centre instead -->
      <section class="sec">
        <h3>Sources</h3>
        <NetSources />
      </section>
    {/if}
  {/if}
</aside>

<style>
  .insp {
    height: 100%;
    min-height: 0;
    background: var(--surface);
    padding-bottom: 16px;
  }
  .pad {
    padding: 12px 14px 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .head {
    padding-bottom: 12px;
  }
  .protected {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    border-bottom: 1px solid var(--line);
  }
  .protected p {
    flex: 1;
    color: var(--muted);
  }
  .title {
    font-size: 16px;
    line-height: 22px;
    font-weight: 500;
    letter-spacing: -0.01em;
    overflow-wrap: anywhere;
  }
  .changed {
    color: var(--white);
  }
  .acts {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .summary {
    overflow-wrap: anywhere;
  }
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
  h3 .muted,
  h4 .muted {
    font-weight: 400;
    margin-left: 4px;
  }
  h4 {
    margin: 4px 0 0;
    font-size: 13px;
    font-weight: 400;
    color: var(--muted);
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .stmts {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .stmts li {
    padding-left: 10px;
    border-left: 1px solid var(--line);
  }
  .pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .rels li {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 26px;
    min-width: 0;
  }
  .rel {
    flex: none;
    width: 96px;
  }
  .rels .link {
    flex: 1;
    min-width: 0;
  }
  .dom {
    flex: none;
    max-width: 96px;
    font-size: 12px;
  }
  .x {
    width: 22px;
    height: 22px;
    color: var(--muted);
  }
  .x:hover:not(:disabled) {
    color: var(--white);
    background: var(--raise);
  }
  .more {
    align-self: flex-start;
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
  .ds li {
    display: flex;
    align-items: baseline;
    gap: 12px;
    padding: 3px 0;
  }
  .lic {
    display: block;
    font-size: 12px;
  }
  .cites {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .cites a {
    overflow-wrap: anywhere;
  }
</style>
