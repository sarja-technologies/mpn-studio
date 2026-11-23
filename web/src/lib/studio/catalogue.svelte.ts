// Official releases (fylume-release/1): the catalogue Studio reads, what is installed, and installs in progress.
import { get, post, del, errText } from '../api';
import { pickFile, pickFolder } from '../desktop.svelte';
import { app } from '../state.svelte';
import { studio, type TaskView } from './studio.svelte';

export interface Release {
  id: string;
  kind: 'mpn' | 'core' | 'model';
  name: string;
  version: string;
  released_at?: string | null;
  licence: { spdx: string | null; notes?: string | null; label?: string | null };
  summary?: string | null;
  links?: { release?: string | null; docs?: string | null };
  download_only?: { reason: string; url?: string | null; type?: string | null; filename?: string | null; bytes?: number | null; page?: string | null } | null;
  payloads?: { format: string; ok: boolean; reason: string | null; bytes: number; plan: Release['plan']; installed: boolean; project?: string | null }[];
  default_format?: string | null;
  hosting?: string;
  repo?: string;
  totals: { download_bytes: number; file_count: number };
  problems: string[];
  compat: { ok: boolean; reasons: string[] };
  origin?: 'catalogue' | 'file';
  manifest_sha256?: string | null;
  installed: { installed_at?: string; verified_at?: string; core_id?: string; project?: string; format?: string } | null;
  plan?: { files: number; download_bytes: number; remaining_bytes: number; free_bytes: number; enough_space: boolean } | null;
  mpn?: { format?: string; format_version?: string; nodes?: number; relationships?: number; read_only?: boolean; studio_min?: string; verification?: any };
  core?: { parameters?: number; architecture?: string; derivation?: string; context_length?: number; formats: { kind: string; quant?: string; file?: string; bytes?: number }[]; base_model?: { id?: string } };
  model?: { pairing?: string; blocked?: string; mpn: PinView; core: PinView; encoder?: PinView; defaults?: any; runtime?: any };
}
export interface PinView {
  id?: string | null;
  name?: string | null;
  version: string;
  installed: boolean;
  in_catalogue: boolean;
}
export interface InstalledRelease {
  kind: 'mpn' | 'core';
  id: string;
  name: string;
  version: string;
  format?: string;
  format_version?: string;
  nodes?: number;
  bytes?: number;
  read_only?: boolean;
  origin?: string;
  installed_at: string;
  verified_at?: string;
  licence?: { spdx: string | null; notes?: string | null; label?: string | null };
  store?: { nodes: number; segments: number };
  store_path?: string;
  project?: string;
  core_id?: string;
  warnings?: string[];
  path: string;
}
export interface CatalogueView {
  source: { url: string; fetched_at: number | null; skipped: string[]; local: number };
  default_url: string;
  releases: Release[];
  rejected: { id: string | null; reason: string }[];
  installed: InstalledRelease[];
  fgmpn: { available: boolean; version: string | null; engine: string | null; reason: string | null; supported: string[] };
  studio: string;
}
export interface InstallState {
  task: TaskView | null;
  error: string | null;
  done?: boolean;
}

export const rkey = (r: { kind: string; id: string; version: string }) => `${r.kind}:${r.id}:${r.version}`;

class Catalogue {
  data = $state.raw<CatalogueView | null>(null);
  error = $state<string | null>(null);
  refreshing = $state(false);
  installs = $state.raw<Record<string, InstallState>>({});
  pkg = $state.raw<InstallState | null>(null);                // a Yaob package being imported (folder or zip)

  async load() {
    try {
      this.data = await get<CatalogueView>('api/catalogue');
    } catch (e) {
      this.error = errText(e);
    }
  }
  async refresh() {
    if (this.refreshing) return;
    this.refreshing = true;
    this.error = null;
    try {
      const r = await post<CatalogueView & { fetched: number; skipped: string[] }>('api/catalogue/refresh');
      this.data = r;
      if (r.skipped.length) app.toast(`${r.fetched} releases read; ${r.skipped.length} could not be used.`, 'error');
    } catch (e) {
      this.error = errText(e);
      await this.load();
    }
    this.refreshing = false;
  }
  async importManifest() {
    const f = await pickFile('Choose a release manifest or catalogue file', ['json']);
    if (!f) return;
    try {
      const r = await post<CatalogueView & { added: { id: string; problems: string[] }[] }>('api/catalogue/import', { path: f });
      this.data = r;
      app.toast(`${r.added.length} release${r.added.length === 1 ? '' : 's'} added from the file.`);
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }
  /** A Yaob package (manifest.json fylume-package/1): verified, registered where it is, its pairing created. */
  async importPackage(how: 'folder' | 'zip') {
    if (this.pkg?.task?.state === 'running') return;
    const p = how === 'zip' ? await pickFile('Choose a Yaob package (.zip)', ['zip']) : await pickFolder('Choose the package folder (the one that contains manifest.json)');
    if (!p) return;
    this.pkg = { task: null, error: null };
    try {
      const t = await studio.follow(post<TaskView>('api/packages/import', { path: p, verify: 'full' }), (t) => (this.pkg = { task: t, error: null }));
      if (t.state === 'done') {
        this.pkg = { task: null, error: null, done: true };
        app.toast(t.result?.already ? 'That package was already imported.' : 'The package is imported. Its pairing is in Pairings.');
      } else if (t.state === 'cancelled') this.pkg = { task: null, error: 'Cancelled. Nothing was registered.' };
      else this.pkg = { task: null, error: t.error ?? 'The import did not finish.' };
    } catch (e) {
      this.pkg = { task: null, error: errText(e) };
    }
    await Promise.all([this.load(), studio.load()]);
  }
  cancelPackage() {
    const t = this.pkg?.task;
    if (t) studio.cancel(t.id);
  }
  async removeLocal(r: Release) {
    await del(`api/catalogue/local/${r.kind}/${r.id}/${r.version}`);
    await this.load();
  }
  set(key: string, s: InstallState) {
    this.installs = { ...this.installs, [key]: s };
  }
  async install(r: Release, opts: { file?: string; format?: string; core_endpoint?: string } = {}) {
    const key = rkey(r);
    this.set(key, { task: null, error: null });
    try {
      const t = await studio.follow(post<TaskView>('api/catalogue/install', { kind: r.kind, id: r.id, version: r.version, ...opts }), (t) => this.set(key, { task: t, error: null }));
      if (t.state === 'done') {
        this.set(key, { task: t, error: null, done: true });
        app.toast(`${r.name} is installed.`);
      } else if (t.state === 'cancelled') {
        this.set(key, { task: null, error: 'Cancelled. What was downloaded is kept; installing again continues from it.' });
      } else this.set(key, { task: null, error: t.error ?? 'The install did not finish.' });
    } catch (e) {
      this.set(key, { task: null, error: errText(e) });
    }
    await Promise.all([this.load(), studio.load()]);
  }
  cancel(r: Release) {
    const t = this.installs[rkey(r)]?.task;
    if (t) studio.cancel(t.id);
  }
  async verify(i: InstalledRelease) {
    const t = await studio.follow(post<TaskView>('api/releases/verify', { kind: i.kind, id: i.id, version: i.version }));
    await this.load();
    return t;
  }
}
export const catalogue = new Catalogue();

export const fmtGB = (n?: number | null) => (n == null ? '' : n >= 1e9 ? `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)} GB` : n >= 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.max(1, Math.round(n / 1e3))} kB`);
export const when = (s?: string | number | null) => (s ? new Date(typeof s === 'number' ? s * 1000 : s).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
