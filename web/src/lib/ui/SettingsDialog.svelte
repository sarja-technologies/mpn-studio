<script lang="ts">
  import { onDestroy } from 'svelte';
  import { desk, errText, get, put, post, pickFolder, pickFile, openInFileManager } from '../desktop.svelte';
  import { backupNow, restoreBackup } from '../desktop-actions';
  import { fmtBytes, fmtInt } from '../api';
  import { app } from '../state.svelte';
  import Modal from './Modal.svelte';

  const tab = $derived(desk.settings);
  let settings = $state.raw<any>(null);
  let storage = $state.raw<any>(null);
  let compute = $state.raw<any>(null);
  let backups = $state.raw<any[]>([]);
  let remoteJobs = $state.raw<any>(null);
  let error = $state<string | null>(null);
  let busy = $state<string | null>(null);
  let ssh = $state({ ssh_host: '', ssh_config: '', api_port: 8840, runtime_port: 8841 });

  async function load() {
    try {
      settings = await get('api/desktop/settings');
      ssh = { ...ssh, ...settings.compute.remote };
      if (tab === 'storage') storage = await get('api/desktop/storage');
      if (tab === 'compute') compute = await get('api/desktop/compute');
      if (tab === 'backups') backups = (await get<{ backups: any[] }>('api/desktop/backups')).backups;
      error = null;
    } catch (e) {
      error = errText(e);
    }
  }
  $effect(() => {
    tab;
    load();
  });
  let timer = setInterval(() => {
    if (tab !== 'compute') return;
    if (compute?.local?.runtime) get('api/desktop/runtime').then((l) => (compute = { ...compute, local: l })).catch(() => {});
    else if (compute?.remote?.connected) get('api/desktop/compute').then((c) => (compute = c)).catch(() => {});
  }, 4000);
  onDestroy(() => clearInterval(timer));

  async function save(patch: any) {
    try {
      settings = await put('api/desktop/settings', patch);
      error = null;
      await load();
    } catch (e) {
      error = errText(e);
    }
  }
  async function choose(key: 'models_dir' | 'checkpoints_dir' | 'datasets_dir', title: string) {
    const p = await pickFolder(title);
    if (p) await save({ storage: { [key]: p } });
  }
  async function choosePython() {
    const p = await pickFile('Choose the Python interpreter that has torch and the Yaob runtime');
    if (p) await save({ compute: { python: p } });
  }
  async function startRuntime() {
    busy = 'Starting...';
    error = null;
    try {
      await post('api/desktop/runtime/start');
    } catch (e) {
      error = errText(e);
    }
    compute = await get('api/desktop/compute').catch(() => compute);
    busy = null;
  }
  async function stopRuntime() {
    busy = 'Stopping...';
    await post('api/desktop/runtime/stop').catch((e) => (error = errText(e)));
    compute = await get('api/desktop/compute').catch(() => compute);
    busy = null;
  }
  async function connect() {
    busy = 'Connecting...';
    error = null;
    try {
      await save({ compute: { mode: 'remote', remote: { ...ssh, api_port: Number(ssh.api_port), runtime_port: Number(ssh.runtime_port) } } });
      await post('api/desktop/remote/connect', {});
      remoteJobs = null;
      compute = await get('api/desktop/compute');
      loadJobs();
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
  async function disconnect() {
    await post('api/desktop/remote/disconnect').catch(() => {});
    await save({ compute: { mode: 'local' } });
    remoteJobs = null;
  }
  async function loadJobs() {
    try {
      remoteJobs = await get('api/desktop/remote/jobs');
    } catch (e) {
      remoteJobs = { error: errText(e) };
    }
  }
  const mode = $derived(settings?.compute?.mode ?? 'local');
  const remote = $derived(compute?.remote);
  const when = (s?: string) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
  const tabs = [
    ['storage', 'Storage'],
    ['compute', 'Compute'],
    ['backups', 'Backups'],
  ] as const;
</script>

{#if tab}
  <Modal title="Settings" onclose={() => (desk.settings = null)} width={720}>
    <div class="tabs" role="tablist">
      {#each tabs as [id, label] (id)}
        <button role="tab" aria-selected={tab === id} onclick={() => (desk.settings = id)}>{label}</button>
      {/each}
    </div>
    {#if error}<p class="error-line">{error}</p>{/if}

    {#if tab === 'storage'}
      <section>
        <h3>Where MPN Studio keeps things</h3>
        {#each [['Settings', storage?.locations.config], ['Projects and backups', storage?.locations.data], ['Cache (safe to delete)', storage?.locations.cache]] as [k, v] (k)}
          <div class="kv"><span>{k}</span><span class="mono ellipsis" title={v}>{v ?? ''}</span><button class="btn" disabled={!v} onclick={() => openInFileManager(v)}>Show</button></div>
        {/each}
        {#if storage}<p class="muted">{storage.disk.free_gib} GiB free of {storage.disk.total_gib} GiB on the data disk.</p>{/if}
      </section>

      <section>
        <h3>Large files</h3>
        <p class="muted">{storage?.note ?? ''}</p>
        {#each [['models_dir', 'Model weights', 'Choose the folder that holds the model weights'], ['checkpoints_dir', 'Checkpoints', 'Choose the folder that holds trained checkpoints'], ['datasets_dir', 'Datasets', 'Choose the folder that holds datasets']] as [key, label, title] (key)}
          {@const v = settings?.storage?.[key]}
          <div class="kv">
            <span>{label}</span>
            <span class="mono ellipsis" class:muted={!v} title={v ?? ''}>{v ?? 'Not set'}</span>
            <span class="row">
              <button class="btn" onclick={() => choose(key as any, title)}>Choose...</button>
              <button class="btn quiet" disabled={!v} onclick={() => save({ storage: { [key]: null } })}>Clear</button>
            </span>
          </div>
        {/each}
      </section>

      <section>
        <h3>Checkpoints this project refers to</h3>
        {#if storage && storage.checkpoints.length === 0}
          <p class="muted">None. The project holds nodes and relations only.</p>
        {:else if storage}
          <table>
            <thead><tr><th class="l">Checkpoint</th><th class="l">On this computer</th><th>Size</th></tr></thead>
            <tbody>
              {#each storage.checkpoints as c (c.id)}
                <tr>
                  <td class="l mono">{c.id}</td>
                  <td class="l">
                    {#if c.available}<span class="mono ellipsis" title={c.resolved_path}>{c.resolved_path}</span>
                    {:else}<span class="muted">Not here. Recorded at <span class="mono">{c.recorded_path}</span></span>{/if}
                  </td>
                  <td class="num">{c.bytes != null ? fmtBytes(c.bytes) : ''}</td>
                </tr>
              {/each}
            </tbody>
          </table>
          <p class="muted">Missing checkpoints do not affect editing. Set the Checkpoints folder above (or copy the checkpoint there) and it is found by its id.</p>
        {/if}
      </section>
    {/if}

    {#if tab === 'compute'}
      <section>
        <h3>This computer</h3>
        {#if compute}
          <ul class="feat">
            {#each compute.features as f (f.id)}
              <li class:off={!f.available}>
                <span class="state">{f.available ? 'Available' : 'Unavailable'}</span>
                <span>{f.label}{#if f.reason}<span class="muted why">{f.reason}</span>{/if}</span>
              </li>
            {/each}
          </ul>
          <p class="muted">{compute.gpu ? `GPU: ${compute.gpu.name}, ${fmtInt(Math.round(compute.gpu.memory_mib / 1024))} GiB.` : 'No NVIDIA GPU was found.'}</p>
        {/if}
        <div class="kv">
          <span>Runtime Python</span>
          <span class="mono ellipsis" class:muted={!settings?.compute?.python} title={settings?.compute?.python ?? ''}>{settings?.compute?.python ?? 'Not set'}</span>
          <span class="row">
            <button class="btn" onclick={choosePython}>Choose...</button>
            <button class="btn quiet" disabled={!settings?.compute?.python} onclick={() => save({ compute: { python: null } })}>Clear</button>
          </span>
        </div>
        <p class="muted">Only needed to run the Yaob runtime or training here. It must be an environment with torch, transformers, peft and safetensors installed.</p>
        <div class="row">
          {#if compute?.local?.runtime}
            <button class="btn" onclick={stopRuntime} disabled={!!busy}>{busy ?? 'Stop local runtime'}</button>
          {:else}
            <button class="btn primary" onclick={startRuntime} disabled={!!busy || compute?.features?.find((f: any) => f.id === 'local_inference')?.available !== true}>{busy ?? 'Start local runtime'}</button>
          {/if}
          <span class="muted">
            {#if compute?.local?.runtime}
              Running{compute.local.worker ? ' with a job worker' : ', without a job worker (no usable CUDA GPU)'}.{compute.local.engine?.startup_error ? ' The model did not load: ' + compute.local.engine.startup_error : compute.local.engine?.loading ? ' Loading the model...' : ''}
            {:else if compute?.local?.error}{compute.local.error}{/if}
          </span>
        </div>
      </section>

      <section>
        <h3>Remote compute</h3>
        <p class="muted">Optional. Connects over SSH to a machine that runs MPN Studio, such as the DGX Spark. MPN Studio never needs it to start or to edit. Remote answers come from that machine's own network, not from this project.</p>
        <div class="grid">
          <label class="lbl" for="h">SSH host</label><input id="h" class="field" placeholder="spark, or user@host" bind:value={ssh.ssh_host} disabled={remote?.connected} />
          <label class="lbl" for="c">SSH config file</label><span class="row"><input id="c" class="field" placeholder="Optional, uses ~/.ssh/config" bind:value={ssh.ssh_config} disabled={remote?.connected} /></span>
          <label class="lbl" for="a">API port</label><input id="a" class="field narrow" type="number" bind:value={ssh.api_port} disabled={remote?.connected} />
          <label class="lbl" for="r">Runtime port</label><input id="r" class="field narrow" type="number" bind:value={ssh.runtime_port} disabled={remote?.connected} />
        </div>
        <div class="row">
          {#if remote?.connected}
            <button class="btn" onclick={disconnect}>Disconnect</button>
          {:else}
            <button class="btn primary" onclick={connect} disabled={!ssh.ssh_host.trim() || !!busy}>{busy ?? 'Connect'}</button>
          {/if}
          <span class="muted">{mode === 'remote' && !remote?.connected ? 'Remote mode is on but not connected. Working locally.' : remote?.connected ? '' : 'Working locally.'}</span>
        </div>
        {#if remote?.connected}
          <ul class="feat">
            <li><span class="state">Connected</span><span>{remote.host}</span></li>
            <li class:off={!remote.api?.ok}><span class="state">{remote.api?.ok ? 'API answers' : 'API silent'}</span><span>{remote.api?.ok ? `MPN Studio ${remote.api.version}, revision ${remote.api.head}` : (remote.api?.error ?? '')}</span></li>
            <li class:off={remote.runtime?.available === false}><span class="state">{remote.runtime?.available === false ? 'Runtime off' : 'Runtime on'}</span><span class="muted">{remote.runtime?.available === false ? remote.runtime.reason : ''}</span></li>
          </ul>
          <div class="row"><button class="btn" onclick={loadJobs}>Show remote jobs</button></div>
          {#if remoteJobs?.error}<p class="error-line">{remoteJobs.error}</p>
          {:else if remoteJobs}
            {#if remoteJobs.jobs.length === 0}<p class="muted">The remote machine has no jobs.</p>
            {:else}
              <table>
                <thead><tr><th class="l">Job</th><th class="l">Kind</th><th class="l">State</th></tr></thead>
                <tbody>{#each remoteJobs.jobs as j (j.id)}<tr><td class="l mono">{j.id}</td><td class="l">{j.kind}</td><td class="l">{j.state}</td></tr>{/each}</tbody>
              </table>
            {/if}
          {/if}
        {:else if remote?.error}<p class="error-line">{remote.error}</p>{/if}
      </section>
    {/if}

    {#if tab === 'backups'}
      <section>
        <div class="row">
          <h3 class="grow">Backups</h3>
          <button class="btn primary" disabled={!desk.project} onclick={async () => (await backupNow(), await load())}>Back up {desk.project?.name ?? ''} now</button>
        </div>
        <p class="muted">A backup is one file with a checksum. Restoring always makes a new project; nothing is overwritten. A backup is also made automatically before any project is migrated to a newer format.</p>
        {#if backups.length === 0}<p class="muted">No backups yet.</p>
        {:else}
          <table>
            <thead><tr><th class="l">Project</th><th class="l">Taken</th><th>Size</th><th></th></tr></thead>
            <tbody>
              {#each backups as b (b.id)}
                <tr>
                  <td class="l">{b.project}</td><td class="l">{when(b.created)}</td><td class="num">{fmtBytes(b.bytes)}</td>
                  <td><button class="btn" onclick={() => restoreBackup(b.id)}>Restore as new project</button></td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/if}

    <div class="row end"><button class="btn" onclick={() => (desk.settings = null)}>Close</button></div>
  </Modal>
{/if}

<style>
  .tabs {
    display: flex;
    gap: 2px;
    border-bottom: 1px solid var(--line);
    margin: -4px 0 0;
  }
  .tabs button {
    border: 0;
    border-bottom: 2px solid transparent;
    background: none;
    padding: 6px 10px;
    color: var(--muted);
    cursor: pointer;
    margin-bottom: -1px;
  }
  .tabs button[aria-selected='true'] {
    color: var(--white);
    border-bottom-color: var(--white);
  }
  section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  h3 {
    font-weight: 500;
    font-size: 13px;
  }
  .kv {
    display: grid;
    grid-template-columns: 130px minmax(0, 1fr) auto;
    gap: 8px;
    align-items: center;
  }
  .grid {
    display: grid;
    grid-template-columns: 130px minmax(0, 1fr);
    gap: 6px 8px;
    align-items: center;
  }
  .grid .lbl {
    margin: 0;
  }
  .narrow {
    max-width: 110px;
  }
  .feat {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .feat li {
    display: grid;
    grid-template-columns: 96px 1fr;
    gap: 8px;
  }
  .feat .why {
    display: block;
  }
  .feat .state {
    color: var(--white);
    font-weight: 500;
  }
  .feat li.off .state {
    color: var(--gold);
  }
  table {
    width: 100%;
    border-collapse: collapse;
  }
  th {
    font-weight: 400;
    color: var(--muted);
    text-align: right;
    padding: 0 8px 4px;
    border-bottom: 1px solid var(--line);
  }
  td {
    padding: 5px 8px;
    border-bottom: 1px solid var(--line-soft);
    text-align: right;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  th.l,
  td.l {
    text-align: left;
  }
  .end {
    justify-content: flex-end;
  }
</style>
