<script lang="ts">
  import { studio } from '../studio/studio.svelte';
  import { app, PANELS } from '../state.svelte';
  import { desk } from '../desktop.svelte';
  import { backupNow, exportProject, importBundle, importStore, openBackupFile, quitApp, saveProject } from '../desktop-actions';
  import { clickOutside } from './actions';

  let { onsearch }: { onsearch: () => void } = $props();
  type Item = { label: string; kbd?: string; run?: () => void; disabled?: boolean; check?: boolean } | 'sep';
  const st = $derived(app.status);
  const hasProject = $derived(!!desk.project);
  const hasSel = $derived(app.selection.length > 0);

  const menus: { label: string; items: Item[] }[] = $derived([
    {
      label: 'File',
      items: [
        { label: 'New network...', kbd: 'Ctrl+N', run: () => ((studio.tab = 'networks'), (desk.home = true)) },
        { label: 'Open network...', kbd: 'Ctrl+O', run: () => ((studio.tab = 'networks'), (desk.home = true)) },
        { label: 'Open MPN Network...', run: () => ((studio.tab = 'mpnnet'), (desk.home = true)) },
        'sep',
        { label: 'Import bundle folder...', run: importBundle },
        { label: 'Import store file...', run: importStore },
        { label: 'Open backup file...', run: () => openBackupFile() },
        'sep',
        { label: 'Save', kbd: 'Ctrl+S', run: saveProject, disabled: !hasProject },
        { label: 'Export network as bundle...', kbd: 'Ctrl+Shift+E', run: exportProject, disabled: !hasProject },
        { label: 'Back up now', run: backupNow, disabled: !hasProject },
        'sep',
        { label: 'Close network', run: () => desk.closeProject(), disabled: !hasProject },
        'sep',
        { label: 'Settings...', kbd: 'Ctrl+,', run: () => (desk.settings = 'storage') },
        { label: 'Quit', kbd: 'Ctrl+Q', run: quitApp },
      ],
    },
    {
      label: 'Edit',
      items: [
        { label: 'Undo', kbd: 'Ctrl+Z', run: () => app.undo(), disabled: !st?.undo },
        { label: 'Redo', kbd: 'Ctrl+Shift+Z', run: () => app.redo(), disabled: !st?.redo },
        'sep',
        { label: 'Find node...', kbd: 'Ctrl+K', run: onsearch, disabled: !hasProject },
      ],
    },
    {
      label: 'View',
      items: [
        { label: 'Fit all', kbd: '0', run: () => app.boardApi?.fitAll(), disabled: !hasProject },
        { label: 'Fit selection', kbd: 'F', run: () => app.boardApi?.fitSelection(), disabled: !hasSel },
        { label: 'Zoom in', kbd: '+', run: () => app.boardApi?.zoomBy(1.3), disabled: !hasProject },
        { label: 'Zoom out', kbd: '-', run: () => app.boardApi?.zoomBy(1 / 1.3), disabled: !hasProject },
        'sep',
        ...PANELS.map((p): Item => ({ label: p.title, check: app.isOpen(p.id), run: () => (app.isOpen(p.id) ? app.closePanel(p.id) : app.showPanel(p.id)), disabled: !hasProject })),
      ],
    },
    {
      label: 'Network',
      items: [
        { label: 'New node...', kbd: 'N', run: () => (app.dialog = { kind: 'newNode', x: null, y: null }), disabled: !hasProject },
        { label: 'New MPN...', run: () => (app.dialog = { kind: 'newMpn' }), disabled: !hasProject },
        { label: 'Connect nodes', kbd: 'C', run: () => app.boardApi?.startConnect(app.selection.length === 1 ? app.selection[0] : undefined), disabled: !hasProject },
        { label: 'Delete selection', kbd: 'Del', run: () => (app.dialog = { kind: 'delete', ids: app.selection }), disabled: !hasSel },
        'sep',
        { label: 'Cell schemas', run: () => app.showPanel('schema'), disabled: !hasProject },
      ],
    },
    {
      label: 'Tools',
      items: [
        { label: 'Ask Yaob', run: () => app.showPanel('run'), disabled: !hasProject },
        { label: 'Runtime', run: () => app.showPanel('runtime'), disabled: !hasProject },
        { label: 'Memory budget', run: () => app.showPanel('budget'), disabled: !hasProject },
        { label: 'Training and evaluation jobs', run: () => app.showPanel('jobs'), disabled: !hasProject },
        { label: 'Datasets', run: () => app.showPanel('datasets'), disabled: !hasProject },
        'sep',
        { label: 'Storage and checkpoints...', run: () => (desk.settings = 'storage') },
        { label: 'Compute...', run: () => (desk.settings = 'compute') },
        { label: 'Backups...', run: () => (desk.settings = 'backups') },
      ],
    },
    {
      label: 'Help',
      items: [
        { label: 'Names and terms', kbd: 'F1', run: () => (desk.help = 'names') },
        { label: 'Keyboard shortcuts', run: () => (desk.help = 'shortcuts') },
        { label: 'About MPN Studio', run: () => (desk.help = 'about') },
      ],
    },
  ]);

  let open = $state<number | null>(null);
  let bar: HTMLElement;
  const close = () => (open !== null ? ((open = null), true) : false);
  function pick(it: Item) {
    if (it === 'sep' || it.disabled) return;
    open = null;
    it.run?.();
  }
  function focusItem(step: 1 | -1) {
    const items = [...bar.querySelectorAll<HTMLButtonElement>('.drop [role^="menuitem"]:not(:disabled)')];
    if (!items.length) return;
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    items[(i + step + items.length) % items.length].focus();
  }
  function key(e: KeyboardEvent) {
    if (open === null) return;
    if (e.key === 'Escape') {
      const i = open;
      open = null;
      (bar.querySelectorAll<HTMLButtonElement>('.top')[i] as HTMLElement | undefined)?.focus();
    } else if (e.key === 'ArrowDown') (e.preventDefault(), focusItem(1));
    else if (e.key === 'ArrowUp') (e.preventDefault(), focusItem(-1));
    else if (e.key === 'ArrowRight') open = (open + 1) % menus.length;
    else if (e.key === 'ArrowLeft') open = (open + menus.length - 1) % menus.length;
    else return;
    e.stopPropagation();
  }
