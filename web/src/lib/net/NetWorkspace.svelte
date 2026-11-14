<script lang="ts">
  // The workspace body for a linked network or a fork: explorer (left), canvas (centre), inspector (right), status bar.
  // OWNED BY the network-workspace work.
  import { onMount } from 'svelte';
  import { fmtBytes, fmtInt } from '../api';
  import Modal from '../ui/Modal.svelte';
  import { submitOnCtrlEnter } from '../ui/actions';
  import ConnectPanel from './ConnectPanel.svelte';
  import NetCanvas from './NetCanvas.svelte';
  import NetExplorer from './NetExplorer.svelte';
  import NetInspector from './NetInspector.svelte';
  import NetSources from './NetSources.svelte';
  import { DOMAINS, KINDS, net, word } from './net.svelte';

  let { onduplicate }: { onduplicate: () => void } = $props();

  // ---------------------------------------------------------------- left pane width (drag its edge)
  const KEY = 'mpn.net.left';
  let left = $state(readWidth());
  function readWidth() {
    try {
      const v = Number(localStorage.getItem(KEY));
      return v >= 220 && v <= 560 ? v : 300;
    } catch {
      return 300;
    }
  }
  function resizeLeft(e: PointerEvent) {
    const el = e.currentTarget as HTMLElement;
    el.setPointerCapture(e.pointerId);
    const x0 = e.clientX, w0 = left;
    const mv = (ev: PointerEvent) => (left = Math.max(220, Math.min(560, w0 + ev.clientX - x0)));
    const up = () => {
      el.removeEventListener('pointermove', mv);
      el.removeEventListener('pointerup', up);
      try {
        localStorage.setItem(KEY, String(left));
      } catch {
        /* the width just isn't remembered */
      }
    };
    el.addEventListener('pointermove', mv);
    el.addEventListener('pointerup', up);
  }
  function keyLeft(e: KeyboardEvent) {
    if (e.key === 'ArrowLeft') left = Math.max(220, left - 16);
    else if (e.key === 'ArrowRight') left = Math.min(560, left + 16);
  }

  // ---------------------------------------------------------------- add node
  let add = $state({ title: '', domain: 'general', kind: 'concept', summary: '' });
  let addBusy = $state(false);
  function openAdd() {
    if (!net.editable) return;
    const d = net.detail?.node?.domain;
    add = { title: '', domain: d && DOMAINS.includes(d) ? d : net.domain || 'general', kind: 'concept', summary: '' };
    net.dialog = 'add';
  }
  async function submitAdd(e: SubmitEvent) {
    e.preventDefault();
    if (!add.title.trim()) return;
    addBusy = true;
    const ok = await net.create({ title: add.title.trim(), domain: add.domain, kind: add.kind, summary: add.summary.trim() || undefined });
    addBusy = false;
    if (ok) net.dialog = null;
  }

  // ---------------------------------------------------------------- delete, connect
  let delBusy = $state(false);
  async function confirmDelete() {
    const id = net.detail?.node?.id;
    if (!id) return;
    delBusy = true;
    const ok = await net.remove(id);
    delBusy = false;
    if (ok) net.dialog = null;
  }
  function startConnect() {
    const n = net.detail?.node;
    if (!net.editable || !n) return;
    net.connecting = { src: n.id, srcTitle: n.title, dst: null, dstTitle: null };
    if (!net.canvas?.has(n.id)) net.canvas?.root(n.id);
  }

  // ---------------------------------------------------------------- keyboard
  function editableTarget(t: EventTarget | null) {
    const el = t as HTMLElement | null;
    return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
  }
  function onKey(e: KeyboardEvent) {
    if (e.defaultPrevented || net.dialog || document.querySelector('dialog[open]')) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      net.focusSearch();
      return;
    }
    if (mod || e.altKey || editableTarget(e.target)) return;
    if (e.key === '/') {
      e.preventDefault();
      net.focusSearch();
    } else if (e.key === 'Escape') {
      if (net.connecting) net.connecting = null;
      else if (net.sel) net.select(null);
      else net.canvas?.up();
    } else if ((e.key === 'n' || e.key === 'N') && net.editable) {
      e.preventDefault();
      openAdd();
    } else if ((e.key === 'c' || e.key === 'C') && net.editable && net.detail?.node) {
      e.preventDefault();
      startConnect();
    } else if (e.key === 'Delete' && net.editable && net.detail?.node && net.sel) {
      e.preventDefault();
      net.dialog = 'delete';
    }
  }

  // ---------------------------------------------------------------- status bar
  const ms = (v: number | null | undefined) => (v == null ? null : v < 1 ? 'under 1 ms' : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(2)} s`);
  const rss = $derived(net.res?.process_rss ?? net.state?.process_rss ?? null);
  let poll: ReturnType<typeof setInterval> | null = null;

  onMount(() => {
    net.start();
    poll = setInterval(() => document.visibilityState === 'visible' && net.state && net.loadResources(), 4000);
    return () => {
      if (poll) clearInterval(poll);
      net.stop();
    };
  });
</script>

<svelte:window onkeydown={onKey} />

<div class="nw">
  <div class="body">
    <div class="left" style="width:clamp(220px, calc(100vw - var(--right) - 380px), {left}px)"><NetExplorer /></div>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <div class="edge" role="separator" aria-orientation="vertical" aria-label="Resize the explore pane" aria-valuenow={left} aria-valuemin={220} aria-valuemax={560}
      tabindex="0" onpointerdown={resizeLeft} onkeydown={keyLeft}></div>

    <main class="centre">
      {#if net.ovErr && !net.ov}
        <div class="down">
          <h2>This network cannot be read right now</h2>
          <p>{net.ovErr}</p>
          <button class="btn primary" disabled={net.busy} onclick={() => net.useSource(null)}>{net.busy ? 'Trying…' : 'Try again'}</button>
          <div class="srcs"><NetSources /></div>
        </div>
      {:else}
        <NetCanvas onadd={openAdd} onconnect={startConnect} />
        {#if net.connecting}<ConnectPanel />{/if}
      {/if}
    </main>

    <div class="right"><NetInspector {onduplicate} /></div>
  </div>

  <footer class="status">
    {#if net.state?.source}<span class="ellipsis" title={net.state.source.target}>{net.state.source.label}</span>{:else if net.state}<span>No source connected</span>{/if}
    {#if net.ov}<span class="num">{fmtInt(net.ov.validated_nodes)} nodes</span>{/if}
    {#if ms(net.res?.last_query_ms ?? net.ov?.last_query_ms)}<span class="num">Last query {ms(net.res?.last_query_ms ?? net.ov?.last_query_ms)}</span>{/if}
    {#if rss != null}<span class="num">Memory {fmtBytes(rss)}</span>{/if}
  </footer>
</div>

{#if net.dialog === 'add'}
  <Modal title="Add node" onclose={() => (net.dialog = null)} width={420}>
    <form class="form" onsubmit={submitAdd} use:submitOnCtrlEnter>
      <div>
        <label class="lbl" for="an-title">Title</label>
        <!-- svelte-ignore a11y_autofocus -->
        <input id="an-title" class="field" bind:value={add.title} maxlength="500" autofocus autocomplete="off" />
      </div>
      <div class="two">
        <div>
          <label class="lbl" for="an-domain">Domain</label>
          <select id="an-domain" class="field" bind:value={add.domain}>{#each DOMAINS as d (d)}<option value={d}>{word(d)}</option>{/each}</select>
        </div>
        <div>
          <label class="lbl" for="an-kind">Kind</label>
          <select id="an-kind" class="field" bind:value={add.kind}>{#each KINDS as k (k)}<option value={k}>{word(k)}</option>{/each}</select>
        </div>
      </div>
      <div>
        <label class="lbl" for="an-summary">Summary</label>
        <textarea id="an-summary" class="field" rows="3" bind:value={add.summary} maxlength="4000"></textarea>
      </div>
      <div class="row end">
        <button type="button" class="btn" onclick={() => (net.dialog = null)}>Cancel</button>
        <button class="btn primary" disabled={!add.title.trim() || addBusy}>{addBusy ? 'Adding…' : 'Add node'}</button>
      </div>
    </form>
  </Modal>
{:else if net.dialog === 'delete' && net.detail?.node}
  <Modal title="Delete “{net.detail.node.title}”?" onclose={() => (net.dialog = null)} width={420}>
    <p>The node and its relationships disappear from this fork. The protected original keeps it, and Undo brings it back.</p>
    <div class="row end">
      <button class="btn" onclick={() => (net.dialog = null)}>Cancel</button>
      <button class="btn danger" disabled={delBusy} onclick={confirmDelete}>{delBusy ? 'Deleting…' : 'Delete node'}</button>
    </div>
  </Modal>
{/if}

<style>
  .nw {
    --right: 360px;
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    min-width: 0;
  }
  .body {
    display: flex;
    flex: 1;
    min-height: 0;
    min-width: 0;
  }
  .left {
    flex: none;
    min-width: 0;
    min-height: 0;
  }
  .edge {
    flex: none;
    width: 5px;
    margin: 0 -2px;
    position: relative;
    z-index: 3;
    cursor: col-resize;
  }
  .edge::before {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 2px;
    width: 1px;
    background: var(--line);
  }
  .edge:hover::before,
  .edge:focus-visible::before {
    background: #6a6a6a;
  }
  .edge:focus-visible {
    outline: none;
  }
  .centre {
    position: relative;
    flex: 1;
    min-width: 0;
    min-height: 0;
    display: flex;
  }
  .right {
    flex: none;
    width: var(--right);
    min-height: 0;
    border-left: 1px solid var(--line);
  }
  .down {
    margin: auto;
    width: min(520px, calc(100% - 32px));
    display: flex;
    flex-direction: column;
    gap: 10px;
    align-items: flex-start;
  }
  .down h2 {
    font-size: 16px;
    font-weight: 500;
  }
  .down p {
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .srcs {
    width: 100%;
    margin-top: 8px;
    padding-top: 12px;
    border-top: 1px solid var(--line-soft);
  }
  .status {
    flex: none;
    height: 24px;
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 0 10px;
    background: var(--surface);
    border-top: 1px solid var(--line);
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
  }
  .status > span {
    flex: none;
  }
  .status > span.ellipsis {
    flex: 0 1 auto;
    min-width: 0;
  }
  .form {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .two {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .end {
    justify-content: flex-end;
  }
  @media (max-width: 1280px) {
    .nw {
      --right: 320px;
    }
  }
  @media (max-width: 1100px) {
    .nw {
      --right: 300px;
    }
  }
</style>
