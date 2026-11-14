<script lang="ts">
  // Export the open project. A project with its own network: the finished model as one .mpn file, the network as a bundle
  // folder, or a backup. A fork: its changes as a .mpnfork file (the network it is based on is never included) or a backup.
  import { onMount } from 'svelte';
  import { errText, fmtBytes, fmtInt, get, post } from '../api';
  import { desk, pickFolder } from '../desktop.svelte';
  import { app } from '../state.svelte';
  import { studio, pct, type TaskView } from '../studio/studio.svelte';
  import Modal from '../ui/Modal.svelte';
  import { work } from './work.svelte';

  const fork = work.kind === 'fork';
  let choice = $state<'mpn' | 'bundle' | 'fork' | 'backup'>(fork ? 'fork' : 'mpn');
  let mode = $state<'complete' | 'lightweight'>('complete');
  let model = $state.raw<{ id: string; name: string; state: string; core: string | null } | null>(null);
  let busy = $state<string | null>(null);
  let error = $state<string | null>(null);
  let done = $state<string | null>(null);
  let task = $state.raw<TaskView | null>(null);
  let fileName = $state((desk.project?.name ?? 'project').replace(/[^\w .-]+/g, '').trim() || 'project');

  onMount(async () => {
    if (fork) return;
    try {
      const r = await get<{ projects: { id: string; models: any[] }[] }>('api/projects');
      model = r.projects.find((p) => p.id === desk.project?.id)?.models[0] ?? null;
    } catch {
      model = null;
    }
  });
  const close = () => !busy && (work.dialog = null);
  const canGo = $derived(!busy && (choice !== 'mpn' || model?.state === 'ready') && (choice !== 'fork' || !!fileName.trim()));

  async function go() {
    error = null;
    done = null;
    try {
      if (choice === 'backup') {
        busy = 'Backing up';
        const r = await post<{ file: string; bytes: number }>('api/desktop/project/backup');
        done = `Backed up (${fmtBytes(r.bytes)}) to ${r.file}`;
      } else {
        const dir = await pickFolder(choice === 'bundle' ? 'Choose where to save the bundle folder' : 'Choose the folder to save the file in');
        if (!dir) return;
        const d = dir.replace(/\/+$/, '');
        if (choice === 'fork') {
          busy = 'Exporting the changes';
          const r = await post<{ file: string; bytes: number; changes: any }>('api/ws/export', { dest: `${d}/${fileName.trim()}.mpnfork` });
          done = `Saved ${fmtBytes(r.bytes)} to ${r.file}`;
        } else if (choice === 'bundle') {
          busy = 'Exporting the network';
          const r = await post<any>('api/desktop/project/export', { dest: `${d}/${desk.project?.id}-rev${app.head}` });
          done = `Exported ${fmtInt(r.counts?.canonical_live_nodes ?? 0)} nodes to ${r.path}`;
        } else if (model) {
          busy = 'Writing the model file';
          const t = await studio.follow(post<TaskView>(`api/library/models/${model.id}/export`, { path: d, mode }), (t) => (task = t));
          if (t.state !== 'done') throw new Error(t.error ?? 'The export did not finish.');
          done = `Saved ${t.result?.file ?? t.result?.path ?? 'the model file'}${t.result?.bytes ? ` (${fmtBytes(t.result.bytes)})` : ''}`;
        }
      }
    } catch (e) {
      error = errText(e);
    } finally {
      busy = null;
    }
  }
</script>

<Modal title="Export {desk.project?.name}" onclose={close} width={560}>
  <div class="choices" role="radiogroup" aria-label="What to export">
    {#if fork}
      <label class:on={choice === 'fork'}><input type="radio" bind:group={choice} value="fork" />
        <span><b>Fork file (.mpnfork)</b><span class="muted">Your changes only. Whoever opens it needs the same network; it is not included.</span></span></label>
    {:else}
      <label class:on={choice === 'mpn'}><input type="radio" bind:group={choice} value="mpn" />
        <span><b>Model file (.mpn)</b><span class="muted">The finished model in one file: the network and its cognitive core, ready to run.</span></span></label>
      <label class:on={choice === 'bundle'}><input type="radio" bind:group={choice} value="bundle" />
        <span><b>Network bundle folder</b><span class="muted">The network's nodes and relationships as open files, without a cognitive core.</span></span></label>
    {/if}
    <label class:on={choice === 'backup'}><input type="radio" bind:group={choice} value="backup" />
      <span><b>Backup (.mpnbak)</b><span class="muted">The whole project, to restore later as a new project.</span></span></label>
  </div>

  {#if choice === 'mpn'}
    {#if !model}
      <p class="muted">This project has no model yet. Attach a cognitive core and build it from the Model button first.</p>
    {:else if model.state !== 'ready'}
      <p class="muted">{model.name} {model.state === 'needs build' ? 'needs a build' : 'needs a cognitive core'} before it can be exported.</p>
      <div class="row"><button class="btn" onclick={() => (work.dialog = 'model')}>Open Model</button></div>
    {:else}
      <div class="modes" role="radiogroup" aria-label="File size">
        <label><input type="radio" bind:group={mode} value="complete" /> Complete <span class="muted">runs anywhere, larger</span></label>
        <label><input type="radio" bind:group={mode} value="lightweight" /> Lightweight <span class="muted">needs the same core installed</span></label>
      </div>
    {/if}
  {:else if choice === 'fork'}
    <label class="lab" for="ex-name">File name</label>
    <div class="row fn"><input id="ex-name" class="field grow" bind:value={fileName} maxlength="120" /><span class="muted">.mpnfork</span></div>
  {/if}

  {#if task && busy}<p class="muted">{task.progress?.label ?? task.title} {pct(task) != null ? `${pct(task)}%` : ''}</p>{/if}
  {#if error}<p class="error-line">{error}</p>{/if}
  {#if done}<p class="done">{done}</p>{/if}
  <div class="row end">
    <button class="btn" onclick={close} disabled={!!busy}>{done ? 'Done' : 'Cancel'}</button>
    <button class="btn primary" onclick={go} disabled={!canGo}>{busy ? `${busy}…` : choice === 'backup' ? 'Back up' : 'Export…'}</button>
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
  .choices input,
  .modes input {
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
  .modes {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .modes label {
    display: flex;
    gap: 8px;
    align-items: baseline;
  }
  .lab {
    margin-bottom: -6px;
    color: var(--muted);
  }
  .fn {
    gap: 8px;
    align-items: center;
  }
  .done {
    overflow-wrap: anywhere;
  }
  .end {
    justify-content: flex-end;
    gap: 8px;
  }
</style>
