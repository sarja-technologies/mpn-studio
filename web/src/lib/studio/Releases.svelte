<script lang="ts">
  import { onMount } from 'svelte';
  import { fmtBytes, fmtInt } from '../api';
  import { desk } from '../desktop.svelte';
  import { catalogue, rkey, fmtGB, when, type Release } from './catalogue.svelte';
  import { studio, fmtParams, pct } from './studio.svelte';

  let { kind, title, note }: { kind: 'mpn' | 'core' | 'model'; title: string; note?: string } = $props();

  const all = $derived(catalogue.data?.releases.filter((r) => r.kind === kind) ?? []);
  // when several versions of one release exist, show the newest first and the others under it
  let picked = $state<Record<string, string>>({});          // release key -> chosen quantisation
  let server = $state<Record<string, string>>({});          // pairing key -> configured server to use instead of downloading a core
  const servers = $derived(studio.data?.core_servers ?? []);

  onMount(() => {
    if (!catalogue.data) catalogue.load();
  });

  const inst = (r: Release) => catalogue.installs[rkey(r)];
  const pairing = (r: Release) => (r.kind === 'model' ? studio.data?.models.find((m: any) => m.release?.id === r.id && m.release?.version === r.version) : undefined);
  const fmtOf = (r: Release) => picked[rkey(r)] ?? r.core?.formats[0]?.quant ?? '';
  // a network can carry two payloads: FG-MPN (explore, read-only) and a Studio network (answer in a pairing). Studio downloads only the one chosen.
  const usable = (r: Release) => (r.payloads ?? []).filter((p) => p.ok);
  const payOf = (r: Release) => usable(r).find((p) => p.format === (picked[rkey(r)] ?? r.default_format)) ?? usable(r)[0];
  const payName = (f: string) => (f === 'fgmpn' ? 'Explore, read-only (FG-MPN)' : f === 'studio-store' ? 'Use in pairings (Studio network)' : f);
  const sizeOf = (r: Release) =>
    r.kind === 'core' ? (r.core?.formats.find((f) => f.quant === fmtOf(r))?.bytes ?? r.totals.download_bytes)
    : r.kind === 'mpn' && payOf(r) ? payOf(r)!.bytes
    : r.download_only?.bytes ?? r.totals.download_bytes;
  const planOf = (r: Release) => (r.kind === 'mpn' ? payOf(r)?.plan : r.plan);
  const blocked = (r: Release) =>
    r.download_only ? '' : r.problems.length ? 'This release is not valid: ' + r.problems[0] + '.' : r.model?.blocked ? r.model.blocked : r.compat.reasons.join(' ');
  const space = (r: Release) => (planOf(r) && !planOf(r)!.enough_space ? `Needs ${fmtGB(planOf(r)!.remaining_bytes)}; ${fmtGB(planOf(r)!.free_bytes)} free.` : '');
  const installedHere = (r: Release) => (r.kind === 'mpn' ? !!payOf(r)?.installed : !!r.installed);
  const pinName = (p?: { id?: string | null; name?: string | null }) => p?.id ?? p?.name ?? '';
  const fetched = $derived(catalogue.data?.source.fetched_at);
  const url = $derived(catalogue.data?.source.url ?? '');

  function go(r: Release) {
    const opts: { file?: string; format?: string; core_endpoint?: string } = {};
    if (r.kind === 'core') opts.file = fmtOf(r);
    if (r.kind === 'mpn' && payOf(r)) opts.format = payOf(r)!.format;
    if (r.kind === 'model' && server[rkey(r)]) opts.core_endpoint = server[rkey(r)];
    catalogue.install(r, opts);
  }
</script>

