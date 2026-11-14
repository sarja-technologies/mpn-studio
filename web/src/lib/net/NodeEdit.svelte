<script lang="ts">
  // Edit one node of a fork. Only the fields that changed are sent.
  import Icon from '../ui/Icon.svelte';
  import { submitOnCtrlEnter } from '../ui/actions';
  import { DOMAINS, KINDS, net, word } from './net.svelte';

  let { node, ondone }: { node: any; ondone: () => void } = $props();

  const stmts = (n: any): string[] => (n.cells?.knowledge?.statements ?? []).map((s: any) => (typeof s === 'string' ? s : s?.text ?? '')).filter(Boolean);
  // the form starts from the node as it was when Edit was pressed
  const start = (() => ({
    title: node.title ?? '',
    summary: node.summary ?? '',
    aliases: (node.aliases ?? []).join(', '),
    domain: node.domain ?? 'general',
    kind: node.kind ?? 'concept',
    statements: stmts(node),
    notes: node.notes ?? '',
  }))();
  let f = $state({ ...start, statements: [...start.statements] });
  let saving = $state(false);
  let titleEl: HTMLInputElement;

  const split = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean);
  function changes() {
    const out: Record<string, any> = {};
    if (f.title.trim() !== start.title) out.title = f.title.trim();
    if (f.summary.trim() !== start.summary.trim()) out.summary = f.summary.trim();
    if (split(f.aliases).join('\u0000') !== split(start.aliases).join('\u0000')) out.aliases = split(f.aliases);
    if (f.domain !== start.domain) out.domain = f.domain;
    if (f.kind !== start.kind) out.kind = f.kind;
    const st = f.statements.map((s) => s.trim()).filter(Boolean);
    if (st.join('\u0000') !== start.statements.join('\u0000')) out.statements = st;
    if (f.notes.trim() !== start.notes.trim()) out.notes = f.notes.trim();
    return out;
  }
  const dirty = $derived(Object.keys(changes()).length > 0);
  // a kind outside the editable list (a claim) stays as it is
  const kinds = $derived(KINDS.includes(start.kind) ? KINDS : [start.kind, ...KINDS]);

  async function save(e: SubmitEvent) {
    e.preventDefault();
    if (!f.title.trim()) return titleEl.focus();
    const ch = changes();
    if (!Object.keys(ch).length) return ondone();
    saving = true;
    const ok = await net.save(node.id, ch);
    saving = false;
    if (ok) ondone();
  }
  function key(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.stopPropagation();
      ondone();
    }
  }
  $effect(() => titleEl?.focus());
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<form class="ed" onsubmit={save} onkeydown={key} use:submitOnCtrlEnter aria-label="Edit node">
  <div class="bar">
    <span class="h grow">Edit node</span>
    <button type="button" class="btn" onclick={ondone} disabled={saving}>Cancel</button>
    <button class="btn primary" disabled={saving || !dirty || !f.title.trim()}>{saving ? 'Saving…' : 'Save'}</button>
  </div>
  <div>
    <label class="lbl" for="ne-title">Title</label>
    <input id="ne-title" class="field" class:bad={!f.title.trim()} bind:this={titleEl} bind:value={f.title} maxlength="500" />
  </div>
  <div>
    <label class="lbl" for="ne-summary">Summary</label>
    <textarea id="ne-summary" class="field" rows="3" bind:value={f.summary} maxlength="4000"></textarea>
  </div>
  <div>
    <label class="lbl" for="ne-aliases">Also known as, separated by commas</label>
    <input id="ne-aliases" class="field" bind:value={f.aliases} />
  </div>
  <div class="two">
    <div>
      <label class="lbl" for="ne-domain">Domain</label>
      <select id="ne-domain" class="field" bind:value={f.domain}>
        {#each DOMAINS as d (d)}<option value={d}>{word(d)}</option>{/each}
      </select>
    </div>
    <div>
      <label class="lbl" for="ne-kind">Kind</label>
      <select id="ne-kind" class="field" bind:value={f.kind}>
        {#each kinds as k (k)}<option value={k}>{word(k)}</option>{/each}
      </select>
    </div>
  </div>
  <fieldset>
    <legend class="lbl">Statements</legend>
    {#each f.statements as _, i (i)}
      <div class="st">
        <textarea class="field grow" rows="2" bind:value={f.statements[i]} aria-label={`Statement ${i + 1}`}></textarea>
        <button type="button" class="btn icon quiet" aria-label={`Remove statement ${i + 1}`} title="Remove" onclick={() => f.statements.splice(i, 1)}><Icon name="close" size={14} /></button>
      </div>
    {/each}
    <button type="button" class="btn add" onclick={() => f.statements.push('')}>Add statement</button>
  </fieldset>
  <div>
    <label class="lbl" for="ne-notes">Notes</label>
    <textarea id="ne-notes" class="field" rows="3" bind:value={f.notes} placeholder="Only in this fork"></textarea>
  </div>
</form>

<style>
  .ed {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .bar {
    position: sticky;
    top: -10px;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 0;
    margin-top: -8px;
    background: var(--surface);
    border-bottom: 1px solid var(--line);
  }
  .h {
    font-size: 15px;
    font-weight: 500;
  }
  .two {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  fieldset {
    border: 0;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  legend {
    padding: 0;
  }
  .st {
    display: flex;
    gap: 4px;
    align-items: flex-start;
  }
  .st .btn {
    color: var(--muted);
  }
  .st .btn:hover {
    color: var(--white);
  }
  .add {
    align-self: flex-start;
  }
</style>
