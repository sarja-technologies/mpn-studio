<script lang="ts">
  // Open MPN Network: a read-only explorer for a whole MPN network, served by an MPN dashboard over the local network
  // (remote) or read from an installed pack on this computer (local). Every list is paged by the backend; nothing
  // loads the network into memory.
  import { onMount } from 'svelte';
  import { errText, fmtInt, get, post, human } from '../api';
  import { pickFolder } from '../desktop.svelte';
  import { studio, type TaskView } from './studio.svelte';

  type Recent = { mode: 'remote' | 'local'; target: string; name: string; opened: number };
  type Conn = { open: boolean; mode: 'remote' | 'local' | null; target: string | null; name: string | null; recent: Recent[] };
  type Card = { id: string; title: string; kind: string; domain: string; summary: string | null; dataset: string | number; licence: string | null };
  type Rel = { rel: string; node: string; title: string | null; domain: string | null; kind: string | null; cross_domain: boolean; present: boolean };

  let conn = $state.raw<Conn | null>(null);
  let ov = $state.raw<any>(null);
  let error = $state<string | null>(null);
  let busy = $state<string | null>(null);
  let url = $state('');

  // results
  let q = $state('');
  let kind = $state('');
  let domain = $state('');
  let dataset = $state('');
  let boundary = $state(false);
  let list = $state.raw<Card[]>([]);
  let listMode = $state<'search' | 'browse' | null>(null);
  let page = $state(0);
  let more = $state(false);
  let cursor = $state<string | null>(null);
  let cursors = $state.raw<(string | null)[]>([]);
  let ms = $state<number | null>(null);
  let listBusy = $state(false);

  // inspector
  let sel = $state.raw<any>(null);
  let selBusy = $state(false);
  let selErr = $state<string | null>(null);
  let graph = $state.raw<any>(null);
  let rels = $state.raw<{ out: Rel[]; in: Rel[]; more_out: boolean; more_in: boolean }>({ out: [], in: [], more_out: false, more_in: false });
  let trail = $state.raw<{ id: string; title: string }[]>([]);

  // install
  let task = $state.raw<TaskView | null>(null);

  async function load() {
    try {
      conn = await get<Conn>('api/library/network');
      if (conn.open && !ov) await overview();
    } catch (e) {
      error = errText(e);
    }
  }
  async function overview() {
    ov = await get('api/library/network/v1/overview');
    if (!listMode) browse(true);
  }
  async function open(mode: 'remote' | 'local', target: string) {
    error = null;
    busy = mode === 'remote' ? `Connecting to ${target}` : 'Opening the pack';
    try {
      const r = await post<Conn>('api/library/network/open', { mode, target });
      conn = r;
      ov = null;
      sel = null;
      graph = null;
      trail = [];
      list = [];
      listMode = null;
      q = kind = domain = dataset = '';
      await overview();
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
  async function openFolder() {
    const dir = await pickFolder('Choose an installed MPN pack (the folder that contains parts/ and manifest.json)');
    if (dir) await open('local', dir);
  }
  async function install() {
    const src = await pickFolder('Choose the downloaded pack (the folder that contains manifest.json and parts/*.zst)');
    if (!src) return;
    const dst = await pickFolder('Choose an empty folder to install it into (it needs the installed size given in the manifest)');
    if (!dst) return;
    error = null;
    try {
      const start = post<TaskView>('api/library/network/install', { source: src, dest: dst });
      task = await start;
      const t = await studio.follow(start, (v: TaskView) => (task = v));
      task = t;
      if (t.state === 'done') await open('local', dst);
      else error = t.error ?? 'The install did not finish.';
    } catch (e) {
      error = errText(e);
    }
  }
  async function closeNet() {
    await post('api/library/network/close');
    ov = null;
    sel = null;
    list = [];
    listMode = null;
    await load();
  }
  async function forget(r: Recent) {
    const out = await post<{ recent: Recent[] }>('api/library/network/forget', { mode: r.mode, target: r.target });
    if (conn) conn = { ...conn, recent: out.recent };
  }

  const params = (o: Record<string, any>) =>
    Object.entries(o)
      .filter(([, v]) => v !== '' && v !== null && v !== undefined && v !== false)
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join('&');

  async function search(p = 0) {
    if (!q.trim()) return browse(true);
    listBusy = true;
    try {
      const r = await get<any>(`api/library/network/v1/search?${params({ q: q.trim(), kind, domain, dataset, page: p, limit: 25 })}`);
      list = r.results;
      more = r.more;
      page = p;
      ms = r.client_ms ?? r.ms;
      listMode = 'search';
      if (r.mode === 'id' && r.results.length === 1) inspect(r.results[0].id);
    } catch (e) {
      error = errText(e);
    }
    listBusy = false;
  }
  async function browse(reset = false, back = false) {
    listBusy = true;
    try {
      let c: string | null = null;
      if (back) {
        cursors = cursors.slice(0, -1);
        c = cursors[cursors.length - 1] ?? null;
      } else if (!reset) {
        c = cursor;
        cursors = [...cursors, cursor];
      } else cursors = [];
      const r = await get<any>(`api/library/network/v1/browse?${params({ domain, kind, dataset, boundary, cursor: c, limit: 25 })}`);
      list = r.results;
      cursor = r.cursor;
      more = !!r.cursor;
      ms = r.client_ms ?? r.ms;
      listMode = 'browse';
    } catch (e) {
      error = errText(e);
    }
    listBusy = false;
  }
  function pickDomain(d: string) {
    domain = domain === d ? '' : d;
    q.trim() ? search(0) : browse(true);
  }
  function refilter() {
    q.trim() ? search(0) : browse(true);
  }

  async function inspect(id: string, push = true) {
    selBusy = true;
    selErr = null;
    try {
      const d = await get<any>(`api/library/network/v1/node/${encodeURIComponent(id)}`);
      sel = d;
      rels = { out: d.out, in: d.in, more_out: d.more_out, more_in: d.more_in };
      if (push) trail = [...trail.filter((t) => t.id !== d.node.id), { id: d.node.id, title: d.node.title }].slice(-8);
      graph = null;
      get<any>(`api/library/network/v1/node/${encodeURIComponent(d.node.id)}/graph?limit=40`)
        .then((g) => {
          if (sel && sel.node.id === g.center) graph = layout(g);
        })
        .catch(() => {});
    } catch (e) {
      selErr = errText(e);
    }
    selBusy = false;
  }
  async function moreRels(dir: 'out' | 'in') {
    const have = rels[dir];
    const r = await get<any>(`api/library/network/v1/node/${sel.node.id}/relationships?direction=${dir}&offset=${have.length}&limit=50`);
    rels = { ...rels, [dir]: [...have, ...r.results], [dir === 'out' ? 'more_out' : 'more_in']: r.more };
  }

  // radial layout: the node in the middle, neighbours on a ring, cross-domain links drawn dashed
  function layout(g: any) {
    const W = 420, H = 320, cx = W / 2, cy = H / 2, R = 112;
    const others = g.nodes.filter((n: any) => !n.center);
    const pos: Record<string, { x: number; y: number }> = { [g.center]: { x: cx, y: cy } };
    others.forEach((n: any, i: number) => {
      const a = (i / Math.max(1, others.length)) * Math.PI * 2 - Math.PI / 2;
      pos[n.id] = { x: cx + Math.cos(a) * R, y: cy + Math.sin(a) * R * 0.86 };
    });
    return { ...g, W, H, pos };
  }

  const datasetName = (v: string | number) => ov?.datasets?.find((d: any) => d.slug === v || d.id === v)?.name ?? (v == null || v === '' ? '' : `dataset ${v}`);
  const json = (v: unknown) => JSON.stringify(v ?? {}, null, 1);

  onMount(() => {
    load();
  });
</script>

<div class="ex">
  {#if !conn}
    <div class="pad"><p class="muted">{error ?? 'Reading'}</p></div>
  {:else if !conn.open}
    <div class="connect">
      <h1>Open MPN Network</h1>
      <p class="muted lead">Browse and search a whole MPN network, read only. Nothing is copied into a project and nothing in the network changes.</p>
      <div class="ways">
        <form class="way" onsubmit={(e) => (e.preventDefault(), url.trim() && open('remote', url.trim()))}>
          <h2>On the local network</h2>
          <p class="muted">The address of the MPN dashboard on the computer that holds the network.</p>
          <input class="field" bind:value={url} placeholder="http://mpn-host.local:8844" aria-label="Dashboard address" spellcheck="false" autocomplete="off" />
          <button class="btn primary" disabled={!url.trim() || !!busy}>Connect</button>
        </form>
        <div class="way">
          <h2>On this computer</h2>
          <p class="muted">An installed pack works offline. A downloaded pack is compressed: install it once, then open the installed folder.</p>
          <div class="row">
            <button class="btn primary" disabled={!!busy} onclick={openFolder}>Open pack folder...</button>
            <button class="btn" disabled={!!busy || task?.state === 'running'} onclick={install}>Install downloaded pack...</button>
          </div>
          {#if task}
            <p class="muted small">{task.title}: {task.state === 'running' ? `${task.progress.done} of ${task.progress.total} partitions · ${task.progress.label}` : task.state}</p>
          {/if}
        </div>
      </div>
      {#if busy}<p class="muted">{busy}...</p>{/if}
      {#if error}<p class="error-line">{error}</p>{/if}
      {#if conn.recent.length}
        <h2 class="recent-h">Recent</h2>
        <ul class="recent">
          {#each conn.recent as r (r.mode + r.target)}
            <li>
              <button class="link" disabled={!!busy} onclick={() => open(r.mode, r.target)}>
                <span>{r.name}</span>
                <span class="muted mono">{r.target}</span>
              </button>
              <button class="btn quiet" onclick={() => forget(r)} aria-label={`Remove ${r.name} from the list`}>Remove</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {:else}
    <aside class="side">
      <div class="net">
        <h1 title={conn.target ?? ''}>{conn.name}</h1>
        <p class="muted small">{conn.mode === 'remote' ? `Remote · ${conn.target}` : `This computer · ${conn.target}`}</p>
        {#if ov}
          <dl>
            <dt>Validated nodes</dt><dd class="num">{fmtInt(ov.validated_nodes)}</dd>
            <dt>Relationships</dt><dd class="num">{fmtInt(ov.relationships)}</dd>
            {#if ov.quarantined_nodes}<dt>Quarantined</dt><dd class="num">{fmtInt(ov.quarantined_nodes)}</dd>{/if}
          </dl>
          {#if ov.construction?.stopped}<p class="muted small">Construction is stopped; the network is preserved as it is.</p>{/if}
        {/if}
        <button class="btn quiet" onclick={closeNet}>Close network</button>
      </div>
      {#if ov}
        <h2>Domains</h2>
        <ul class="domains">
          {#each ov.domains as d (d.domain)}
            <li><button aria-pressed={domain === d.domain} onclick={() => pickDomain(d.domain)}><span>{human(d.domain)}</span><span class="num muted">{fmtInt(d.nodes)}</span></button></li>
          {/each}
        </ul>
      {/if}
    </aside>

    <section class="results">
      <form class="qbar" onsubmit={(e) => (e.preventDefault(), search(0))}>
        <input class="field grow" type="search" bind:value={q} placeholder="Search titles and aliases, or a node or Wikidata id (Q42)" aria-label="Search the network" />
        <button class="btn primary" disabled={listBusy}>Search</button>
      </form>
      {#if ov}
        <div class="filters">
          <select class="field" bind:value={kind} onchange={refilter} aria-label="Kind">
            <option value="">Every kind</option>
            {#each ov.kinds as k (k.kind)}<option value={k.kind}>{human(k.kind)} ({fmtInt(k.nodes)})</option>{/each}
          </select>
          <select class="field" bind:value={dataset} onchange={refilter} aria-label="Source">
            <option value="">Every source</option>
            {#each ov.datasets as d (d.slug)}<option value={d.slug}>{d.name} ({fmtInt(d.nodes)})</option>{/each}
          </select>
          {#if !q.trim()}
            <label class="lbl chk"><input type="checkbox" bind:checked={boundary} onchange={refilter} /> Only nodes linked to another domain</label>
          {/if}
          {#if domain}<button class="btn quiet" onclick={() => pickDomain(domain)}>{human(domain)} ×</button>{/if}
        </div>
      {/if}
      {#if error}<p class="error-line">{error}</p>{/if}
      <div class="list" aria-busy={listBusy}>
        {#if !list.length && !listBusy}
          <p class="muted pad">{listMode === 'search' ? `Nothing matches “${q}”.` : 'No nodes in this window. Try Next or another filter.'}</p>
        {/if}
        {#each list as n (n.id)}
          <button class="item" aria-current={sel?.node.id === n.id} onclick={() => inspect(n.id)}>
            <span class="t">{n.title}</span>
            <span class="m muted">{human(n.kind)} · {human(n.domain)} · {datasetName(n.dataset)}</span>
            {#if n.summary}<span class="s muted">{n.summary}</span>{/if}
          </button>
        {/each}
      </div>
      <div class="pager">
        {#if listMode === 'search'}
          <button class="btn" disabled={page === 0 || listBusy} onclick={() => search(page - 1)}>Previous</button>
          <span class="muted small">Page {page + 1}{ms != null ? ` · ${(ms / 1000).toFixed(2)} s` : ''}</span>
          <button class="btn" disabled={!more || listBusy} onclick={() => search(page + 1)}>Next</button>
        {:else if listMode === 'browse'}
          <button class="btn" disabled={!cursors.length || listBusy} onclick={() => browse(false, true)}>Previous</button>
          <span class="muted small">In storage order{ms != null ? ` · ${(ms / 1000).toFixed(2)} s` : ''}</span>
          <button class="btn" disabled={!more || listBusy} onclick={() => browse(false)}>Next</button>
        {/if}
      </div>
    </section>

    <section class="insp" aria-label="Node">
      {#if selErr}
        <p class="error-line">{selErr}</p>
      {:else if !sel}
        <p class="muted pad">Choose a node to see its cells, provenance and relationships.</p>
      {:else}
        {@const n = sel.node}
        {#if trail.length > 1}
          <nav class="trail" aria-label="Visited">
            {#each trail as t (t.id)}<button class="link" disabled={t.id === n.id} onclick={() => inspect(t.id, false)}>{t.title}</button>{/each}
          </nav>
        {/if}
        <h2 class="nt">{n.title}</h2>
        <p class="muted">{human(n.kind)} · {human(n.domain)}{n.subdomain ? ` / ${human(n.subdomain)}` : ''}{selBusy ? ' · loading' : ''}</p>
        {#if n.summary}<p>{n.summary}</p>{/if}
        {#if n.aliases?.length}<p class="muted small">Also: {n.aliases.slice(0, 12).join(', ')}</p>{/if}

        {#if graph && graph.nodes.length > 1}
          <svg class="graph" viewBox={`0 0 ${graph.W} ${graph.H}`} role="img" aria-label={`${n.title} and ${graph.nodes.length - 1} neighbours`}>
            {#each graph.edges as e, i (i)}
              {#if graph.pos[e.src] && graph.pos[e.dst]}
                <line x1={graph.pos[e.src].x} y1={graph.pos[e.src].y} x2={graph.pos[e.dst].x} y2={graph.pos[e.dst].y} class:cross={e.cross_domain}><title>{e.rel}</title></line>
              {/if}
            {/each}
            {#each graph.nodes as g (g.id)}
              {@const dx = graph.pos[g.id].x - graph.W / 2}
              <g class="gn" class:center={g.center} transform={`translate(${graph.pos[g.id].x},${graph.pos[g.id].y})`} role="button" tabindex="0"
                onclick={() => !g.center && inspect(g.id)} onkeydown={(e) => e.key === 'Enter' && !g.center && inspect(g.id)}>
                <circle r={g.center ? 6 : 4} class:other={g.domain !== n.domain} />
                {#if g.center}
                  <text y={-11} text-anchor="middle">{(g.title ?? '').slice(0, 28)}</text>
                {:else}
                  <text x={dx < -4 ? -7 : dx > 4 ? 7 : 0} y={Math.abs(dx) <= 4 ? (graph.pos[g.id].y < graph.H / 2 ? -8 : 14) : 3}
                    text-anchor={dx < -4 ? 'end' : dx > 4 ? 'start' : 'middle'}>{(g.title ?? '').length > 18 ? (g.title ?? '').slice(0, 17) + '…' : g.title}</text>
                {/if}
                <title>{g.title} · {human(g.domain)}</title>
              </g>
            {/each}
          </svg>
          <p class="muted small">Neighbourhood: {graph.nodes.length - 1} nodes. Hollow circles and dashed lines lie in another domain.</p>
        {/if}

        <h3>Provenance</h3>
        <dl class="kv">
          <dt>Source</dt><dd>{n.provenance?.dataset_name ?? datasetName(n.dataset)}</dd>
          {#if n.provenance?.record}<dt>Record</dt><dd class="mono">{n.provenance.record}</dd>{/if}
          {#if n.provenance?.url}<dt>Link</dt><dd><a href={n.provenance.url}>{n.provenance.url}</a></dd>{/if}
          <dt>Licence</dt><dd>{n.licence ?? n.provenance?.dataset_licence ?? 'not recorded'}</dd>
          {#if n.provenance?.attribution}<dt>Attribution</dt><dd>{n.provenance.attribution}</dd>{/if}
          <dt>Status</dt><dd>{n.validation?.status}{n.validation?.confidence != null ? `, confidence ${Number(n.validation.confidence).toFixed(2)}` : ''}</dd>
          {#if n.validation?.method}<dt>Method</dt><dd>{n.validation.method}</dd>{/if}
          <dt>Id</dt><dd class="mono">{n.id}</dd>
          {#if n.key}<dt>Key</dt><dd class="mono">{n.key}</dd>{/if}
        </dl>

        {#if sel.cross_domain.length}<p class="small">Links into {sel.cross_domain.map(human).join(', ')}.</p>{/if}

        <h3>Relationships</h3>
        {#each [['out', 'From this node'], ['in', 'To this node']] as [dir, label] (dir)}
          {@const rs = rels[dir as 'out' | 'in']}
          <h4>{label} ({fmtInt(rs.length)}{(dir === 'out' ? rels.more_out : rels.more_in) ? '+' : ''})</h4>
          {#if rs.length}
            <ul class="rels">
              {#each rs as r, i (i)}
                <li>
                  <span class="muted">{r.rel}</span>
                  {#if r.present}<button class="link" onclick={() => inspect(r.node)}>{r.title}</button>{:else}<span class="muted" title={r.node}>not in this network</span>{/if}
                  {#if r.cross_domain}<span class="muted small">{human(r.domain ?? '')}</span>{/if}
                </li>
              {/each}
            </ul>
            {#if dir === 'out' ? rels.more_out : rels.more_in}<button class="btn quiet" onclick={() => moreRels(dir as 'out' | 'in')}>Load more</button>{/if}
          {:else}
            <p class="muted small">None.</p>
          {/if}
        {/each}

        {#if sel.claims.length}
          <h3>Claims ({fmtInt(sel.claims.length)}{sel.more_claims ? '+' : ''})</h3>
          <ul class="rels">
            {#each sel.claims as c (c.id)}
              <li><button class="link" onclick={() => inspect(c.id)}>{c.property}</button> <span>{c.value}</span>{#if c.basis}<span class="muted small"> {c.basis}</span>{/if}</li>
            {/each}
          </ul>
        {/if}

        <h3>KTR cells</h3>
        {#each ['knowledge', 'thinking', 'reasoning'] as c (c)}
          <details>
            <summary>{c[0].toUpperCase() + c.slice(1)}</summary>
            <pre>{json(n.cells?.[c])}</pre>
          </details>
        {/each}
      {/if}
    </section>
  {/if}
</div>

<style>
  .ex {
    position: absolute;
    inset: 0;
    display: grid;
    grid-template-columns: 240px minmax(0, 1fr) minmax(320px, 440px);
    min-height: 0;
  }
  .pad {
    padding: 16px;
  }
  .connect {
    grid-column: 1 / -1;
    overflow: auto;
    padding: 32px max(24px, calc(50% - 440px));
  }
  .connect h1 {
    font-size: 22px;
    margin: 0 0 6px;
  }
  .lead {
    margin: 0 0 24px;
    max-width: 64ch;
  }
  .ways {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  .way {
    display: grid;
    gap: 10px;
    align-content: start;
    border: 1px solid var(--line);
    padding: 16px;
    background: var(--surface);
  }
  .way h2,
  .recent-h {
    font-size: 15px;
    margin: 0;
  }
  .way p {
    margin: 0;
  }
  .way .btn {
    justify-self: start;
  }
  .recent-h {
    margin: 28px 0 8px;
  }
  .recent {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 2px;
  }
  .recent li {
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--line-soft);
  }
  .recent .link {
    flex: 1;
    display: grid;
    gap: 2px;
    text-align: left;
    padding: 8px 0;
    min-width: 0;
  }
  .recent .link span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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
  .link:hover:not(:disabled) {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .link:disabled {
    color: var(--muted);
    cursor: default;
  }
  .small {
    font-size: 12px;
  }

  .side {
    border-right: 1px solid var(--line);
    overflow: auto;
    padding: 14px 0 20px;
    min-height: 0;
  }
  .net {
    padding: 0 14px 14px;
    border-bottom: 1px solid var(--line);
    display: grid;
    gap: 6px;
  }
  .net h1 {
    font-size: 15px;
    margin: 0;
    overflow-wrap: anywhere;
  }
  .net p {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .net dl {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 2px 8px;
    margin: 6px 0;
  }
  .net dt {
    color: var(--muted);
  }
  .net dd {
    margin: 0;
  }
  .net .btn {
    justify-self: start;
  }
  .side h2 {
    font-size: 13px;
    margin: 14px 14px 6px;
  }
  .domains {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .domains button {
    width: 100%;
    display: flex;
    justify-content: space-between;
    gap: 8px;
    padding: 5px 14px;
    background: none;
    border: 0;
    color: var(--white);
    cursor: pointer;
    font: inherit;
    text-align: left;
  }
  .domains button:hover {
    background: var(--raise);
  }
  .domains button[aria-pressed='true'] {
    background: var(--raise);
    box-shadow: inset 2px 0 0 var(--white);
  }

  .results {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
    border-right: 1px solid var(--line);
  }
  .qbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 14px 8px;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 0 14px 10px;
    align-items: center;
    border-bottom: 1px solid var(--line);
  }
  .qbar .field {
    height: var(--ctl);
    min-width: 0;
  }
  .filters select {
    max-width: 240px;
  }
  .chk {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .list {
    flex: 1;
    overflow: auto;
    min-height: 0;
  }
  .item {
    display: grid;
    gap: 2px;
    width: 100%;
    text-align: left;
    padding: 9px 14px;
    background: none;
    border: 0;
    border-bottom: 1px solid var(--line-soft);
    color: var(--white);
    cursor: pointer;
    font: inherit;
  }
  .item:hover {
    background: var(--surface);
  }
  .item[aria-current='true'] {
    background: var(--raise);
    box-shadow: inset 2px 0 0 var(--white);
  }
  .item .t {
    font-weight: 500;
    overflow-wrap: anywhere;
  }
  .item .m {
    font-size: 12px;
  }
  .item .s {
    font-size: 12px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .pager {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border-top: 1px solid var(--line);
  }

  .insp {
    overflow: auto;
    padding: 14px 16px 28px;
    min-height: 0;
    min-width: 0;
  }
  .insp p {
    margin: 4px 0 8px;
    overflow-wrap: anywhere;
  }
  .nt {
    font-size: 18px;
    margin: 0 0 2px;
    overflow-wrap: anywhere;
  }
  .insp h3 {
    font-size: 13px;
    margin: 18px 0 6px;
  }
  .insp h4 {
    font-size: 12px;
    color: var(--muted);
    font-weight: 500;
    margin: 10px 0 4px;
  }
  .trail {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    font-size: 12px;
    margin-bottom: 10px;
  }
  .kv {
    display: grid;
    grid-template-columns: 90px minmax(0, 1fr);
    gap: 3px 10px;
    margin: 0;
  }
  .kv dt {
    color: var(--muted);
  }
  .kv dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .rels {
    list-style: none;
    margin: 0 0 6px;
    padding: 0;
    display: grid;
    gap: 3px;
  }
  .rels li {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 8px;
    overflow-wrap: anywhere;
  }
  details {
    border-top: 1px solid var(--line-soft);
    padding: 6px 0;
  }
  summary {
    cursor: pointer;
  }
  pre {
    font: 11px/16px var(--mono);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: var(--muted);
    margin: 6px 0 0;
    max-height: 320px;
    overflow: auto;
  }
  .graph {
    width: 100%;
    height: auto;
    display: block;
    margin: 10px 0 2px;
    background: var(--surface);
    border: 1px solid var(--line);
  }
  .graph line {
    stroke: #5a5a5a;
    stroke-width: 1;
  }
  .graph line.cross {
    stroke-dasharray: 3 3;
    stroke: var(--muted);
  }
  .gn {
    cursor: pointer;
  }
  .gn.center {
    cursor: default;
  }
  .gn circle {
    fill: var(--white);
    stroke: var(--white);
  }
  .gn circle.other {
    fill: var(--surface);
  }
  .gn text {
    fill: var(--muted);
    font: 9px var(--sans);
  }
  .gn.center text {
    fill: var(--white);
    font-size: 11px;
    font-weight: 500;
  }
  .gn:hover text,
  .gn:focus-visible text {
    fill: var(--white);
  }

  @media (max-width: 1100px) {
    .ex {
      grid-template-columns: 200px minmax(0, 1fr) minmax(300px, 380px);
    }
    .net dl {
      grid-template-columns: minmax(0, 1fr);
      gap: 0;
    }
    .net dd {
      margin-bottom: 4px;
    }
  }
  @media (max-width: 860px) {
    .ex {
      position: static;
      display: block;
    }
    .side,
    .results {
      border-right: 0;
      border-bottom: 1px solid var(--line);
    }
    .domains {
      max-height: 220px;
      overflow: auto;
    }
    .list {
      max-height: 60vh;
    }
    .ways {
      grid-template-columns: 1fr;
    }
  }
</style>
