<script lang="ts">
  // Rename a project, or move it to the trash (the project's name must be typed; nothing is destroyed: it moves to the
  // trash folder). A linked network's own data is never touched by either.
  import { errText, post } from '../api';
  import { desk } from '../desktop.svelte';
  import { autofocus } from '../ui/actions';
  import Modal from '../ui/Modal.svelte';
  import { home } from './home.svelte';

  const d = home.dialog as { kind: 'rename' | 'trash'; project: import('./home.svelte').Project };
  let name = $state(d.kind === 'rename' ? d.project.name : '');
  let error = $state<string | null>(null);
  let busy = $state(false);
  const close = () => !busy && (home.dialog = null);
  const forks = home.projects.filter((p) => p.base === d.project.id);

  async function go() {
    busy = true;
    error = null;
    try {
      if (d.kind === 'rename') await post(`api/projects/${d.project.id}/rename`, { name: name.trim() });
      else await post(`api/projects/${d.project.id}/trash`, { confirm: name, keep_forks_ok: true });
      home.dialog = null;
      await Promise.all([home.load(), desk.refresh()]);
    } catch (e) {
      error = errText(e);
    }
    busy = false;
  }
</script>

{#if d.kind === 'rename'}
  <Modal title="Rename project" onclose={close}>
    <input class="field" bind:value={name} use:autofocus maxlength="120" aria-label="Name" onkeydown={(e) => e.key === 'Enter' && name.trim() && go()} />
    {#if error}<p class="error-line">{error}</p>{/if}
    <div class="row end">
      <button class="btn" onclick={close}>Cancel</button>
      <button class="btn primary" disabled={!name.trim() || busy} onclick={go}>Rename</button>
    </div>
  </Modal>
{:else}
  <Modal title="Move {d.project.name} to the trash?" onclose={close} width={480}>
    {#if d.project.kind === 'linked'}
      <p>Only the link is removed. The network itself stays where it is, unchanged.</p>
    {:else if d.project.kind === 'fork'}
      <p>The fork's changes move to the trash folder. The network it is based on is not affected.</p>
    {:else}
      <p>The project moves to MPN Studio's trash folder. Nothing is destroyed until you empty that folder.</p>
    {/if}
    {#if forks.length}
      <p class="muted">{forks.map((f) => f.name).join(', ')} {forks.length === 1 ? 'is a fork' : 'are forks'} of it and keep working.</p>
    {/if}
    <label class="muted" for="tr-name">Type the project's name to confirm</label>
    <input id="tr-name" class="field" bind:value={name} use:autofocus aria-label="Project name" />
    {#if error}<p class="error-line">{error}</p>{/if}
    <div class="row end">
      <button class="btn" onclick={close}>Cancel</button>
      <button class="btn danger" disabled={name !== d.project.name || busy} onclick={go}>Move to trash</button>
    </div>
  </Modal>
{/if}

<style>
  .end {
    justify-content: flex-end;
    gap: 8px;
  }
</style>
