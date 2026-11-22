<script lang="ts">
  import { app } from '../state.svelte';
  import { desk } from '../desktop.svelte';
  import { del, errText, fmtBytes, fmtInt, post } from '../api';
  import { catalogue, when, type InstalledRelease } from './catalogue.svelte';
  import { studio, pct } from './studio.svelte';

  const rows = $derived((catalogue.data?.installed ?? []).filter((r) => r.kind === 'mpn' && r.format === 'fgmpn'));
  const rt = $derived(catalogue.data?.fgmpn);
  let busy = $state<Record<string, string>>({});
  let error = $state<string | null>(null);
  let menuFor = $state<string | null>(null);
  const key = (r: InstalledRelease) => r.id + ':' + r.version;

  async function open(r: InstalledRelease) {
    error = null;
    busy = { ...busy, [key(r)]: 'Opening' };
    try {
      const where = await post<{ path: string; name: string }>(`api/releases/${r.id}/${r.version}/open`);
      const existing = desk.info?.projects.find((p: any) => p.kind === 'linked' && p.network?.sources?.some((s: any) => s.mode === 'fgmpn' && s.target === where.path));
      const id = existing?.id ?? (await post<{ id: string }>('api/projects/link', { name: r.name, sources: [{ mode: 'fgmpn', target: where.path }], open: false })).id;
      await desk.openProject(id);
    } catch (e) {
      error = errText(e);
    }
    busy = { ...busy, [key(r)]: '' };
  }
  async function verify(r: InstalledRelease) {
    menuFor = null;
    error = null;
    busy = { ...busy, [key(r)]: 'Verifying' };
    const t = await studio.follow(post('api/releases/verify', { kind: r.kind, id: r.id, version: r.version, format: r.format }), (t) => (busy = { ...busy, [key(r)]: `Verifying ${pct(t) ?? 0}%` }));
    busy = { ...busy, [key(r)]: '' };
    await catalogue.load();
    if (t.state !== 'done') error = t.error ?? 'The check did not finish.';
    else if (t.result?.ok) app.toast(`${r.name}: every file matches its checksum.`);
    else error = `${r.name} does not verify: ${(t.result?.problems ?? []).slice(0, 3).join('; ')}. Install it again to repair it.`;
  }
  async function remove(r: InstalledRelease) {
    menuFor = null;
    try {
      const out = await del<any>(`api/releases/mpn/${r.id}/${r.version}?format=${r.format ?? ''}`);
      await Promise.all([catalogue.load(), studio.load()]);
      app.toast(`${r.name} was moved to ${out.moved_to}.`);
    } catch (e) {
      error = errText(e);
    }
  }
</script>

{#if rows.length}
  <section class="inst">
    <h2>Installed FG-MPN networks</h2>
    <p class="muted small">
      Read-only, opened in the network explorer
      {#if rt && !rt.available}; <strong>opening needs the FG-MPN runtime</strong>. {rt.reason}{:else if rt}through FG-MPN {rt.version}.{/if}
    </p>
    {#if error}<p class="error-line">{error}</p>{/if}
    <table class="list">
      <thead><tr><th>Network</th><th class="r hide-narrow">Nodes</th><th class="hide-narrow">Format</th><th class="r hide-narrow">On disk</th><th class="hide-narrow">Licence</th><th></th></tr></thead>
      <tbody>
        {#each rows as r (key(r))}
          <tr>
            <td>
              <span class="nm">{r.name}</span> <span class="muted">{r.origin === 'local' ? 'from this computer' : r.version}</span>
              <div class="muted small">{r.verified_at ? `Verified ${when(r.verified_at)}` : `Installed ${when(r.installed_at)}`}{r.store_path ? `, used where it is: ${r.store_path}` : ''}</div>
              {#each r.warnings ?? [] as w}<p class="problem small">{w}</p>{/each}
            </td>
            <td class="r num hide-narrow">{r.nodes != null ? fmtInt(r.nodes) : ''}</td>
            <td class="hide-narrow">FG-MPN{r.format_version ? ` ${r.format_version}` : ''}</td>
            <td class="r num hide-narrow">{r.bytes ? fmtBytes(r.bytes) : ''}</td>
            <td class="hide-narrow" title={r.licence?.notes ?? ''}>{r.licence?.spdx ?? r.licence?.label ?? 'not stated'}</td>
            <td class="act">
              {#if busy[key(r)]}<span class="muted">{busy[key(r)]}</span>{/if}
              <button class="btn primary" disabled={!!busy[key(r)] || (rt && !rt.available)} title={rt && !rt.available ? rt.reason ?? '' : ''} onclick={() => open(r)}>Explore</button>
              <div class="anchor">
                <button class="btn icon" aria-label="More actions for {r.name}" aria-haspopup="menu" aria-expanded={menuFor === key(r)} onclick={() => (menuFor = menuFor === key(r) ? null : key(r))}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="12.5" cy="8" r="1.2" /></svg>
                </button>
                {#if menuFor === key(r)}
                  <div class="pop menu" role="menu">
                    <button role="menuitem" onclick={() => verify(r)}>Verify every file</button>
                    <hr />
                    <button role="menuitem" onclick={() => remove(r)}>{r.store_path ? 'Remove from the list' : 'Move to trash'}</button>
                  </div>
                {/if}
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </section>
{/if}

<style>
  .inst {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .nm {
    font-size: 14px;
    font-weight: 500;
  }
</style>