<section class="rel">
  <div class="row head">
    <h2>{title}</h2>
    <span class="grow"></span>
    <button class="btn quiet" disabled={catalogue.refreshing} onclick={() => catalogue.refresh()}>{catalogue.refreshing ? 'Refreshing' : 'Refresh'}</button>
    <button class="btn quiet" onclick={() => catalogue.importManifest()}>Import manifest file</button>
    {#if kind === 'model'}
      <button class="btn quiet" disabled={catalogue.pkg?.task?.state === 'running'} onclick={() => catalogue.importPackage('folder')}>Import package folder</button>
      <button class="btn quiet" disabled={catalogue.pkg?.task?.state === 'running'} onclick={() => catalogue.importPackage('zip')}>Import package zip</button>
    {/if}
    <button class="btn quiet" onclick={() => (desk.settings = 'catalogue')}>Catalogue settings</button>
  </div>
  <p class="muted small src">
    {#if fetched}Read from {url}, {when(fetched)}.{:else}Catalogue {url}, not read yet.{/if}{#if note}<span>{" " + note}</span>{/if}
  </p>
  {#if catalogue.error}<p class="error-line">{catalogue.error}</p>{/if}
  {#if kind === 'model' && catalogue.pkg}
    {#if catalogue.pkg.task?.state === 'running'}
      <div class="row pk">
        <div class="bar grow"><i style="width:{pct(catalogue.pkg.task) ?? 0}%"></i></div>
        <span class="muted num">{pct(catalogue.pkg.task) ?? 0}%</span>
        <button class="btn" onclick={() => catalogue.cancelPackage()}>Cancel</button>
      </div>
      <p class="muted small">{catalogue.pkg.task.progress.label}</p>
    {:else if catalogue.pkg.error}
      <p class="error-line">{catalogue.pkg.error}</p>
    {/if}
  {/if}

  {#if all.length}
    <table class="list">
      <thead>
        <tr>
          <th>{kind === 'model' ? 'Pairing' : kind === 'core' ? 'Core' : 'Network'}</th>
          {#if kind === 'mpn'}<th class="r hide-narrow">Nodes</th><th class="hide-narrow">Format</th>{/if}
          {#if kind === 'core'}<th class="hide-narrow">Format</th>{/if}
          {#if kind === 'model'}<th class="hide-narrow">Network</th><th class="hide-narrow">Core</th>{/if}
          <th class="r">Download</th>
          <th class="hide-narrow">Licence</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each all as r (rkey(r))}
          {@const st = inst(r)}
          {@const run = st?.task?.state === 'running'}
          <tr>
            <td>
              <span class="nm">{r.name}</span> <span class="muted">{r.version}</span>
              {#if r.kind === 'mpn' && r.mpn && !r.download_only}<div class="muted small">{r.mpn.read_only ? 'Read-only' : 'Editable'}{r.mpn.relationships ? `, ${fmtInt(r.mpn.relationships)} relationships` : ''}</div>{/if}
              {#if r.download_only}<div class="muted small">Download only, not installable by Studio.</div>{/if}
              {#if r.kind === 'core' && r.core}<div class="muted small">{fmtParams(r.core.parameters)} parameters{r.core.architecture ? `, ${r.core.architecture}` : ''}{r.core.derivation ? `, ${r.core.derivation}` : ''}{r.core.base_model?.id ? ` of ${r.core.base_model.id}` : ''}</div>{/if}
              {#if r.origin === 'file'}<div class="muted small">From a file you imported. <button class="link" onclick={() => catalogue.removeLocal(r)}>Remove from the list</button></div>{/if}
            </td>
            {#if kind === 'mpn'}
              <td class="r num hide-narrow">{r.mpn?.nodes != null ? fmtInt(r.mpn.nodes) : ''}</td>
              <td class="hide-narrow fmt">
                {#if (r.payloads?.length ?? 0) > 1 && !r.download_only}
                  <select class="field inline" aria-label="What to install of {r.name}" value={payOf(r)?.format ?? ''} disabled={installedHere(r) || run} onchange={(e) => (picked = { ...picked, [rkey(r)]: e.currentTarget.value })}>
                    {#each usable(r) as p (p.format)}<option value={p.format}>{payName(p.format)}</option>{/each}
                  </select>
                {:else if r.download_only}
                  <span class="muted">{r.download_only.filename?.match(/\.(tar\.\w+|\w+)$/)?.[0] ?? 'download'}</span>
                {:else}
                  {r.mpn?.format === 'fgmpn' ? 'FG-MPN' : r.mpn?.format === 'studio-store' ? 'Studio network' : r.mpn?.format}{r.mpn?.format_version ? ` ${r.mpn.format_version}` : ''}
                {/if}
              </td>
            {/if}
            {#if kind === 'core'}
              <td class="hide-narrow">
                {#if r.core?.formats.length}
                  <select class="field inline" aria-label="Format of {r.name}" value={fmtOf(r)} disabled={!!r.installed || run} onchange={(e) => (picked = { ...picked, [rkey(r)]: e.currentTarget.value })}>
                    {#each r.core.formats.filter((f) => f.kind === 'gguf') as f (f.file)}<option value={f.quant}>{f.quant} {f.bytes ? `(${fmtGB(f.bytes)})` : ''}</option>{/each}
                  </select>
                {/if}
              </td>
            {/if}
            {#if kind === 'model'}
              <td class="hide-narrow">{#if r.download_only}<span class="muted">a single file</span>{:else}{pinName(r.model?.mpn)} <span class="muted">{r.model?.mpn.version}{r.model?.mpn.installed ? ', installed' : ''}</span>{/if}</td>
              <td class="hide-narrow">{#if r.download_only}<span class="muted">inside it</span>{:else}{pinName(r.model?.core)} <span class="muted">{r.model?.core.version}{r.model?.core.installed ? ', installed' : ''}</span>{#if r.model?.encoder}<div class="muted small">encoder {pinName(r.model.encoder)} {r.model.encoder.version}{r.model.encoder.installed ? ', installed' : ''}</div>{/if}{/if}</td>
            {/if}
            <td class="r num">{fmtGB(sizeOf(r))}</td>
            <td class="hide-narrow lic" title={r.licence.notes ?? ''}>{r.licence.spdx ?? r.licence.label ?? 'not stated'}</td>
            <td class="act">
              {#if run}
                <span class="muted num">{pct(st.task!) ?? 0}%</span>
                <button class="btn" onclick={() => catalogue.cancel(r)}>Cancel</button>
              {:else if r.download_only}
                {#if r.download_only.url}<a class="btn" href={r.download_only.url} target="_blank" rel="noopener noreferrer">{r.download_only.type === 'page' ? 'Open page' : 'Download'}</a>{/if}
              {:else if installedHere(r) && r.kind !== 'model'}
                <span class="muted">Installed</span>
              {:else if pairing(r)}
                <span class="muted">Installed</span>
                <button class="btn" onclick={() => (studio.model = pairing(r)!.id)}>Open</button>
              {:else}
                {#if r.kind === 'model' && servers.length}
                  <select class="field inline" aria-label="Core for {r.name}" value={server[rkey(r)] ?? ''} onchange={(e) => (server = { ...server, [rkey(r)]: e.currentTarget.value })}>
                    <option value="">Download its core</option>
                    {#each servers as s (s.id)}<option value={s.id}>Use {s.name}</option>{/each}
                  </select>
                {/if}
                <button class="btn primary" disabled={!!blocked(r) || !!space(r)} title={blocked(r) || space(r)} onclick={() => go(r)}>{st?.error ? 'Try again' : 'Install'}</button>
              {/if}
            </td>
          </tr>
          {#if run || st?.error || space(r) || blocked(r)}
            <tr class="sub">
              <td colspan="7">
                {#if run}
                  <div class="bar"><i style="width:{pct(st.task!) ?? 0}%"></i></div>
                  <p class="muted small">{st.task!.progress.label}{st.task!.progress.total ? `, ${fmtBytes(st.task!.progress.done)} of ${fmtBytes(st.task!.progress.total)}` : ''}</p>
                {:else if st?.error}
                  <p class={st.error.startsWith('Cancelled') ? 'muted small' : 'error-line'}>{st.error}</p>
                {:else if blocked(r)}
                  <p class="problem small">{blocked(r)}</p>
                {:else}
                  <p class="problem small">{space(r)} Free some space, then install; nothing has been downloaded.</p>
                {/if}
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  {:else}
    <p class="muted">
      {#if catalogue.data && !fetched && !catalogue.data.source.local}The catalogue has not been read yet. Refresh it, or import a release manifest file.{:else}The catalogue lists no {kind === 'model' ? 'pairings' : kind === 'core' ? 'cores' : 'networks'}.{/if}
    </p>
  {/if}
  {#if catalogue.data?.rejected.length}
    <p class="muted small">{catalogue.data.rejected.length} catalogue entr{catalogue.data.rejected.length === 1 ? 'y was' : 'ies were'} left out: {catalogue.data.rejected[0].reason}</p>
  {/if}
</section>

<style>
  .rel {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .head {
    gap: 8px;
    flex-wrap: wrap;
  }
  .pk {
    gap: 8px;
    align-items: center;
  }
  .src {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .nm {
    font-size: 14px;
    font-weight: 500;
  }
  .inline {
    width: auto;
    display: inline-block;
    height: 28px;
  }
  .sub td {
    padding-top: 0;
  }
  td.fmt,
  td.lic {
    white-space: nowrap;
  }
  td.lic {
    max-width: 170px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .sub p {
    margin: 4px 0 0;
  }
  a.btn {
    text-decoration: none;
    display: inline-flex;
    align-items: center;
  }
  .link {
    border: 0;
    background: none;
    padding: 0;
    color: inherit;
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
    font: inherit;
  }
</style>
