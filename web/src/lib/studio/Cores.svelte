<script lang="ts">
  import { app } from '../state.svelte';
  import { del, errText, fmtBytes, post } from '../api';
  import { studio, fmtParams, pct, type CoreRow, type CoreServer, type TaskView } from './studio.svelte';
  import CoreServerDialog from './CoreServerDialog.svelte';
  import Releases from './Releases.svelte';

  const files = $derived(studio.data?.cores ?? []);
  const servers = $derived(studio.data?.core_servers ?? []);
  let busy = $state.raw<TaskView | null>(null);
  let error = $state<string | null>(null);
  let menuFor = $state<string | null>(null);
  let outtype = $state('q8_0');
  let dialog = $state.raw<{ server: CoreServer | null } | null>(null);
  let testing = $state<Record<string, boolean>>({});

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
  async function removeFile(c: CoreRow) {
    menuFor = null;
    try {
      const r = await del<any>(`api/library/cores/${c.id}`);
      await studio.load();
      app.toast(r.moved_to ? `${c.name} was moved to ${r.moved_to}.` : `${c.name} was removed from the library.`);
    } catch (e) {
      error = errText(e);
    }
  }
  async function removeServer(s: CoreServer) {
    menuFor = null;
    try {
      await del(`api/core-servers/${s.id}`);
      await studio.load();
      app.toast(`${s.name} was removed. The server itself is untouched.`);
    } catch (e) {
      error = errText(e);
    }
  }
  async function test(s: CoreServer) {
    testing = { ...testing, [s.id]: true };
    error = null;
    try {
      await post(`api/core-servers/${s.id}/test`);
      await studio.load();
    } catch (e) {
      error = errText(e);
    }
    testing = { ...testing, [s.id]: false };
  }
  const host = (u: string) => u.replace(/^https?:\/\//, '').replace(/\/v1$/, '');
  const what = (c: CoreRow) => (c.kind === 'encoder' ? 'Retrieval encoder' : c.projector ? 'Language model with vision' : 'Language model');
  const usedBy = (id: string) => (studio.data?.models ?? []).filter((m) => m.core?.id === id).map((m) => m.name);
  const status = (s: CoreServer) =>
    testing[s.id] ? 'Testing' : !s.last_test ? 'Not tested yet' : s.last_test.ok ? `Answered in ${s.last_test.latency_ms} ms` : s.last_test.message;
</script>

<div class="wrap">
  <div class="row head">
    <h1>Cognitive cores</h1>
    <span class="grow"></span>
    <button class="btn" disabled={!!busy} onclick={() => run('hf')}>Convert Hugging Face folder</button>
    <button class="btn" disabled={!!busy} onclick={() => run('gguf')}>Import GGUF file</button>
    <button class="btn primary" onclick={() => (dialog = { server: null })}>Add a core on a server</button>
  </div>
  <p class="muted">
    A pairing answers with one core and reads one network. Use a model you already serve, or let Studio serve a GGUF file with llama.cpp{files[0]?.compatibility.llama_cpp ? ` ${files[0].compatibility.llama_cpp}` : ''}.
  </p>

  {#if busy}
    <div class="task">
      <div class="row"><span class="grow">{busy.title}{busy.progress.label ? `: ${busy.progress.label}` : ''}</span>{#if pct(busy) != null}<span class="num muted">{pct(busy)}%</span>{/if}</div>
      {#if pct(busy) != null}<div class="bar"><i style="width:{pct(busy)}%"></i></div>{/if}
      {#if busy.log.length}<pre class="log">{busy.log.slice(-6).join('\n')}</pre>{/if}
    </div>
  {/if}
  {#if error}<p class="error-line">{error}</p>{/if}

  {#if servers.length || files.length}
    <table class="list">
      <thead><tr><th>Core</th><th class="hide-narrow">Runs on</th><th class="hide-narrow">Status</th><th class="r hide-narrow">Size</th><th></th></tr></thead>
      <tbody>
        {#each servers as s (s.id)}
          <tr>
            <td>
              <span class="nm">{s.name}</span>
              <div class="muted small mono">{s.model_id}{s.context_tokens ? `, ${s.context_tokens.toLocaleString()} tokens` : ''}</div>
              <div class="muted small show-narrow">{host(s.base_url)}, {status(s).toLowerCase()}</div>
              {#if usedBy('endpoint:' + s.id).length}<div class="muted small">Used by {usedBy('endpoint:' + s.id).join(', ')}</div>{/if}
            </td>
            <td class="hide-narrow">{host(s.base_url)}<div class="muted small">{s.has_key ? 'API key ' + (s.key_source === 'saved in Studio' ? 'saved' : 'from the environment') : 'No API key'}</div></td>
            <td class="hide-narrow status">{status(s)}</td>
            <td class="r hide-narrow"></td>
            <td class="act">
              <button class="btn" disabled={testing[s.id]} onclick={() => test(s)}>Test connection</button>
              <button class="btn" onclick={() => (dialog = { server: s })}>Edit</button>
              <div class="anchor">
                <button class="btn icon" aria-label="More actions for {s.name}" aria-haspopup="menu" aria-expanded={menuFor === s.id} onclick={() => (menuFor = menuFor === s.id ? null : s.id)}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
                </button>
                {#if menuFor === s.id}
                  <div class="pop menu" role="menu">
                    <button role="menuitem" onclick={() => ((menuFor = null), studio.newPairing(s.id_ref))}>Pair with a network</button>
                    <hr />
                    <button role="menuitem" onclick={() => removeServer(s)}>Remove</button>
                  </div>
                {/if}
              </div>
            </td>
          </tr>
        {/each}
        {#each files as c (c.id)}
          <tr>
            <td>
              <span class="nm">{c.name}</span>
              <div class="muted small">{c.info.architecture}, {fmtParams(c.info.parameters)} parameters, {c.info.quantization}{c.info.context_length ? `, ${Math.round(c.info.context_length / 1024)}K context` : ''}</div>
              {#if !c.present}<p class="problem small">The file is missing: {c.path}</p>{/if}
              {#each c.compatibility.notes as n}<p class="muted small">{n}</p>{/each}
              {#if c.release}<div class="muted small">Official release {c.release.id} {c.release.version}</div>{/if}
            </td>
            <td class="hide-narrow">Studio, llama.cpp<div class="muted small">{c.info.license ?? 'licence not stated'}</div></td>
            <td class="hide-narrow">{c.present ? 'File present' : 'File missing'}</td>
            <td class="r hide-narrow num">{fmtBytes(c.info.bytes)}</td>
            <td class="act">
              {#if c.kind === 'core'}<button class="btn" disabled={!c.present || !c.compatibility.runnable} onclick={() => studio.newPairing(c.id)}>Pair with a network</button>{/if}
              <div class="anchor">
                <button class="btn icon" aria-label="More actions for {c.name}" aria-haspopup="menu" aria-expanded={menuFor === c.id} onclick={() => (menuFor = menuFor === c.id ? null : c.id)}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
                </button>
                {#if menuFor === c.id}
                  <div class="pop menu" role="menu">
                    <button role="menuitem" onclick={() => removeFile(c)}>Move to trash</button>
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
      <p>No cores yet. Add the server you already run, install an official core below, or import a GGUF file.</p>
    </div>
  {/if}
  <p class="muted small">
    Converted Hugging Face folders are saved as
    <select class="field inline" aria-label="Precision of converted cores" bind:value={outtype}>
      <option value="q8_0">Q8_0</option>
      <option value="f16">F16</option>
      <option value="bf16">BF16</option>
    </select>
    Converting needs a Python environment with torch and transformers, chosen in Settings.
  </p>

  <Releases kind="core" title="Official cores" />
</div>

{#if dialog}
  <CoreServerDialog server={dialog.server} onclose={() => (dialog = null)} ondone={(s) => ((dialog = null), app.toast(`${s.name} is saved.`))} />
{/if}

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
    padding: 32px 0;
    text-align: center;
  }
  .status {
    max-width: 240px;
  }
  .show-narrow {
    display: none;
  }
  @media (max-width: 760px) {
    .show-narrow {
      display: block;
    }
  }
</style>
