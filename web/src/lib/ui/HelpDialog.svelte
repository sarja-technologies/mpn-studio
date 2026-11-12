<script lang="ts">
  import { desk, openInFileManager } from '../desktop.svelte';
  import Modal from './Modal.svelte';

  const names: [string, string][] = [
    ['Fylume', 'Research organisation.'],
    ['Yaob', 'Model family.'],
    ['MPN', 'Modular Persistent Network.'],
    ['MPN Studio', 'This desktop application.'],
    ['KTR', 'Knowledge, Thinking, Reasoning: the three cells of a node.'],
    ['3CN', 'Three Cell Node.'],
    ['C4B', 'The cognitive core, about 4 billion parameters.'],
    ['N10K', 'The 10,000-node network configuration.'],
  ];
  const keys: [string, string][] = [
    ['Ctrl+N / Ctrl+O', 'New or open project'],
    ['Ctrl+S', 'Save'],
    ['Ctrl+Shift+E', 'Export project as bundle'],
    ['Ctrl+Z / Ctrl+Shift+Z', 'Undo / redo an edit'],
    ['Ctrl+K or /', 'Find a node'],
    ['Ctrl+,', 'Settings'],
    ['Ctrl+Q', 'Quit'],
    ['Space + drag, arrows', 'Pan the board'],
    ['Mouse wheel, + and -', 'Zoom'],
    ['0 / F', 'Fit all / fit selection'],
    ['N', 'New node'],
    ['C', 'Connect two nodes'],
    ['V / S', 'Move tool / select tool'],
    ['Delete', 'Delete the selection (with preview)'],
    ['Ctrl+C / Ctrl+V', 'Copy and paste node references'],
    ['Alt+Left / Alt+Right', 'Back and forward through views'],
    ['Escape', 'Cancel the current action or clear the selection'],
    ['F1', 'Names and terms'],
  ];
  const info = $derived(desk.info);
  const title = $derived(desk.help === 'names' ? 'Names and terms' : desk.help === 'shortcuts' ? 'Keyboard shortcuts' : 'About MPN Studio');
</script>

{#if desk.help}
  <Modal {title} onclose={() => (desk.help = null)} width={560}>
    {#if desk.help === 'names'}
      <dl>
        {#each names as [k, v] (k)}<dt>{k}</dt><dd>{v}</dd>{/each}
      </dl>
      <p class="muted">
        MPN used to stand for {info?.legacy_architecture_name ?? 'Modular Parameter Network'}. Only the name changed. Stored networks, package files, API paths and internal
        identifiers (such as <code>mpn_*</code> tables and <code>yaob-mpn-package/1</code>) keep their original spelling, so existing projects open unchanged.
      </p>
    {:else if desk.help === 'shortcuts'}
      <dl class="keys">
        {#each keys as [k, v] (k)}<dt><kbd>{k}</kbd></dt><dd>{v}</dd>{/each}
      </dl>
    {:else}
      <dl>
        <dt>Version</dt><dd>{info?.version}</dd>
        <dt>Architecture</dt><dd>{info?.architecture}</dd>
        <dt>Settings</dt><dd><button class="linkbtn" onclick={() => info && openInFileManager(info.paths.config)}>{info?.paths.config}</button></dd>
        <dt>Projects</dt><dd><button class="linkbtn" onclick={() => info && openInFileManager(info.paths.projects)}>{info?.paths.projects}</button></dd>
        <dt>Cache</dt><dd><button class="linkbtn" onclick={() => info && openInFileManager(info.paths.cache)}>{info?.paths.cache}</button></dd>
      </dl>
      <p class="muted">Fylume. MPN Studio runs on this computer and makes no network connection of its own. Remote compute is used only when you connect it under Settings.</p>
    {/if}
    <div class="row end"><button class="btn" onclick={() => (desk.help = null)}>Close</button></div>
  </Modal>
{/if}

<style>
  dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 6px 18px;
    margin: 0;
  }
  dt {
    font-weight: 500;
  }
  dd {
    margin: 0;
    color: var(--muted);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .keys dt kbd {
    font: 500 12px var(--sans);
  }
  .end {
    justify-content: flex-end;
  }
  .linkbtn {
    border: 0;
    background: none;
    padding: 0;
    color: var(--white);
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
    text-align: left;
    font: inherit;
    overflow-wrap: anywhere;
  }
</style>