</script>

<div class="mb" role="menubar" tabindex="-1" bind:this={bar} aria-label="Application menu" use:clickOutside={close} onkeydown={key}>
  {#each menus as m, i (m.label)}
    <div class="m">
      <button class="top" aria-haspopup="menu" aria-expanded={open === i} onclick={() => (open = open === i ? null : i)} onmouseenter={() => open !== null && (open = i)}>{m.label}</button>
      {#if open === i}
        <div class="pop menu drop" role="menu" aria-label={m.label}>
          {#each m.items as it, j (j)}
            {#if it === 'sep'}
              <hr />
            {:else}
              <button role={it.check !== undefined ? 'menuitemcheckbox' : 'menuitem'} aria-checked={it.check} disabled={it.disabled} onclick={() => pick(it)}>
                <span class="tick">{#if it.check}<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2 6.5 5 9.5 10 3" /></svg>{/if}</span>
                {it.label}
                {#if it.kbd}<span class="kbd">{it.kbd}</span>{/if}
              </button>
            {/if}
          {/each}
        </div>
      {/if}
    </div>
  {/each}
</div>

<style>
  .mb {
    display: flex;
    align-items: center;
    gap: 0;
    height: 100%;
  }
  .m {
    position: relative;
    height: 100%;
    display: flex;
    align-items: center;
  }
  .top {
    height: 26px;
    padding: 0 9px;
    border: 0;
    border-radius: 2px;
    background: transparent;
    color: var(--white);
    cursor: pointer;
  }
  .top:hover,
  .top[aria-expanded='true'] {
    background: #262626;
  }
  .drop {
    position: absolute;
    left: 0;
    top: calc(100% - 4px);
    z-index: 40;
    min-width: 230px;
  }
  .tick {
    width: 12px;
    flex: none;
    display: inline-flex;
  }
</style>
