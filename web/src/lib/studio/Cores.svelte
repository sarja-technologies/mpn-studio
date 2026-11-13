<script lang="ts">
  import { app } from '../state.svelte';
  import { del, errText, fmtBytes } from '../api';
  import { studio, fmtParams, pct, type CoreRow, type TaskView } from './studio.svelte';

  const rows = $derived(studio.data?.cores ?? []);
  let busy = $state.raw<TaskView | null>(null);
  let error = $state<string | null>(null);
  let menuFor = $state<string | null>(null);
  let outtype = $state('q8_0');

  async function run(kind: 'gguf' | 'hf') {
    error = null;
    try {
      const c = kind === 'gguf' ? await studio.importGguf((t) => (busy = t)) : await studio.importHf((t) => (busy = t), outtype);
      if (c) app.toast(`${c.name} is in the library.`);
    } catch (e) {
      error = errText(e);
    }
    busy = null;
  }
  async function remove(c: CoreRow) {
    menuFor = null;
    try {
      const r = await del<any>(`api/library/cores/${c.id}`);
      await studio.load();
      app.toast(r.moved_to ? `${c.name} was moved to ${r.moved_to}.` : `${c.name} was removed from the library.`);
    } catch (e) {
      error = errText(e);
    }
  }
  const what = (c: CoreRow) => (c.kind === 'encoder' ? 'Retrieval encoder' : c.projector ? 'Language model with vision' : 'Language model');
</script>

<div class="wrap">
  <div class="row head">
    <h1>Cognitive cores</h1>
    <span class="grow"></span>
    <button class="btn" disabled={!!busy} onclick={() => run('hf')}>Convert Hugging Face folder</button>
    <button class="btn primary" disabled={!!busy} onclick={() => run('gguf')}>Import GGUF file</button>
  </div>
  <p class="muted">
    Cores run through llama.cpp {rows[0]?.compatibility.llama_cpp ?? ''}, unchanged. Converting needs a Python environment with torch and transformers, chosen in Settings.
    Converted cores are saved as
    <select class="field inline" aria-label="Precision of converted cores" bind:value={outtype}>
      <option value="q8_0">Q8_0</option>
      <option value="f16">F16</option>
      <option value="bf16">BF16</option>
    </select>
  </p>

  {#if busy}
    <div class="task">
      <div class="row"><span class="grow">{busy.title}{busy.progress.label ? `: ${busy.progress.label}` : ''}</span>{#if pct(busy) != null}<span class="num muted">{pct(busy)}%</span>{/if}</div>
      {#if pct(busy) != null}<div class="bar"><i style="width:{pct(busy)}%"></i></div>{/if}
      {#if busy.log.length}<pre class="log">{busy.log.slice(-6).join('\n')}</pre>{/if}
    </div>
  {/if}
  {#if error}<p class="error-line">{error}</p>{/if}

  {#if rows.length}
    <table class="list">
      <thead><tr><th>Core</th><th class="hide-narrow">Kind</th><th class="r">Size</th><th class="hide-narrow">Licence</th><th></th></tr></thead>
      <tbody>
        {#each rows as c (c.id)}
          <tr>
            <td>
              <span class="nm">{c.name}</span>
              <div class="muted small">{c.info.architecture}, {fmtParams(c.info.parameters)} parameters, {c.info.quantization}{c.info.context_length ? `, ${Math.round(c.info.context_length / 1024)}K context` : ''}</div>
              {#if !c.present}<p class="problem small">The file is missing: {c.path}</p>{/if}
              {#each c.compatibility.notes as n}<p class="muted small">{n}</p>{/each}
            </td>
            <td class="hide-narrow">{what(c)}</td>
            <td class="r num">{fmtBytes(c.info.bytes)}</td>
            <td class="hide-narrow">{c.info.license ?? 'not stated'}</td>
            <td class="act">
              <div class="anchor">
                <button class="btn icon" aria-label="More actions for {c.name}" aria-haspopup="menu" aria-expanded={menuFor === c.id} onclick={() => (menuFor = menuFor === c.id ? null : c.id)}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
                </button>
                {#if menuFor === c.id}
                  <div class="pop menu" role="menu">
                    <button role="menuitem" onclick={() => remove(c)}>Move to trash</button>
                  </div>
                {/if}
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {:else if !busy}
    <div class="empty">
      <p>No cores yet. Import a GGUF file, or convert a Hugging Face model folder.</p>
    </div>
  {/if}
</div>

<style>
  .head {
    gap: 8px;
    flex-wrap: wrap;
  }
  .nm {
    font-size: 14px;
    font-weight: 500;
  }
  .inline {
    width: auto;
    display: inline-block;
    height: 24px;
    vertical-align: middle;
  }
  .task {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .log {
    margin: 0;
    font: 12px/16px var(--mono);
    color: var(--muted);
    white-space: pre-wrap;
    max-height: 120px;
    overflow: auto;
  }
  .empty {
    padding: 48px 0;
    text-align: center;
  }
</style>
