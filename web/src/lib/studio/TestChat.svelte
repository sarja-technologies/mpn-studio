<script lang="ts">
  import Answer from './Answer.svelte';
  import { onDestroy, tick } from 'svelte';
  import { apiFetch, errText, fmtBytes, get, post } from '../api';
  import { studio } from './studio.svelte';

  let { key, canRun = true, why = '' }: { key: string; canRun?: boolean; why?: string } = $props();

  interface Ev {
    nodes: { title: string; mpn: string; read_as: string[]; via: string; score: number }[];
    citations: { n: number; title: string; cited: boolean; source?: { url?: string; title?: string } | null }[];
    checks: { kind: string; ok: boolean; detail: string }[];
    tools: { name: string; result_summary: string }[];
    timing?: { route_ms: number; ttft_ms: number; total_ms: number; tokens_per_s: number | null };
    usage?: { prompt_tokens: number; completion_tokens: number };
    memory?: { backend_peak_rss_mib?: number };
  }
  interface Turn {
    q: string;
    a: string;
    ev: Ev;
    error?: string;
    busy: boolean;
  }
  let health = $state<string>('stopped');
  let status = $state.raw<any>(null);
  let mem = $state.raw<{ ram_bytes?: number; gpu_bytes?: number } | null>(null);
  let log = $state('');
  let turns = $state<Turn[]>([]);
  let q = $state('');
  let starting = $state(false);
  let ctrl = $state.raw<AbortController | null>(null);
  let list: HTMLElement;
  let timer: ReturnType<typeof setInterval> | null = null;

  async function poll() {
    try {
      const r = await get<any>(`api/library/run/${key}`);
      health = r.health;
      status = r.status ?? status;
      mem = r.memory ?? null;
      log = r.log ?? '';
      if (health === 'ok' || health === 'stopped') starting = false;
    } catch {
      health = 'stopped';
    }
  }
  $effect(() => {
    key;
    poll();
    timer = setInterval(poll, 2000);
    return () => timer && clearInterval(timer);
  });
  onDestroy(() => ctrl?.abort());

  async function start() {
    starting = true;
    try {
      await post(`api/library/run/${key}/start`);
      await poll();
    } catch (e) {
      starting = false;
      log = errText(e);
    }
  }
  async function stop() {
    ctrl?.abort();
    await post(`api/library/run/${key}/stop`).catch(() => {});
    await poll();
    studio.load();
  }

  async function ask() {
    const text = q.trim();
    if (!text || health !== 'ok') return;
    q = '';
    const history = turns.filter((t) => !t.error && t.a).flatMap((t) => [
      { role: 'user', content: t.q },
      { role: 'assistant', content: t.a },
    ]);
    const turn: Turn = { q: text, a: '', ev: { nodes: [], citations: [], checks: [], tools: [] }, busy: true };
    turns = [...turns, turn];
    const i = turns.length - 1;
    await tick();
    list?.scrollTo({ top: list.scrollHeight });
    ctrl = new AbortController();
    try {
      const res = await apiFetch(`api/library/run/${key}/chat`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ messages: [...history, { role: 'user', content: text }] }),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) throw new Error((await res.text()) || `The server answered ${res.status}.`);
      const rd = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let ev = '';
      for (;;) {
        const { value, done } = await rd.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).replace(/\r$/, '');
          buf = buf.slice(nl + 1);
          if (line.startsWith('event:')) ev = line.slice(6).trim();
          else if (line.startsWith('data:')) handle(i, ev, JSON.parse(line.slice(5).trim() || '{}'));
        }
      }
    } catch (e: any) {
      if (e?.name !== 'AbortError') turns[i].error = errText(e);
    }
    turns[i].busy = false;
    ctrl = null;
  }
  function handle(i: number, ev: string, d: any) {
    const t = turns[i];
    if (ev === 'token') t.a += d.text ?? '';
    else if (ev === 'route') t.ev.nodes = d.nodes ?? [];
    else if (ev === 'tool') t.ev.tools = [...t.ev.tools, d];
    else if (ev === 'validation') {
      t.ev.citations = d.citations ?? [];
      t.ev.checks = d.checks ?? [];
    } else if (ev === 'done') {
      t.ev.timing = d.timing;
      t.ev.usage = d.usage;
      t.ev.memory = d.memory;
      if (d.text) t.a = d.text;
    } else if (ev === 'error') t.error = d.error ?? 'The model reported an error.';
    if (ev === 'token') list?.scrollTo({ top: list.scrollHeight });
  }
  const read = (t: Turn) => t.ev.nodes.filter((n) => n.read_as?.includes('text'));
  const cited = (t: Turn) => t.ev.citations.filter((c) => c.cited);
  const secs = (ms?: number) => (ms == null ? '' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
  function summary(t: Turn) {
    const parts = [`${cited(t).length} of ${t.ev.citations.length} supplied nodes cited`];
    if (t.ev.tools.length) parts.push(`${t.ev.tools.length} tool ${t.ev.tools.length === 1 ? 'call' : 'calls'}`);
    if (t.ev.timing) parts.push(secs(t.ev.timing.total_ms));
    return parts.join(', ');
  }
  const gpu = $derived(status?.device?.gpu?.[0]);
</script>

<div class="chat">
  <div class="runline row">
    {#if health === 'ok'}
      <span>Running{status?.core?.server ? `, core on ${status.core.server}` : status?.device?.variant ? ` on ${status.device.variant === 'cpu' ? 'the CPU' : status.device.variant.toUpperCase()}` : ''}</span>
      {#if status?.routing}<span class="muted">{status.routing}</span>{/if}
      {#if mem?.ram_bytes}<span class="muted num" title="Measured now: the runtime and its llama.cpp processes">Memory {fmtBytes(mem.ram_bytes)}{mem.gpu_bytes ? `, GPU ${fmtBytes(mem.gpu_bytes)}` : ''}</span>{/if}
      {#if gpu && !mem?.gpu_bytes}<span class="muted num" title="Memory in use on the whole GPU, by every program">GPU {Math.round(gpu.used_mib / 1024)} of {Math.round(gpu.total_mib / 1024)} GiB in use</span>{/if}
      <span class="grow"></span>
      <button class="btn" onclick={stop}>Stop</button>
    {:else if starting || health === 'starting' || health === 'loading'}
      <span>Starting the model</span><span class="muted ellipsis grow">{log.split('\n').filter(Boolean).at(-1) ?? ''}</span>
      <button class="btn" onclick={stop}>Stop</button>
    {:else}
      <span class="muted">{canRun ? 'The model is not running.' : why}</span>
      <span class="grow"></span>
      <button class="btn primary" disabled={!canRun} onclick={start}>Start</button>
    {/if}
  </div>
  {#if health === 'stopped' && log && !starting}<pre class="log">{log}</pre>{/if}

  <div class="turns scroll" bind:this={list}>
    {#each turns as t, i (i)}
      <div class="turn">
        <p class="q">{t.q}</p>
        {#if t.a}<Answer text={t.a} />{:else if t.busy}<p class="muted">{t.ev.nodes.length ? `Read ${read(t).length} nodes, answering` : 'Searching the network'}</p>{/if}
        {#if t.error}<p class="error-line">{t.error}</p>{/if}
        {#if !t.busy && !t.error && t.ev.citations.length + t.ev.tools.length + t.ev.nodes.length > 0}
          <details class="ev">
            <summary>{summary(t)}</summary>
            <div class="evb">
              {#if t.ev.citations.length}
                <table>
                  <tbody>
                    {#each t.ev.citations as c (c.n)}
                      <tr><td class="num muted">[{c.n}]</td><td>{#if c.source?.url}<a href={c.source.url}>{c.title}</a>{:else}{c.title}{/if}</td><td class="muted">{c.cited ? 'cited' : 'not cited'}</td></tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
              {#if t.ev.nodes.length > read(t).length}
                <p class="muted">Also selected without being read: {t.ev.nodes.filter((n) => !n.read_as?.includes('text')).map((n) => n.title).join(', ')}</p>
              {/if}
              {#each t.ev.tools as tl}<p><span class="mono">{tl.name}</span> <span class="muted">{tl.result_summary}</span></p>{/each}
              {#each t.ev.checks.filter((c) => !c.ok) as c}<p class="problem">{c.detail}</p>{/each}
              {#if t.ev.timing}
                <p class="muted num">
                  Routing {secs(t.ev.timing.route_ms)}, first token {secs(t.ev.timing.ttft_ms)}, total {secs(t.ev.timing.total_ms)}{t.ev.timing.tokens_per_s ? `, ${t.ev.timing.tokens_per_s} tokens/s` : ''}{t.ev.usage ? `, ${t.ev.usage.prompt_tokens} prompt and ${t.ev.usage.completion_tokens} answer tokens` : ''}
                </p>
              {/if}
            </div>
          </details>
        {/if}
      </div>
    {/each}
  </div>

  <form class="ask row" onsubmit={(e) => (e.preventDefault(), ask())}>
    <input class="field" bind:value={q} placeholder={health === 'ok' ? 'Ask the model' : 'Start the model to ask it'} disabled={health !== 'ok'} aria-label="Question" />
    {#if ctrl}
      <button type="button" class="btn" onclick={() => ctrl?.abort()}>Stop answer</button>
    {:else}
      <button type="submit" class="btn primary" disabled={health !== 'ok' || !q.trim()}>Ask</button>
    {/if}
  </form>
</div>

<style>
  .chat {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-height: 0;
  }
  .runline {
    gap: 12px;
    min-height: var(--ctl);
    flex-wrap: wrap;
  }
  .log {
    margin: 0;
    max-height: 160px;
    overflow: auto;
    font: 12px/16px var(--mono);
    color: var(--muted);
    white-space: pre-wrap;
  }
  .turns {
    max-height: 520px;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .turns:empty {
    display: none;
  }
  .turn {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .q {
    font-weight: 500;
  }
  .ev summary {
    cursor: pointer;
    color: var(--muted);
    font-size: 12px;
  }
  .ev summary:hover {
    color: var(--white);
  }
  .evb {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px 0 0 14px;
    font-size: 12px;
  }
  .evb table {
    border-collapse: collapse;
    width: auto;
    align-self: flex-start;
  }
  .evb td {
    padding: 2px 10px 2px 0;
    vertical-align: top;
  }
  .ask .field {
    height: 32px;
  }
</style>
