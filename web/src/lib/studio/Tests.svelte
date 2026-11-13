<script lang="ts">
  import { errText, post } from '../api';
  import { openInFileManager } from '../desktop.svelte';
  import { studio, pct, type TaskView } from './studio.svelte';

  const choices = $derived([
    ...(studio.data?.models ?? []).filter((m) => m.status.state === 'ready').map((m) => ({ key: m.id, name: m.name })),
    ...(studio.data?.opened ?? []).map((o) => ({ key: o.key, name: `${o.name} (file)` })),
  ]);
  const nameOf = (k: string) => choices.find((c) => c.key === k)?.name ?? k;
  let picked = $state<string[]>([]);
  let text = $state('');
  let task = $state.raw<TaskView | null>(null);
  let error = $state<string | null>(null);
  let open = $state<string | null>(null);

  // one question per line; after a "|" come the accepted answers, separated by "|"
  const questions = $derived(
    text
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const [q, ...exp] = l.split('|').map((s) => s.trim());
        return { question: q, expect: exp.filter(Boolean) };
      })
      .filter((x) => x.question),
  );
  const scored = $derived(questions.filter((q) => q.expect.length).length);

  async function run() {
    error = null;
    task = null;
    const t = await studio.follow(post<TaskView>('api/library/bench', { models: picked, questions }), (t) => (task = t)).catch((e) => ((error = errText(e)), null));
    if (t && t.state !== 'done') error = t.error;
  }
  const secs = (ms: number) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
  const dir = (p: string) => p.replace(/\/[^/]+$/, '');
</script>

<div class="wrap">
  <h1>Tests</h1>
  {#if !choices.length}
    <p class="muted">Build a model, or open a .mpn file, to test it here.</p>
  {:else}
    <div class="cols">
      <section>
        <span class="lbl">Models</span>
        {#each choices as c (c.key)}
          <label class="ck"><input type="checkbox" value={c.key} bind:group={picked} />{c.name}</label>
        {/each}
      </section>
      <section class="qs">
        <label class="lbl" for="t-q">Questions, one per line. After a | put the accepted answers, separated by |</label>
        <textarea id="t-q" class="field mono" rows="8" bind:value={text} placeholder={'What is the capital of Australia? | Canberra\nWho wrote the first published algorithm for a machine? | Ada Lovelace | Lovelace\nWhat is 17% of 2350? | 399.5'}></textarea>
        <div class="row">
          <span class="muted grow">{questions.length} {questions.length === 1 ? 'question' : 'questions'}, {scored} with accepted answers</span>
          {#if task?.state === 'running'}
            <button class="btn" onclick={() => studio.cancel(task!.id)}>Stop</button>
          {:else}
            <button class="btn primary" disabled={!picked.length || !questions.length} onclick={run}>Run test</button>
          {/if}
        </div>
      </section>
    </div>
  {/if}

  {#if task?.state === 'running'}
    <div class="bar"><i style="width:{pct(task) ?? 0}%"></i></div>
    <p class="muted small">{task.progress.label || 'Starting the models'}</p>
  {/if}
  {#if error}<p class="error-line">{error}</p>{/if}

  {#if task?.state === 'done'}
    {@const r = task.result}
    <table class="list">
      <thead><tr><th>Model</th><th class="r">Correct</th><th class="r">Median answer time</th><th class="r hide-narrow">Median routing</th></tr></thead>
      <tbody>
        {#each Object.entries(r.models) as [k, m] (k)}
          {@const mm = m as any}
          <tr>
            <td><button class="rowname" onclick={() => (open = open === k ? null : k)}>{nameOf(k)}</button></td>
            <td class="r num">{mm.scored ? `${mm.correct} of ${mm.scored}` : 'not scored'}</td>
            <td class="r num">{secs(mm.median_total_ms)}</td>
            <td class="r num hide-narrow">{secs(mm.median_route_ms)}</td>
          </tr>
          {#if open === k}
            <tr class="detail">
              <td colspan="4">
                {#each mm.rows as x}
                  <div class="qa">
                    <p><strong>{x.question}</strong> <span class="muted">{x.expect?.length ? (x.correct ? 'correct' : `expected ${x.expect.join(' or ')}`) : ''}</span></p>
                    {#if x.error}<p class="error-line">{x.error}</p>{:else}<p>{x.answer}</p>{/if}
                    <p class="muted small">{x.cited.length ? `Cited ${x.cited.join(', ')}` : 'No node cited'}, {secs(x.total_ms)}</p>
                  </div>
                {/each}
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
    <div class="row">
      <span class="muted small grow">Saved as <code>{r.saved}</code> and <code>{r.saved_csv}</code></span>
      <button class="btn" onclick={() => openInFileManager(dir(r.saved))}>Show in file manager</button>
    </div>
  {/if}
</div>

<style>
  .cols {
    display: grid;
    grid-template-columns: minmax(200px, 1fr) minmax(0, 2fr);
    gap: 32px;
  }
  section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .ck {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 26px;
    cursor: pointer;
  }
  .detail td {
    background: #0c0c0c;
  }
  .qa {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 6px 0 10px;
  }
  @media (max-width: 760px) {
    .cols {
      grid-template-columns: 1fr;
    }
  }
</style>
