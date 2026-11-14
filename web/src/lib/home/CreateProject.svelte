<script lang="ts">
  // Create a project: start blank, duplicate one you have, or import one from a file or folder.
  import { onMount, untrack } from 'svelte';
  import { errText, fmtBytes, get, post } from '../api';
  import { pickFile, pickFolder } from '../desktop.svelte';
  import { autofocus } from '../ui/actions';
  import Modal from '../ui/Modal.svelte';
  import { home, kindLine } from './home.svelte';

  const d = home.dialog?.kind === 'create' ? home.dialog : { kind: 'create' as const };
  let tab = $state<'blank' | 'duplicate' | 'import'>(d.tab ?? 'blank');
  let name = $state('');
  let busy = $state<string | null>(null);
  let error = $state<string | null>(null);

  // blank
  let withCore = $state(false);
  let cores = $state.raw<{ id: string; name: string; kind: string; bytes?: number; info?: any }[]>([]);
  let coreId = $state('');

  // duplicate
  let from = $state<string>(d.from ?? home.projects.find((p) => !p.damaged)?.id ?? '');
  const src = $derived(home.byId(from));

  // import
  let path = $state('');

  onMount(async () => {
    try {
      const r = await get<{ cores: any[] }>('api/library/cores');
      cores = r.cores.filter((c) => c.kind === 'core' && c.present !== false);
      coreId = cores[0]?.id ?? '';
    } catch {
      cores = [];
    }
  });
  // Suggest "<source> copy" when the source changes, unless a name of one's own was typed.
  let suggested = '';
  $effect(() => {
    if (tab !== 'duplicate' || !src) return;
    const s = `${src.name} copy`;
    untrack(() => {
      if (!name || name === suggested) name = s;
      suggested = s;
    });
  });

  const close = () => !busy && (home.dialog = null);
  const canGo = $derived(!busy && (tab === 'import' ? !!path : !!name.trim()) && (tab !== 'duplicate' || !!src));

  async function go() {
    error = null;
    try {
      if (tab === 'blank') {
        busy = 'Creating';
        await post('api/projects/blank', { name: name.trim(), core: withCore && coreId ? coreId : null });
      } else if (tab === 'duplicate') {
        busy = src?.kind === 'network' ? 'Copying the network' : 'Creating the fork';
        await post(`api/projects/${from}/duplicate`, { name: name.trim() });
      } else {
        busy = 'Importing';
        await post('api/projects/import', { path, name: name.trim() || null });
      }
      home.enter();
    } catch (e) {
      error = errText(e);
      busy = null;
    }
  }
  async function chooseFile() {
    const f = await pickFile('Choose a fork file, backup or store file', ['mpnfork', 'mpnbak', 'sqlite', 'db']);
    if (f) path = f;
  }
  async function chooseFolder() {
    const f = await pickFolder('Choose a network folder, Yaob release or bundle folder');
    if (f) path = f;
  }
  function dupNote(): string {
    const p = src;
    if (!p) return '';
    if (p.kind === 'network') return `Copies the whole network (${fmtBytes(p.bytes ?? 0)}) with its history. The original is not changed.`;
    const size = p.network?.storage_bytes ? ` (${fmtBytes(p.network.storage_bytes)})` : '';
    return `Makes an editable fork. The network${size} stays where it is and is not copied: the new project keeps only your changes, and the original is never modified.`;
  }
</script>

<Modal title="Create project" onclose={close} width={560}>
  <div class="choices" role="radiogroup" aria-label="How to start">
    <label class:on={tab === 'blank'}><input type="radio" bind:group={tab} value="blank" />
      <span><b>Blank project</b><span class="muted">A new, empty network you build yourself</span></span></label>
    <label class:on={tab === 'duplicate'}><input type="radio" bind:group={tab} value="duplicate" />
      <span><b>Duplicate existing</b><span class="muted">An independent copy you can change freely</span></span></label>
    <label class:on={tab === 'import'}><input type="radio" bind:group={tab} value="import" />
      <span><b>Import</b><span class="muted">A network, model project, backup or fork file you already have</span></span></label>
  </div>

  {#if tab === 'blank'}
    <label class="lab" for="np-name">Name</label>
    <input id="np-name" class="field" placeholder="My Biology Model" bind:value={name} use:autofocus maxlength="120"
      onkeydown={(e) => e.key === 'Enter' && canGo && go()} />
    <p class="muted note">
      The project starts as an empty knowledge network: you add nodes, write what each one knows and connect them. To chat with it,
      attach a cognitive core, an existing open-weight language model. Attaching a core does not train it; the model answers by
      reading your nodes.
    </p>
    <label class="check"><input type="checkbox" bind:checked={withCore} disabled={!cores.length} /> Attach a cognitive core now</label>
    {#if withCore}
      <select class="field" bind:value={coreId} aria-label="Cognitive core">
        {#each cores as c (c.id)}<option value={c.id}>{c.name}{c.bytes ? ` (${fmtBytes(c.bytes)})` : ''}</option>{/each}
      </select>
    {:else if !cores.length}
      <p class="muted note">No cognitive cores are installed yet. You can add one later from the project's Model button.</p>
    {/if}
  {:else if tab === 'duplicate'}
    <label class="lab" for="np-from">Project to duplicate</label>
    <select id="np-from" class="field" bind:value={from}>
      {#each home.projects.filter((p) => !p.damaged) as p (p.id)}<option value={p.id}>{p.name}, {kindLine(p, home.projects).toLowerCase()}</option>{/each}
    </select>
    <label class="lab" for="np-dname">Name of the copy</label>
    <input id="np-dname" class="field" bind:value={name} maxlength="120" onkeydown={(e) => e.key === 'Enter' && canGo && go()} />
    {#if src}<p class="muted note">{dupNote()}</p>{/if}
  {:else}
    <div class="row pick">
      <button class="btn" onclick={chooseFile}>Choose a file…</button>
      <button class="btn" onclick={chooseFolder}>Choose a folder…</button>
    </div>
    {#if path}<p class="mono path">{path}</p>{/if}
    <p class="muted note">
      Files: fork files (.mpnfork), backups (.mpnbak) and network store files. Folders: an installed MPN network (linked where it
      is, not copied), a Yaob release or a network bundle. A finished model file (.mpn) is opened from Open project.
    </p>
    <label class="lab" for="np-iname">Name (optional)</label>
    <input id="np-iname" class="field" bind:value={name} maxlength="120" />
  {/if}

  {#if error}<p class="error-line">{error}</p>{/if}
  <div class="row end">
    <button class="btn" onclick={close} disabled={!!busy}>Cancel</button>
    <button class="btn primary" onclick={go} disabled={!canGo}>{busy ? `${busy}…` : tab === 'import' ? 'Import' : 'Create'}</button>
  </div>
</Modal>

<style>
  .choices {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .choices label {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 9px 10px;
    border: 1px solid var(--line);
    border-radius: 2px;
    cursor: pointer;
  }
  .choices label:hover {
    border-color: #4a4a4a;
  }
  .choices label.on {
    border-color: var(--white);
  }
  .choices input {
    margin: 2px 0 0;
    accent-color: var(--white);
  }
  .choices span {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  b {
    font-weight: 500;
  }
  .lab {
    margin-bottom: -6px;
    color: var(--muted);
  }
  .note {
    line-height: 19px;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .check input {
    accent-color: var(--white);
  }
  .pick {
    gap: 8px;
  }
  .path {
    word-break: break-all;
  }
  .end {
    justify-content: flex-end;
    gap: 8px;
  }
</style>
