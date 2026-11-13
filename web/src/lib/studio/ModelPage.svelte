<script lang="ts">
  import { app } from '../state.svelte';
  import { errText, fmtBytes, fmtInt, get, post } from '../api';
  import { pickFolder } from '../desktop.svelte';
  import { studio, fmtParams, pct, type ModelRow, type TaskView } from './studio.svelte';
  import TestChat from './TestChat.svelte';

  let { id }: { id: string } = $props();
  const isFile = $derived(id.startsWith('mpn-'));
  const opened = $derived(studio.data?.opened.find((o) => o.key === id) ?? null);

  let m = $state.raw<ModelRow | null>(null);
  let error = $state<string | null>(null);
  let build = $state.raw<TaskView | null>(null);
  let exp = $state.raw<TaskView | null>(null);
  let mode = $state<'complete' | 'lightweight'>('complete');
  let identity = $state('');
  let conn = $state.raw<{ base_url: string; model: string; api_key: string } | null>(null);
  let menu = $state(false);

  async function load() {
    if (isFile) return;
    try {
      m = await get<ModelRow>(`api/library/models/${id}`);
      identity = m.identity ?? '';
      error = null;
    } catch (e) {
      error = errText(e);
    }
  }
  $effect(() => {
    id;
    load();
  });

  async function set(body: Record<string, unknown>) {
    error = null;
    try {
      m = await studio.updateModel(id, body);
      await load();
    } catch (e) {
      error = errText(e);
    }
  }
  async function doBuild() {
    error = null;
    build = null;
    const t = await studio.follow(post<TaskView>(`api/library/models/${id}/build`, {}), (t) => (build = t)).catch((e) => ((error = errText(e)), null));
    await load();
    studio.load();
    if (t?.state === 'done') app.toast(`${m?.name} is built. Its test question was answered from the network.`);
  }
  async function doExport() {
    const dir = await pickFolder('Choose the folder to save the .mpn file in');
    if (!dir) return;
    exp = null;
    error = null;
    await studio.follow(post<TaskView>(`api/library/models/${id}/export`, { path: dir, mode }), (t) => (exp = t)).catch((e) => (error = errText(e)));
  }
  async function connection() {
    try {
      conn = await get(`api/library/run/${id}/connection`);
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }
  const copy = (s: string) => navigator.clipboard?.writeText(s).then(() => app.toast('Copied.'));

  const net = $derived(m?.network ? studio.network(m.network.id) : null);
  const routerOk = $derived(!!(net?.router && m?.encoder && studio.core(m.encoder.id)?.sha256 === net.router.encoder?.sha256));
  const report = $derived(m?.build_report);
  const test = $derived(report?.test);
  const canRun = $derived(isFile || m?.status.state === 'ready');
  const cap = (t: string) => (t ? t[0].toUpperCase() + t.slice(1) : t);
  const stepLine = (s: any) =>
    cap(
      s.step === 'network snapshot' ? `${fmtInt(s.nodes)} nodes and ${fmtInt(s.relations)} relations, revision ${s.revision}`
      : s.step === 'routing' ? (s.mode === 'learned router' ? 'routing by the learned router' : s.mode)
      : s.step === 'embedding cache' ? (s.reused ? `${fmtInt(s.reused)} node embeddings reused from earlier builds` : '')
      : s.step === 'model loaded' ? `core loaded on ${s.device === 'cpu' ? 'the CPU' : s.device.toUpperCase()} in ${s.seconds} s`
      : s.step === 'retrieval index' ? (s.embedded_now ? `${fmtInt(s.embedded_now)} nodes embedded for retrieval` : s.covered_by_router ? `all ${fmtInt(s.covered_by_router)} nodes reachable through the router` : '')
      : s.step === 'test inference' ? (s.retrieved_expected ? 'test question answered from the expected node' : 'test question answered')
      : '',
    );
</script>

<div class="wrap">
  <div class="row">
    <button class="btn quiet back" onclick={() => (studio.model = null)}>Models</button>
  </div>

  {#if isFile}
    {#if opened}
      <div class="head">
        <h1>{opened.name}</h1>
        <p class="muted">{opened.file}</p>
      </div>
      <section class="facts">
        <div><span class="muted">Cognitive core</span><span>{opened.summary.core?.name} ({fmtParams(opened.summary.core?.parameters)}, {opened.summary.core?.quantization})</span></div>
        <div><span class="muted">Node network</span><span>{fmtInt(opened.summary.network?.nodes ?? 0)} nodes</span></div>
        <div><span class="muted">File</span><span>{opened.summary.mode === 'lightweight' ? 'Lightweight' : 'Complete'}, {fmtBytes(opened.summary.bytes)}</span></div>
        <div><span class="muted">API id</span><span class="mono">{opened.summary.model_id}</span></div>
      </section>
      <section>
        <h2>Test</h2>
        <TestChat key={id} />
      </section>
    {:else}
      <p class="muted">This file is no longer open.</p>
    {/if}
  {:else if !m}
    {#if error}<p class="error-line">{error}</p>{:else}<p class="muted">Reading the model</p>{/if}
  {:else}
    <div class="row head">
      <div class="grow">
        <h1>{m.name}</h1>
        <p class="muted">{m.status.state === 'ready' ? `Built ${new Date(m.status.built ?? '').toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}` : m.status.state === 'needs build' ? 'Needs a build' : 'Incomplete'}</p>
      </div>
      <button class="btn {m.status.state === 'ready' ? '' : 'primary'}" disabled={m.status.state === 'incomplete' || build?.state === 'running'} onclick={doBuild}>{m.build?.ok ? 'Rebuild' : 'Build'}</button>
      <div class="anchor">
        <button class="btn icon" aria-label="More actions" aria-haspopup="menu" aria-expanded={menu} onclick={() => (menu = !menu)}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
        </button>
        {#if menu}
          <div class="pop menu" role="menu">
            <button role="menuitem" onclick={() => ((menu = false), studio.removeModel(id))}>Move to trash</button>
          </div>
        {/if}
      </div>
    </div>
    {#each m.status.problems as p}<p class="problem">{p}</p>{/each}
    {#if error}<p class="error-line">{error}</p>{/if}

    <div class="cols">
      <section class="settings">
        <h2>Made of</h2>
        <div>
          <label class="lbl" for="mp-core">Cognitive core</label>
          <select id="mp-core" class="field" value={m.core?.id ?? ''} onchange={(e) => set({ core_id: e.currentTarget.value })}>
            {#if !m.core}<option value="">Choose a core</option>{/if}
            {#each studio.cores as c (c.id)}<option value={c.id} disabled={!c.present || !c.compatibility.runnable}>{c.name} ({fmtParams(c.info.parameters)}, {c.info.quantization})</option>{/each}
          </select>
        </div>
        <div>
          <label class="lbl" for="mp-net">Node network</label>
          <select id="mp-net" class="field" value={m.network?.id ?? ''} onchange={(e) => set({ network_id: e.currentTarget.value })}>
            {#if !m.network}<option value="">Choose a network</option>{/if}
            {#each studio.data?.networks.filter((n) => !n.damaged) ?? [] as n (n.id)}<option value={n.id}>{n.name} ({fmtInt(n.nodes ?? 0)} nodes)</option>{/each}
          </select>
        </div>
        <div>
          <label class="lbl" for="mp-enc">Retrieval encoder</label>
          <select id="mp-enc" class="field" value={m.encoder?.id ?? ''} onchange={(e) => set({ encoder_id: e.currentTarget.value || null })}>
            <option value="">None: text search only</option>
            {#each studio.encoders as c (c.id)}<option value={c.id} disabled={!c.present}>{c.name}</option>{/each}
          </select>
          <p class="muted small">
            {#if routerOk}Routing uses the network's learned router.{:else if net?.router}The network's learned router needs {net.router.encoder?.name}; without it the model routes by retrieval.{:else if m.encoder}Routing by meaning and text search.{:else}Routing by text search.{/if}
          </p>
        </div>
        <div>
          <label class="lbl" for="mp-id">Identity</label>
          <textarea id="mp-id" class="field" rows="3" bind:value={identity} placeholder="You are {m.name}, an AI assistant built with MPN Studio…" onchange={() => set({ identity: identity.trim() || null })}></textarea>
          <p class="muted small">How the model introduces itself. Leave empty for the default.</p>
        </div>
        <details>
          <summary class="muted">Advanced</summary>
          <div class="adv">
            <div>
              <label class="lbl" for="mp-api">API id</label>
              <input id="mp-api" class="field mono" value={m.model_id} onchange={(e) => set({ model_id: e.currentTarget.value.trim() })} />
            </div>
            <div>
              <label class="lbl" for="mp-ctx">Context length (tokens)</label>
              <input id="mp-ctx" class="field num" type="number" min="2048" step="1024" value={m.context.tokens} onchange={(e) => set({ context: { tokens: +e.currentTarget.value } })} />
            </div>
            <div>
              <label class="lbl" for="mp-tn">Nodes read per answer</label>
              <input id="mp-tn" class="field num" type="number" min="1" max="12" value={m.retrieval.text_nodes} onchange={(e) => set({ retrieval: { text_nodes: +e.currentTarget.value } })} />
            </div>
            <div>
              <label class="lbl" for="mp-tr">Tool rounds</label>
              <input id="mp-tr" class="field num" type="number" min="0" max="4" value={m.retrieval.tool_rounds} onchange={(e) => set({ retrieval: { tool_rounds: +e.currentTarget.value } })} />
            </div>
          </div>
        </details>
      </section>

      <section class="build">
        <h2>Build</h2>
        {#if build && build.state === 'running'}
          <p>{build.log.at(-1) ?? 'Starting'}</p>
          <button class="btn" onclick={() => studio.cancel(build!.id)}>Cancel build</button>
        {:else if build && build.state !== 'done'}
          <p class="error-line">{build.state === 'cancelled' ? 'The build was cancelled.' : build.error}</p>
        {/if}
        {#if report && build?.state !== 'running'}
          <ul class="steps">
            {#each report.steps as s}
              {#if stepLine(s)}<li><span>{stepLine(s)}</span></li>{/if}
            {/each}
          </ul>
          {#if test}
            <div class="test">
              <p class="muted small">Test question</p>
              <p>{test.question}</p>
              <p class="muted small">Answer</p>
              <p>{test.answer}</p>
            </div>
          {/if}
        {:else if !build}
          <p class="muted">Building checks the network, indexes it for the encoder, starts the model and asks it one question from the network. The build succeeds only if the answer comes from the network.</p>
        {/if}
      </section>
    </div>

    <section>
      <h2>Test</h2>
      <TestChat key={id} {canRun} why={m.status.state === 'incomplete' ? 'Choose a core and a network first.' : 'Build the model to test it.'} />
      {#if canRun}
        <div class="row">
          <button class="btn quiet" onclick={connection}>Connect another app</button>
        </div>
        {#if conn}
          <div class="conn">
            <p class="muted small">OpenAI-compatible API on this computer, for as long as this run lasts.</p>
            <div class="row"><span class="muted k">Base URL</span><code class="grow">{conn.base_url}</code><button class="btn" onclick={() => copy(conn!.base_url)}>Copy</button></div>
            <div class="row"><span class="muted k">Model</span><code class="grow">{conn.model}</code><button class="btn" onclick={() => copy(conn!.model)}>Copy</button></div>
            <div class="row"><span class="muted k">API key</span><code class="grow ellipsis">{conn.api_key}</code><button class="btn" onclick={() => copy(conn!.api_key)}>Copy</button></div>
          </div>
        {/if}
      {/if}
    </section>

    <section>
      <h2>Export</h2>
      <div class="choices" role="radiogroup" aria-label="Kind of file">
        <label class="choice" class:on={mode === 'complete'}>
          <input type="radio" name="mode" value="complete" bind:group={mode} />
          <span><strong>Complete</strong><span class="muted">One file with everything, the core included. Runs on any computer with MPN Studio or the mpn runtime.</span></span>
        </label>
        <label class="choice" class:on={mode === 'lightweight'}>
          <input type="radio" name="mode" value="lightweight" bind:group={mode} />
          <span><strong>Lightweight</strong><span class="muted">The network, router and settings. The core is found by its checksum on the computer that runs it.</span></span>
        </label>
      </div>
      <div class="row">
        <button class="btn primary" disabled={m.status.state !== 'ready' || exp?.state === 'running'} onclick={doExport}>Export .mpn file</button>
        {#if m.status.state !== 'ready'}<span class="muted">Build the model first.</span>{/if}
      </div>
      {#if exp?.state === 'running'}
        <div class="bar"><i style="width:{pct(exp) ?? 0}%"></i></div>
        <p class="muted small">{exp.progress.label}</p>
      {:else if exp?.state === 'done'}
        <p>Saved {fmtBytes(exp.result.bytes)} to <code>{exp.result.file}</code> in {exp.result.seconds} s.</p>
        <p class="muted small">Run it with <code>mpn run "{exp.result.file}"</code></p>
      {:else if exp}
        <p class="error-line">{exp.error}</p>
      {/if}
    </section>
  {/if}
</div>

<style>
  .back {
    margin-left: -10px;
    color: var(--muted);
  }
  .back::before {
    content: '‹';
    margin-right: 4px;
  }
  .head {
    gap: 12px;
    align-items: flex-start;
  }
  section {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .cols {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 32px;
  }
  .settings > div {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .settings .lbl {
    margin: 0;
  }
  details summary {
    cursor: pointer;
  }
  .adv {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    padding-top: 10px;
  }
  .steps {
    margin: 0;
    padding: 0 0 0 16px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .test {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding-top: 6px;
  }
  .test .small + p {
    margin-bottom: 6px;
  }
  .facts {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 12px 24px;
  }
  .facts div {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .choices {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  .choice {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 2px;
    cursor: pointer;
  }
  .choice.on {
    border-color: var(--white);
  }
  .choice input {
    margin-top: 3px;
    accent-color: var(--white);
  }
  .choice > span {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .conn {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .conn .k {
    width: 72px;
    flex: none;
  }
  .conn code {
    min-width: 0;
  }
  @media (max-width: 760px) {
    .cols,
    .choices {
      grid-template-columns: 1fr;
    }
  }
</style>
