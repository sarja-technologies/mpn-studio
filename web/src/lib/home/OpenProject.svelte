<script lang="ts">
  // Open something that is not on the home screen yet: a file or folder on this computer, a network on another computer
  // (linked where it is, read-only), or a finished model file (.mpn), which runs without being imported.
  import { errText, fmtBytes, fmtInt, post } from '../api';
  import { pickFile, pickFolder } from '../desktop.svelte';
  import { work } from '../work/work.svelte';
  import { studio } from '../studio/studio.svelte';
  import Modal from '../ui/Modal.svelte';
  import { home } from './home.svelte';

  let address = $state('');
  let probe = $state.raw<any>(null);
  let busy = $state<string | null>(null);
  let error = $state<string | null>(null);
  let local = $state('');

  const close = () => !busy && (home.dialog = null);

  async function run(label: string, f: () => Promise<void>) {
    error = null;
    busy = label;
    try {
      await f();
    } catch (e) {
      error = errText(e);
      busy = null;
    }
  }
  async function file() {
    const f = await pickFile('Open a project file', ['mpnfork', 'mpnbak', 'sqlite', 'db', 'mpn']);
    if (!f) return;
    if (f.endsWith('.mpn')) {
      home.dialog = null;
      work.library = true;
      await studio.openMpn(f);
      return;
    }
    await run('Opening', async () => {
      await post('api/projects/import', { path: f });
      home.enter();
    });
  }
  async function folder() {
    const f = await pickFolder('Open a network folder, Yaob release or bundle folder');
    if (!f) return;
    local = f;
    await run('Opening', async () => {
      await post('api/projects/import', { path: f });
      home.enter();
    });
  }
  async function check() {
    probe = null;
    await run('Connecting', async () => {
      probe = await post('api/projects/link/probe', { mode: 'remote', target: address.trim() });
      busy = null;
    });
  }
  async function link() {
    await run('Linking', async () => {
      await post('api/projects/link', { sources: [{ mode: 'remote', target: address.trim() }] });
      home.enter();
    });
  }
</script>

<Modal title="Open project" onclose={close} width={560}>
  <section>
    <h3>On this computer</h3>
    <div class="row gap">
      <button class="btn" onclick={file} disabled={!!busy}>Choose a file…</button>
      <button class="btn" onclick={folder} disabled={!!busy}>Choose a folder…</button>
    </div>
    <p class="muted">A fork file, backup, store file or model file (.mpn); or a network folder, Yaob release or bundle folder.
      A network folder is linked where it is and never copied.</p>
    {#if local}<p class="mono path">{local}</p>{/if}
  </section>

  <section>
    <h3>On another computer</h3>
    <div class="row gap">
      <input class="field grow" placeholder="http://computer-name:8844" bind:value={address} aria-label="Network address"
        onkeydown={(e) => e.key === 'Enter' && address.trim() && check()} />
      <button class="btn" onclick={check} disabled={!address.trim() || !!busy}>Check</button>
    </div>
    <p class="muted">The address of a computer that shares an MPN network. The network is read over your local network and stays
      protected; duplicate it to make changes.</p>
    {#if probe}
      <div class="probe">
        <p><b>{probe.official_name ?? probe.identity.name}</b></p>
        <p class="muted num">{fmtInt(probe.identity.nodes)} nodes, {fmtInt(probe.identity.relationships)} relationships,
          {fmtBytes(probe.identity.storage_bytes ?? 0)} on that computer. Answered in {Math.round(probe.ms)} ms.</p>
        <div class="row end"><button class="btn primary" onclick={link} disabled={!!busy}>Link and open</button></div>
      </div>
    {/if}
  </section>

  {#if error}<p class="error-line">{error}</p>{/if}
  {#if busy}<p class="muted">{busy}…</p>{/if}
  <div class="row end"><button class="btn" onclick={close} disabled={!!busy}>Close</button></div>
</Modal>

<style>
  section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  h3 {
    font-weight: 500;
  }
  .gap {
    gap: 8px;
  }
  .path {
    word-break: break-all;
  }
  .probe {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 2px;
  }
  b {
    font-weight: 500;
  }
  .end {
    justify-content: flex-end;
  }
</style>
