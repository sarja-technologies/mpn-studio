<script lang="ts">
  import { untrack } from 'svelte';
  import { app } from '../state.svelte';
  import { get, post, del, errText, ApiError, plural, human, type SchemaEntry } from '../api';
  import Modal from './Modal.svelte';
  import { studio, type TaskView } from '../studio/studio.svelte';

  const d = $derived(app.dialog);
  const close = () => (app.dialog = null);

  // ---------------------------------------------------------------- new node
  let nn = $state({ title: '', type: 'knowledge', schema: '3cn@1', statement: '', mpns: [''] as string[] });
  let schemas = $state.raw<SchemaEntry[]>([]);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let problems = $state<string[]>([]);

  function regionAt(x: number | null, y: number | null): string {
    const ms = app.board?.mpns ?? [];
    if (x != null && y != null) {
      const hit = ms.filter((m) => Math.hypot(m.x - x, m.y - y) <= m.r).sort((a, b) => a.r - b.r)[0];
      if (hit) return hit.id;
    }
    return '';
  }
  $effect(() => {
    const d = app.dialog;
    untrack(() => openNewNode(d));
  });
  function openNewNode(d: typeof app.dialog) {
    if (d?.kind === 'newNode') {
      nn = { title: '', type: 'knowledge', schema: nn.schema, statement: '', mpns: [regionAt(d.x, d.y)] };
      error = null;
      problems = [];
      get<{ schemas: SchemaEntry[] }>('api/schemas')
        .then((r) => {
          schemas = r.schemas;
          if (!r.schemas.some((s) => `${s.id}@${s.version}` === nn.schema) && r.schemas[0]) nn.schema = `${r.schemas[0].id}@${r.schemas[0].version}`;
        })
        .catch(() => {});
    }
  }
  async function createNode() {
    if (d?.kind !== 'newNode') return;
    const [sid, sv] = nn.schema.split('@');
    const mems = [...new Set(nn.mpns.filter(Boolean))].map((mpn_id) => ({ mpn_id }));
    busy = true;
    error = null;
    problems = [];
    try {
      const body: any = {
        title: nn.title.trim(),
        type: nn.type,
        schema: { id: sid, version: Number(sv) },
        cells: { knowledge: { statements: nn.statement.trim() ? [{ text: nn.statement.trim() }] : [] }, thinking: {}, reasoning: {} },
        memberships: mems,
      };
      if (d.x != null && d.y != null) body.position = { x: d.x, y: d.y };
      const r = await post('api/nodes', body);
      close();
      await app.afterEdit(r);
      await app.goToNode(r.id, r.node?.position);
      const fx = r.effects;
      app.toast(`Created “${r.node.title}”. Live now: ${fx.live_now.join(', ')}. After indexing: ${fx.after_indexing.join(', ')}.`);
    } catch (e) {
      error = errText(e);
      problems = e instanceof ApiError ? (e.detail?.problems ?? []) : [];
    }
    busy = false;
  }

  // ---------------------------------------------------------------- new MPN
  let nm = $state({ name: '', description: '' });
  $effect(() => {
    const d = app.dialog;
    untrack(() => {
      if (d?.kind === 'newMpn') {
        nm = { name: '', description: '' };
        error = null;
      }
    });
  });
  async function createMpn() {
    busy = true;
    error = null;
    try {
      const r = await post('api/mpns', { name: nm.name.trim(), description: nm.description.trim() });
      close();
      await app.afterEdit(r);
      app.boardApi?.fitRegion(r.mpn.id);
      app.toast(`Created the MPN “${r.mpn.name}”. It is empty: add nodes to it from a node's memberships or with New node.`);
    } catch (e) {
      error = errText(e);
    }
    busy = false;
  }

  // ---------------------------------------------------------------- delete (preview first, recoverable)
  interface Preview {
    node: { id: string; title: string };
    memberships: { mpn_name: string; role: string }[];
    edges: number;
    learned_rows: any[];
    checkpoint: string | null;
    note: string;
  }
  // One node: the detailed preview. Several (any number, or every node matching a search): one bulk preview, one revision.
  interface BulkPreview {
    count: number;
    live: number;
    relations: number;
    by_mpn: { mpn: string; nodes: number }[];
    sample: string[];
    note: string;
  }
  let previews = $state.raw<Preview[] | null>(null);
  let bulk = $state.raw<BulkPreview | null>(null);
  let task = $state.raw<TaskView | null>(null);
  const target = (d: { ids: string[]; filter?: Record<string, unknown> }) => (d.filter ? { filter: d.filter } : { ids: d.ids });
  $effect(() => {
    const d = app.dialog;
    untrack(() => {
      if (d?.kind !== 'delete') return;
      previews = null;
      bulk = null;
      task = null;
      error = null;
      if (!d.filter && d.ids.length === 1)
        get<Preview>(`api/nodes/${d.ids[0]}/delete-preview`)
          .then((p) => (previews = [p]))
          .catch((e) => (error = errText(e)));
      else
        post<BulkPreview>('api/nodes/bulk-preview', target(d))
          .then((p) => (bulk = p))
          .catch((e) => (error = errText(e)));
    });
  });
  async function doDelete() {
    if (d?.kind !== 'delete') return;
    busy = true;
    error = null;
    if (previews) {
      const ids = d.ids;
      try {
        const last = await del(`api/nodes/${ids[0]}`);
        close();
        app.selection = [];
        await app.afterEdit(last);
        app.toast(`Deleted “${previews[0].node.title}”. Deleted nodes can be restored.`, 'info', { label: 'Restore', run: () => restore(ids) });
      } catch (e) {
        error = errText(e);
      }
      busy = false;
      return;
    }
    const t = await studio.follow(post<TaskView>('api/nodes/bulk-delete', target(d)), (t) => (task = t)).catch((e) => ((error = errText(e)), null));
    busy = false;
    if (!t) return;
    if (t.state !== 'done') {
      error = t.error ?? 'The deletion did not finish. Nothing was changed.';
      task = null;
      return;
    }
    const r = t.result;
    close();
    app.selection = [];
    await app.afterEdit({ rev: r.rev });
    app.toast(`Deleted ${r.deleted.toLocaleString()} nodes in revision ${r.rev}.`, 'info', { label: 'Undo', run: () => undoBulk(r.rev) });
  }
  async function undoBulk(rev: number) {
    try {
      const r = await post<{ rev: number }>('api/nodes/bulk-restore', { rev });
      await app.afterEdit({ rev: r.rev });
      app.toast(`Restored. Now at revision ${r.rev}.`);
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }
  async function restore(ids: string[]) {
    try {
      let last: any;
      for (const id of ids) last = await post(`api/nodes/${id}/restore`);
      await app.afterEdit(last);
      app.select(ids, ids.length === 1);
      app.toast(`Restored ${ids.length === 1 ? 'the node' : ids.length + ' nodes'}.`);
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }

  // ---------------------------------------------------------------- connect
  let types = $state.raw<string[]>([]);
  let rel = $state('related_to');
  let flip = $state(false);
  $effect(() => {
    const d = app.dialog;
    untrack(() => {
      if (d?.kind !== 'connect') return;
      error = null;
      flip = false;
      get<{ types: string[] }>('api/edge-types')
        .then((r) => {
          types = r.types;
          if (!r.types.includes(rel)) rel = r.types[0] ?? 'related_to';
        })
        .catch((e) => (error = errText(e)));
    });
  });
  async function connect() {
    if (d?.kind !== 'connect') return;
    busy = true;
    error = null;
    try {
      const r = await post('api/edges', flip ? { src: d.dst, dst: d.src, type: rel } : { src: d.src, dst: d.dst, type: rel });
      close();
      app.tool = 'pan';
      await app.afterEdit(r);
      app.toast(`Connected with “${human(rel)}”. This is a model edit (revision ${r.rev}).`);
    } catch (e) {
      error = errText(e);
    }
    busy = false;
  }
</script>

{#if d?.kind === 'newNode'}
  <Modal title="New node" onclose={close} width={460}>
    <form onsubmit={(e) => (e.preventDefault(), createNode())}>
      <div>
        <label class="lbl" for="nn-title">Title</label>
        <!-- svelte-ignore a11y_autofocus -->
        <input id="nn-title" class="field" bind:value={nn.title} autofocus required maxlength="200" />
      </div>
      <div class="two">
        <div>
          <label class="lbl" for="nn-type">Type</label>
          <select id="nn-type" class="field" bind:value={nn.type}>
            <option value="knowledge">Knowledge</option>
            <option value="procedure">Procedure</option>
          </select>
        </div>
        <div>
          <label class="lbl" for="nn-schema">Schema</label>
          <select id="nn-schema" class="field" bind:value={nn.schema}>
            {#each schemas as s (s.id + s.version)}<option value="{s.id}@{s.version}">{s.name} v{s.version}</option>{/each}
            {#if !schemas.length}<option value={nn.schema}>{nn.schema.replace('@', ' v')}</option>{/if}
          </select>
        </div>
      </div>
      <div>
        <label class="lbl" for="nn-st">First statement</label>
        <textarea id="nn-st" class="field" rows="2" bind:value={nn.statement} placeholder="One sentence this node states"></textarea>
      </div>
      <fieldset>
        <legend class="lbl">Member of</legend>
        {#each nn.mpns as _, i (i)}
          <div class="row">
            <select class="field" aria-label="MPN {i + 1}" bind:value={nn.mpns[i]} required={i === 0}>
              <option value="" disabled>Choose an MPN</option>
              {#each app.mpns as m (m.id)}<option value={m.id}>{m.name}{i === 0 ? '' : ' (shared)'}</option>{/each}
            </select>
            {#if nn.mpns.length > 1}<button type="button" class="btn" onclick={() => (nn.mpns = nn.mpns.filter((_, j) => j !== i))}>Remove</button>{/if}
          </div>
        {/each}
        <button type="button" class="btn add" onclick={() => (nn.mpns = [...nn.mpns, ''])}>Add another MPN</button>
      </fieldset>
      {#if error}<p class="error-line">{error}</p>{/if}
      {#each problems as p}<p class="problem">{p}</p>{/each}
      <div class="actions">
        <button type="button" class="btn" onclick={close}>Cancel</button>
        <button type="submit" class="btn primary" disabled={busy || !nn.title.trim() || !nn.mpns[0]}>Create node</button>
      </div>
    </form>
  </Modal>
{:else if d?.kind === 'newMpn'}
  <Modal title="New MPN" onclose={close} width={420}>
    <form onsubmit={(e) => (e.preventDefault(), createMpn())}>
      <div>
        <label class="lbl" for="nm-name">Name</label>
        <!-- svelte-ignore a11y_autofocus -->
        <input id="nm-name" class="field" bind:value={nm.name} autofocus required maxlength="120" />
      </div>
      <div>
        <label class="lbl" for="nm-desc">Description</label>
        <textarea id="nm-desc" class="field" rows="2" bind:value={nm.description}></textarea>
      </div>
      {#if error}<p class="error-line">{error}</p>{/if}
      <div class="actions">
        <button type="button" class="btn" onclick={close}>Cancel</button>
        <button type="submit" class="btn primary" disabled={busy || !nm.name.trim()}>Create MPN</button>
      </div>
    </form>
  </Modal>
{:else if d?.kind === 'delete'}
  {@const n = bulk?.count ?? d.ids.length}
  <Modal title={d.label ? `Delete ${d.label}?` : n === 1 ? 'Delete this node?' : `Delete ${n.toLocaleString()} nodes?`} onclose={() => !task && close()} width={480}>
    {#if error && !previews && !bulk}
      <p class="error-line">{error}</p>
      <div class="actions"><button class="btn" onclick={close}>Close</button></div>
    {:else if previews}
      {@const p = previews[0]}
      <div class="what">
        <p class="strong">{p.node.title}</p>
        <p>Member of {p.memberships.map((m) => `${m.mpn_name} (${m.role})`).join(', ')}.</p>
        <p>{plural(p.edges, 'edge')} to other nodes and {plural(p.learned_rows.length, 'learned row')} are affected.</p>
        <p class="muted">{p.note}</p>
      </div>
      {#if error}<p class="error-line">{error}</p>{/if}
      <div class="actions">
        <button class="btn" onclick={close}>Cancel</button>
        <button class="btn danger" disabled={busy} onclick={doDelete}>Delete node</button>
      </div>
    {:else if !bulk}
      <p class="muted">Reading what the deletion affects</p>
    {:else if bulk.count === 0}
      <p>No node matches.</p>
      <div class="actions"><button class="btn" onclick={close}>Close</button></div>
    {:else}
      <div class="what">
        <p class="strong">{bulk.sample.slice(0, 4).join(', ')}{bulk.count > 4 ? ` and ${(bulk.count - Math.min(4, bulk.sample.length)).toLocaleString()} more` : ''}</p>
        {#if bulk.by_mpn.length}
          <p>{bulk.by_mpn.slice(0, 5).map((m) => `${m.nodes.toLocaleString()} in ${m.mpn}`).join(', ')}{bulk.by_mpn.length > 5 ? ', and others' : ''}.</p>
        {/if}
        <p>{plural(bulk.relations, 'relation')} to or from these nodes will be closed.</p>
        <p class="muted">{bulk.note}</p>
      </div>
      {#if task?.state === 'running'}
        <div class="bar"><i style="width:{task.progress.total ? Math.round((task.progress.done / task.progress.total) * 100) : 0}%"></i></div>
        <p class="muted num">{task.progress.done.toLocaleString()} of {task.progress.total.toLocaleString()}</p>
      {/if}
      {#if error}<p class="error-line">{error}</p>{/if}
      <div class="actions">
        {#if task?.state === 'running'}
          <button class="btn" onclick={() => studio.cancel(task!.id)}>Cancel</button>
        {:else}
          <button class="btn" onclick={close}>Cancel</button>
          <button class="btn danger" disabled={busy} onclick={doDelete}>Delete {bulk.count.toLocaleString()} {bulk.count === 1 ? 'node' : 'nodes'}</button>
        {/if}
      </div>
    {/if}
  </Modal>
{:else if d?.kind === 'connect'}
  <Modal title="Connect nodes" onclose={close} width={420}>
    <form onsubmit={(e) => (e.preventDefault(), connect())}>
      <div class="pair">
        <span class="ellipsis strong">{flip ? d.dstTitle : d.srcTitle}</span>
        <select class="field mono" aria-label="Relation type" bind:value={rel}>
          {#each types as t (t)}<option value={t}>{t}</option>{/each}
          {#if !types.length}<option value={rel}>{rel}</option>{/if}
        </select>
        <span class="ellipsis strong">{flip ? d.srcTitle : d.dstTitle}</span>
      </div>
      <p class="muted">This adds an edge to the model and creates a revision.</p>
      {#if error}<p class="error-line">{error}</p>{/if}
      <div class="actions">
        <button type="button" class="btn left" onclick={() => (flip = !flip)}>Swap direction</button>
        <button type="button" class="btn" onclick={close}>Cancel</button>
        <button type="submit" class="btn primary" disabled={busy}>Connect</button>
      </div>
    </form>
  </Modal>
{/if}

<style>
  form {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .two {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  fieldset {
    border: 0;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  legend {
    padding: 0;
  }
  .add {
    align-self: flex-start;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 2px;
  }
  .actions .left {
    margin-right: auto;
  }
  .what {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .strong {
    font-weight: 500;
  }
  .bar {
    height: 4px;
    background: var(--line-soft);
    border-radius: 2px;
    overflow: hidden;
  }
  .bar > i {
    display: block;
    height: 100%;
    background: var(--white);
  }
  .pair {
    display: grid;
    grid-template-columns: 1fr;
    gap: 6px;
  }
</style>
