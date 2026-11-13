<script lang="ts">
  // A model's answer with the little Markdown cores write (bold, inline code, bullet lists, headings) shown as
  // formatting instead of asterisks. Everything is rendered as text nodes: nothing in an answer becomes HTML.
  type Seg = { t: string; b?: boolean; c?: boolean };
  type Block = { k: 'p' | 'h' | 'gap'; s: Seg[] } | { k: 'ul'; items: Seg[][] };

  let { text }: { text: string } = $props();

  function inline(s: string): Seg[] {
    const out: Seg[] = [];
    for (const part of s.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/)) {
      if (!part) continue;
      if (part.length > 4 && part.startsWith('**') && part.endsWith('**')) out.push({ t: part.slice(2, -2), b: true });
      else if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) out.push({ t: part.slice(1, -1), c: true });
      else out.push({ t: part });
    }
    return out;
  }

  const blocks = $derived.by(() => {
    const out: Block[] = [];
    for (const line of text.split('\n')) {
      const li = /^\s*(?:[*+-]|\d+\.)\s+(.*)$/.exec(line);
      const h = /^\s*#{1,4}\s+(.*)$/.exec(line);
      const last = out.at(-1);
      if (li) {
        if (last?.k === 'ul') last.items.push(inline(li[1]));
        else out.push({ k: 'ul', items: [inline(li[1])] });
      } else if (h) out.push({ k: 'h', s: inline(h[1]) });
      else if (!line.trim()) {
        if (last && last.k !== 'gap') out.push({ k: 'gap', s: [] });
      } else out.push({ k: 'p', s: inline(line) });
    }
    return out;
  });
</script>

{#snippet segs(list: Seg[])}{#each list as s, i (i)}{#if s.b}<strong>{s.t}</strong>{:else if s.c}<code>{s.t}</code>{:else}{s.t}{/if}{/each}{/snippet}

<div class="answer">
  {#each blocks as b, i (i)}
    {#if b.k === 'ul'}
      <ul>{#each b.items as it, j (j)}<li>{@render segs(it)}</li>{/each}</ul>
    {:else if b.k === 'h'}
      <p class="h">{@render segs(b.s)}</p>
    {:else if b.k === 'gap'}
      <div class="gap"></div>
    {:else}
      <p>{@render segs(b.s)}</p>
    {/if}
  {/each}
</div>

<style>
  .answer {
    line-height: 20px;
    font-size: 14px;
    overflow-wrap: anywhere;
  }
  p {
    margin: 0;
  }
  .h {
    font-weight: 600;
  }
  .gap {
    height: 10px;
  }
  ul {
    margin: 2px 0;
    padding-left: 20px;
  }
  li + li {
    margin-top: 2px;
  }
  strong {
    font-weight: 600;
  }
  code {
    font: 13px/18px var(--mono);
  }
</style>
