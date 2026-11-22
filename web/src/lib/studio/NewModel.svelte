<script lang="ts">
  import { errText, fmtBytes, fmtInt, post } from '../api';
  import { studio, fmtParams, pct, type TaskView } from './studio.svelte';
  import CoreServerDialog from './CoreServerDialog.svelte';

  let { first = false, core: presetCore, oncancel }: { first?: boolean; core?: string; oncancel?: () => void } = $props();

  let name = $state('');
  let coreId = $state<string>(presetCore ?? studio.data?.core_servers[0]?.id_ref ?? studio.cores[0]?.id ?? '');
  let netId = $state<string>(studio.data?.networks.find((n) => !n.damaged)?.id ?? '');
  let error = $state<string | null>(null);
  let busy = $state<string | null>(null);
  let newNet = $state<string | null>(null);
  let addServer = $state(false);

  const core = $derived(studio.core(coreId));
  const server = $derived(studio.data?.core_servers.find((c) => c.id_ref === coreId) ?? null);
  const net = $derived(studio.network(netId));
  const canCreate = $derived(!!name.trim() && !!coreId && !!netId && !busy);

  const show = (t: TaskView) => (busy = `${t.title}${t.progress.label ? `: ${t.progress.label}` : ''}${pct(t) != null ? ` ${pct(t)}%` : ''}`);

  async function importCore(kind: 'gguf' | 'hf') {
    error = null;
    try {
      const c = kind === 'gguf' ? await studio.importGguf(show) : await studio.importHf(show);
      if (c && c.kind === 'core') coreId = c.id;
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
  async function importNet() {
    error = null;
    busy = 'Importing the network';
    try {
      const n = await studio.importNetwork('auto');
      if (n) netId = n.id;
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
  async function createNet() {
    if (!newNet?.trim()) return;
    error = null;
    try {
      const n = await post<{ id: string }>('api/library/networks', { name: newNet.trim() });
      await studio.load();
      netId = n.id;
      newNet = null;
    } catch (e) {
      error = errText(e);
    }
  }
  async function create() {
    error = null;
    busy = 'Creating the pairing';
    try {
      const m = await studio.createModel({ name: name.trim(), core_id: coreId, network_id: netId });
      studio.model = m.id;
      oncancel?.();
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
</script>

<form class="compose" onsubmit={(e) => (e.preventDefault(), canCreate && create())}>
  <h1>{first ? 'Pair a core with a network' : 'New pairing'}</h1>
  {#if first}
    <p class="muted lead">A pairing joins a cognitive core, a language model used unchanged, to an MPN network that it searches before it answers. Either one can be changed later without touching the other.</p>
  {/if}

  <section>
    <label class="lbl" for="nm-core">Cognitive core</label>
    {#if studio.cores.length || studio.data?.core_servers.length}
      <select id="nm-core" class="field" bind:value={coreId}>
        {#if studio.data?.core_servers.length}
          <optgroup label="On a server">
            {#each studio.data.core_servers as s (s.id)}<option value={s.id_ref}>{s.name} ({s.base_url.replace(/^https?:\/\//, '').replace(/\/v1$/, '')})</option>{/each}
          </optgroup>
        {/if}
        {#if studio.cores.length}
          <optgroup label="Served by Studio">
            {#each studio.cores as c (c.id)}
              <option value={c.id} disabled={!c.present || !c.compatibility.runnable}>{c.name} ({fmtParams(c.info.parameters)}, {c.info.quantization}, {fmtBytes(c.info.bytes)})</option>
            {/each}
          </optgroup>
        {/if}
      </select>
      {#if server}<p class="muted small">{server.model_id} at {server.base_url}{server.last_test ? (server.last_test.ok ? `, last test answered in ${server.last_test.latency_ms} ms` : `, last test failed: ${server.last_test.message}`) : ', not tested yet'}</p>{/if}
      {#if core}<p class="muted small">{core.info.architecture}{core.info.license ? `, licence ${core.info.license}` : ''}{core.projector ? ', with a vision projector' : ''}</p>{/if}
    {:else}
      <p class="muted">No core yet.</p>
    {/if}
    <div class="row acts">
      <button type="button" class="btn" disabled={!!busy} onclick={() => (addServer = true)}>Add a core on a server</button>
      <button type="button" class="btn" disabled={!!busy} onclick={() => importCore('gguf')}>Import GGUF file</button>
      <button type="button" class="btn" disabled={!!busy} onclick={() => importCore('hf')}>Convert Hugging Face folder</button>
    </div>
  </section>

  <section>
    <label class="lbl" for="nm-net">MPN network</label>
    {#if studio.data?.networks.length}
      <select id="nm-net" class="field" bind:value={netId}>
        {#each studio.data.networks.filter((n) => !n.damaged) as n (n.id)}
          <option value={n.id}>{n.name} ({fmtInt(n.nodes ?? 0)} nodes{n.router ? ', learned router' : ''})</option>
        {/each}
      </select>
      {#if net?.router}<p class="muted small">This network carries a learned router trained for {net.router.encoder?.name ?? 'its encoder'}.</p>{/if}
    {:else}
      <p class="muted">No network yet.</p>
    {/if}
    {#if newNet !== null}
      <div class="row acts">
        <input class="field" placeholder="Network name" aria-label="New network name" bind:value={newNet} />
        <button type="button" class="btn" disabled={!newNet.trim()} onclick={createNet}>Create</button>
        <button type="button" class="btn quiet" onclick={() => (newNet = null)}>Cancel</button>
      </div>
    {:else}
      <div class="row acts">
        <button type="button" class="btn" disabled={!!busy} onclick={() => (newNet = '')}>New empty network</button>
        <button type="button" class="btn" disabled={!!busy} onclick={importNet}>Import network folder</button>
      </div>
    {/if}
  </section>

  <section>
    <label class="lbl" for="nm-name">Pairing name</label>
    <input id="nm-name" class="field" bind:value={name} placeholder="For example, Field Guide Assistant" />
  </section>

  {#if busy}<p class="muted">{busy}</p>{/if}
  {#if error}<p class="error-line">{error}</p>{/if}

  <div class="row end">
    {#if oncancel}<button type="button" class="btn" onclick={oncancel}>Cancel</button>{/if}
    <button type="submit" class="btn primary" disabled={!canCreate}>Create pairing</button>
  </div>
</form>

{#if addServer}
  <CoreServerDialog onclose={() => (addServer = false)} ondone={(s) => ((coreId = s.id_ref), (addServer = false))} />
{/if}

<style>
  .compose {
    width: min(620px, 100%);
    margin: 4vh auto 0;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  .lead {
    font-size: 14px;
    line-height: 20px;
    margin-top: -10px;
  }
  section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .acts {
    flex-wrap: wrap;
    gap: 8px;
  }
  .acts .field {
    max-width: 280px;
  }
  .end {
    justify-content: flex-end;
  }
</style>
