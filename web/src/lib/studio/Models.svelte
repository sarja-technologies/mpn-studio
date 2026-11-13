<script lang="ts">
  import { app } from '../state.svelte';
  import { errText, fmtBytes, fmtInt } from '../api';
  import { studio, fmtParams, type ModelRow } from './studio.svelte';
  import NewModel from './NewModel.svelte';

  const models = $derived(studio.data?.models ?? []);
  let composing = $state(false);
  let menuFor = $state<string | null>(null);

  const netName = (m: ModelRow) => (m.network ? (studio.network(m.network.id)?.name ?? 'missing network') : 'no network');
  const netNodes = (m: ModelRow) => (m.network ? studio.network(m.network.id)?.nodes : undefined);
  const stateWord = (m: ModelRow) => (m.status.state === 'ready' ? 'Built' : m.status.state === 'needs build' ? 'Needs a build' : 'Incomplete');
  const running = (m: ModelRow) => studio.data?.runs.some((r) => r.key === m.id && r.health !== 'stopped');

  async function remove(m: ModelRow) {
    menuFor = null;
    try {
      await studio.removeModel(m.id);
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }
</script>

<div class="wrap">
  {#if !models.length || composing}
    <NewModel first={!models.length} oncancel={models.length ? () => (composing = false) : undefined} />
  {:else}
    <div class="row head">
      <h1>Models</h1>
      <span class="grow"></span>
      <button class="btn" onclick={() => studio.openMpn()}>Open .mpn file</button>
      <button class="btn primary" onclick={() => (composing = true)}>New model</button>
    </div>
    {#if studio.opening}<p class="muted">{studio.opening}</p>{/if}
    <table class="list">
      <thead>
        <tr><th>Model</th><th class="hide-narrow">Cognitive core</th><th class="hide-narrow">Node network</th><th>State</th><th></th></tr>
      </thead>
      <tbody>
        {#each models as m (m.id)}
          <tr>
            <td>
              <button class="rowname" onclick={() => (studio.model = m.id)}>{m.name}</button>
              {#if m.description}<div class="muted small ellipsis">{m.description}</div>{/if}
            </td>
            <td class="hide-narrow">
              {#if m.core}{m.core.name}<span class="muted after">{fmtParams(m.core.parameters)} {m.core.quantization}</span>{:else}<span class="muted">none</span>{/if}
            </td>
            <td class="hide-narrow">
              {netName(m)}{#if netNodes(m) != null}<span class="muted num after">{fmtInt(netNodes(m) ?? 0)} nodes</span>{/if}
            </td>
            <td>{stateWord(m)}{#if running(m)}<span class="muted">, running</span>{/if}</td>
            <td class="act">
              <button class="btn primary" onclick={() => (studio.model = m.id)}>Open</button>
              <div class="anchor">
                <button class="btn icon" aria-label="More actions for {m.name}" aria-haspopup="menu" aria-expanded={menuFor === m.id} onclick={() => (menuFor = menuFor === m.id ? null : m.id)}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
                </button>
                {#if menuFor === m.id}
                  <div class="pop menu" role="menu">
                    <button role="menuitem" onclick={() => remove(m)}>Move to trash</button>
                  </div>
                {/if}
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
    {#if studio.data?.opened.length}
      <h2>Opened .mpn files</h2>
      <table class="list">
        <tbody>
          {#each studio.data.opened as o (o.key)}
            <tr>
              <td><button class="rowname" onclick={() => (studio.model = o.key)}>{o.name}</button><div class="muted small ellipsis">{o.file}</div></td>
              <td class="r muted hide-narrow">{fmtBytes(o.summary?.bytes ?? 0)}</td>
              <td class="act"><button class="btn" onclick={() => (studio.model = o.key)}>Open</button></td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</div>

<style>
  .head {
    gap: 12px;
  }
  .after {
    margin-left: 6px;
  }
</style>
