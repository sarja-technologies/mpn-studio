// State of the model builder: the library (models, cognitive cores, node networks), long-running tasks and the view.
import { get, post, patch, del, errText } from '../api';
import { pickFile, pickFolder } from '../desktop.svelte';
import { app } from '../state.svelte';

export interface CoreRow {
  id: string;
  kind: 'core' | 'encoder';
  name: string;
  sha256: string;
  present: boolean;
  path: string;
  bytes?: number;
  info: Record<string, any>;
  compatibility: { runnable: boolean; notes: string[]; llama_cpp?: string };
  projector?: any;
  source?: any;
  imported?: string;
}
export interface NetworkRow {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
  nodes?: number;
  mpns?: number;
  head?: number;
  bytes?: number;
  modified?: string;
  origin?: any;
  router?: { id: string; encoder?: { name?: string; sha256?: string } } | null;
  damaged?: boolean;
}
export interface ModelRow {
  id: string;
  name: string;
  description?: string;
  model_id: string;
  version?: string;
  core: { id: string; name: string; quantization?: string; parameters?: number; bytes?: number; license?: string } | null;
  encoder: { id: string; name: string } | null;
  network: { id: string } | null;
  identity: string | null;
  retrieval: Record<string, any>;
  context: { tokens: number; parallel: number; output_tokens: number };
  build?: any;
  status: { state: 'ready' | 'needs build' | 'incomplete'; problems: string[]; built?: string };
  run?: { health: string; port: number } | null;
  build_report?: any;
}
export interface TaskView {
  id: string;
  kind: string;
  title: string;
  state: 'running' | 'done' | 'failed' | 'cancelled';
  progress: { label: string; done: number; total: number };
  log: string[];
  result: any;
  error: string | null;
  cancellable: boolean;
}
export interface Overview {
  models: ModelRow[];
  cores: CoreRow[];
  networks: NetworkRow[];
  runtime: { ok: boolean; reason: string | null; variants: string[]; machine: string };
  runs: { key: string; health: string }[];
  opened: { key: string; file: string; name: string; summary: any; run: any }[];
  open_network: string | null;
}

export type Tab = 'models' | 'cores' | 'networks' | 'tests';

class Studio {
  tab = $state<Tab>('models');
  data = $state.raw<Overview | null>(null);
  error = $state<string | null>(null);
  /** the model (or opened .mpn key) shown in detail */
  model = $state<string | null>(null);
  tasks = $state.raw<Record<string, TaskView>>({});
  /** progress line while an .mpn file is being opened */
  opening = $state<string | null>(null);

  async load() {
    try {
      this.data = await get<Overview>('api/library/overview');
      this.error = null;
    } catch (e) {
      this.error = errText(e);
    }
  }
  core(id?: string | null) {
    return this.data?.cores.find((c) => c.id === id) ?? null;
  }
  network(id?: string | null) {
    return this.data?.networks.find((n) => n.id === id) ?? null;
  }
  get cores() {
    return (this.data?.cores ?? []).filter((c) => c.kind === 'core');
  }
  get encoders() {
    return (this.data?.cores ?? []).filter((c) => c.kind === 'encoder');
  }

  /** Start a task and follow it until it ends. Resolves with the final view; never throws for a task that failed. */
  async follow(start: Promise<TaskView>, onUpdate?: (t: TaskView) => void): Promise<TaskView> {
    let t = await start;
    this.tasks = { ...this.tasks, [t.id]: t };
    onUpdate?.(t);
    while (t.state === 'running') {
      await new Promise((r) => setTimeout(r, 400));
      try {
        t = await get<TaskView>(`api/library/tasks/${t.id}`);
      } catch (e) {
        t = { ...t, state: 'failed', error: errText(e) };
      }
      this.tasks = { ...this.tasks, [t.id]: t };
      onUpdate?.(t);
    }
    return t;
  }
  cancel(id: string) {
    return post(`api/library/tasks/${id}/cancel`).catch(() => {});
  }

  // ---------------------------------------------------------------- imports, each through the system file chooser
  async importGguf(onUpdate?: (t: TaskView) => void): Promise<CoreRow | null> {
    const f = await pickFile('Choose a GGUF model file', ['gguf']);
    if (!f) return null;
    const t = await this.follow(post<TaskView>('api/library/cores/import', { path: f }), onUpdate);
    await this.load();
    if (t.state !== 'done') throw new Error(t.error ?? 'The import did not finish.');
    return t.result;
  }
  async importHf(onUpdate?: (t: TaskView) => void, outtype = 'q8_0'): Promise<CoreRow | null> {
    const d = await pickFolder('Choose a Hugging Face model folder (with config.json and safetensors)');
    if (!d) return null;
    const info = await post<any>('api/library/cores/inspect', { path: d });
    if (info.kind !== 'hf') throw new Error('That folder is not a Hugging Face model folder (config.json is missing).');
    if (!info.convertible) throw new Error(info.notes?.join(' ') || 'This model cannot be converted.');
    if (!info.converter?.ready) throw new Error(info.converter?.reason ?? 'The converter is not ready.');
    const t = await this.follow(post<TaskView>('api/library/cores/import', { path: d, outtype }), onUpdate);
    await this.load();
    if (t.state !== 'done') throw new Error(t.error ?? 'The conversion did not finish.');
    return t.result;
  }
  async importNetwork(kind: 'auto' | 'yaob' | 'store' | 'bundle' = 'auto'): Promise<NetworkRow | null> {
    const p =
      kind === 'store'
        ? await pickFile('Choose a network store file', ['sqlite', 'db'])
        : await pickFolder(kind === 'yaob' ? 'Choose a Yaob release folder' : 'Choose a network folder (a bundle or a Yaob release folder)');
    if (!p) return null;
    const r = await post<NetworkRow>('api/library/networks/import', { kind, path: p });
    await this.load();
    return r;
  }
  async createModel(body: { name: string; core_id?: string | null; network_id?: string | null; encoder_id?: string | null; description?: string }) {
    const m = await post<ModelRow>('api/library/models', body);
    await this.load();
    return m;
  }
  async updateModel(id: string, body: Record<string, unknown>) {
    const m = await patch<ModelRow>(`api/library/models/${id}`, body);
    await this.load();
    return m;
  }
  /** Open a .mpn model file (picked, or handed over by the file manager) and show it. */
  async openMpn(file?: string) {
    const f = file ?? (await pickFile('Choose a model file', ['mpn']));
    if (!f) return;
    this.tab = 'models';
    try {
      const t = await this.follow(post('api/library/mpn/open', { path: f }), (t) => (this.opening = t.progress.label ? `${t.title}: ${t.progress.label} ${pct(t) ?? ''}%` : t.title));
      if (t.state !== 'done') throw new Error(t.error ?? 'The file could not be opened.');
      await this.load();
      this.model = t.result.key;
    } catch (e) {
      app.toast(errText(e), 'error');
    } finally {
      this.opening = null;
    }
  }
  async removeModel(id: string) {
    const r = await del<{ moved_to: string }>(`api/library/models/${id}`);
    if (this.model === id) this.model = null;
    await this.load();
    app.toast(`Moved to ${r.moved_to}. Nothing was deleted.`);
  }
}
export const studio = new Studio();

export const fmtParams = (n?: number) => (!n ? '' : n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : `${Math.round(n / 1e6)}M`);
export const pct = (t: TaskView) => (t.progress.total ? Math.min(100, Math.round((t.progress.done / t.progress.total) * 100)) : null);
