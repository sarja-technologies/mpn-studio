<script lang="ts">
  import { onMount } from 'svelte';
  import { errText, patch, post } from '../api';
  import Modal from '../ui/Modal.svelte';
  import { studio, type CoreServer as Server } from './studio.svelte';

  interface TestResult {
    ok: boolean;
    code: string | null;
    message: string;
    latency_ms: number | null;
    models: string[];
    reply: string | null;
    context_tokens: number | null;
    warnings: string[];
  }

  let { server = null, ondone, onclose }: { server?: Server | null; ondone: (s: Server) => void; onclose: () => void } = $props();

  let name = $state(server?.name ?? '');
  let url = $state(server?.base_url ?? 'http://127.0.0.1:8080/v1');
  let model = $state(server?.model_id ?? '');
  let key = $state('');
  let clearKey = $state(false);
  let ctx = $state(server?.context_tokens ? String(server.context_tokens) : '');
  let maxOut = $state(server?.max_output_tokens ? String(server.max_output_tokens) : '');
  let timeout = $state(server?.timeout_s ? String(server.timeout_s) : '');
  let more = $state(!!(server?.context_tokens || server?.max_output_tokens || server?.api_key_env));
  let keyEnv = $state(server?.api_key_env ?? '');
  let result = $state.raw<TestResult | null>(null);
  let testing = $state(false);
  let saving = $state(false);
  let error = $state<string | null>(null);

  const body = () => ({
    name: name.trim(),
    base_url: url.trim(),
    model_id: model.trim(),
    context_tokens: ctx.trim() || null,
    max_output_tokens: maxOut.trim() || null,
    timeout_s: timeout.trim() || null,
    api_key_env: keyEnv.trim() || null,
    ...(key ? { api_key: key } : clearKey ? { api_key: '' } : {}),
  });
  async function test() {
    testing = true;
    result = null;
    error = null;
    try {
      result = await post<TestResult>('api/core-servers/test', { ...body(), id: server?.id });
      if (result.ok && !ctx.trim() && result.context_tokens) ctx = ctx || '';
    } catch (e) {
      error = errText(e);
    }
    testing = false;
  }
  async function save() {
    saving = true;
    error = null;
    try {
      const s = server ? await patch<Server>(`api/core-servers/${server.id}`, body()) : await post<Server>('api/core-servers', body());
      if (result) await post(`api/core-servers/${s.id}/test`).catch(() => {});
      await studio.load();
      ondone(s);
    } catch (e) {
      error = errText(e);
    }
    saving = false;
  }
  let dlg: HTMLFormElement;
  onMount(() => dlg?.querySelector<HTMLInputElement>('input')?.focus());
</script>

<Modal title={server ? `Edit ${server.name}` : 'Add a core on a server'} {onclose} width={540}>
  <form bind:this={dlg} class="f" onsubmit={(e) => (e.preventDefault(), name.trim() && url.trim() && model.trim() && !saving && save())}>
    <p class="muted small lead">Any server that speaks the OpenAI chat API: llama.cpp, vLLM, Ollama, LM Studio, or a hosted service. The network is not touched when you change this later.</p>
    <div class="fld">
      <label class="lbl" for="cs-name">Name</label>
      <input id="cs-name" class="field" bind:value={name} placeholder="Qwen 3.5 4B on the Spark" autocomplete="off" />
    </div>
    <div class="fld">
      <label class="lbl" for="cs-url">Server address</label>
      <input id="cs-url" class="field mono" bind:value={url} placeholder="http://127.0.0.1:8080/v1" autocomplete="off" spellcheck="false" />
    </div>
    <div class="fld">
      <label class="lbl" for="cs-model">Model id</label>
      <input id="cs-model" class="field mono" bind:value={model} placeholder="the id the server lists" autocomplete="off" spellcheck="false" list="cs-models" />
      <datalist id="cs-models">{#each result?.models ?? [] as m}<option value={m}></option>{/each}</datalist>
    </div>
    <div class="fld">
      <label class="lbl" for="cs-key">API key <span class="muted">(optional)</span></label>
      <input id="cs-key" class="field mono" type="password" bind:value={key} autocomplete="new-password"
        placeholder={server?.has_key && !clearKey ? (server.key_source === 'saved in Studio' ? 'Saved. Type to replace it.' : server.key_source ?? '') : 'Stored on this computer, never shown again'} />
      {#if server?.has_key && server.key_source === 'saved in Studio'}
        <label class="chk muted small"><input type="checkbox" bind:checked={clearKey} disabled={!!key} /> Remove the saved key</label>
      {/if}
    </div>
    <details bind:open={more}>
      <summary class="muted">Limits</summary>
      <div class="adv">
        <div>
          <label class="lbl" for="cs-ctx">Context (tokens)</label>
          <input id="cs-ctx" class="field num" inputmode="numeric" bind:value={ctx} placeholder="the server's own" />
        </div>
        <div>
          <label class="lbl" for="cs-out">Longest answer (tokens)</label>
          <input id="cs-out" class="field num" inputmode="numeric" bind:value={maxOut} placeholder="no limit" />
        </div>
        <div>
          <label class="lbl" for="cs-to">Wait up to (seconds)</label>
          <input id="cs-to" class="field num" inputmode="numeric" bind:value={timeout} placeholder="120" />
        </div>
        <div>
          <label class="lbl" for="cs-env">Read the key from</label>
          <input id="cs-env" class="field mono" bind:value={keyEnv} placeholder="environment variable" spellcheck="false" />
        </div>
      </div>
    </details>

    <div class="row">
      <button type="button" class="btn" disabled={testing || !url.trim()} onclick={test}>{testing ? 'Testing' : 'Test connection'}</button>
      {#if result?.ok}<span class="muted" role="status">Answered in {result.latency_ms} ms</span>{/if}
    </div>
    {#if result && !result.ok}<p class="error-line" role="status">{result.message}</p>{/if}
    {#if result?.ok}
      <p class="muted small">
        {#if result.reply}It replied “{result.reply}”.{/if}
        {#if result.context_tokens} Context {result.context_tokens.toLocaleString()} tokens.{/if}
        {#if result.models.length} The server lists {result.models.slice(0, 4).join(', ')}{result.models.length > 4 ? '…' : ''}.{/if}
      </p>
      {#each result.warnings as w}<p class="problem small">{w}</p>{/each}
    {/if}
    {#if error}<p class="error-line">{error}</p>{/if}
    <div class="actions">
      <button type="button" class="btn" onclick={onclose}>Cancel</button>
      <button class="btn primary" disabled={!name.trim() || !url.trim() || !model.trim() || saving}>{server ? 'Save' : 'Add core'}</button>
    </div>
  </form>
</Modal>

<style>
  .f {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .fld {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .f .lbl {
    margin: 0;
  }
  .lead {
    margin: -4px 0 0;
  }
  .row {
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
  }
  .adv {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    padding-top: 10px;
  }
  .adv > div {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  details summary {
    cursor: pointer;
  }
  .chk {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  @media (max-width: 560px) {
    .adv {
      grid-template-columns: 1fr;
    }
  }
</style>
