// State of the open linked network or fork (projects of kind "linked" or "fork"), shared by the workspace top bar and
// the network workspace. OWNED BY the network-workspace work; the top bar only uses the members marked "top bar".
import { ApiError, errText, get, patch, post } from '../api';
import { app } from '../state.svelte';

export interface WsSource {
  index: number;
  mode: 'local' | 'remote';
  target: string;
  label: string;
  available: boolean | null;
  reason: string | null;
  ms?: number;
  nodes?: number;
}
export interface WsState {
  kind: 'linked' | 'fork';
  id: string;
  name: string;
  description?: string;
  read_only: boolean;
  connected: boolean;
  identity: any;
  source: { index: number; mode: 'local' | 'remote'; label: string; target: string } | null;
  sources: WsSource[];
  warning: string | null;
  changes?: { nodes_added: number; nodes_edited: number; nodes_deleted: number; relationships_added: number;
    relationships_removed: number; revision: number; bytes: number; can_undo: boolean; can_redo: boolean };
  project_bytes: number;
  process_rss: number | null;
}
export interface Card {
  id: string;
  title: string;
  kind: string;
  domain: string;
  summary: string | null;
  dataset?: string | number;
  edited?: boolean;
}
export interface Rel {
  rel: string;
  rel_id: string;
  node: string;
  title: string | null;
  domain: string | null;
  kind: string | null;
  cross_domain: boolean;
  present: boolean;
  added?: boolean;
}
export interface GNode {
  id: string;
  title: string | null;
  domain: string | null;
  kind: string | null;
  center?: boolean;
  edited?: boolean;
}
export interface GEdge {
  src: string;
  dst: string;
  rel: string;
  cross_domain: boolean;
  added?: boolean;
}
export interface Graph {
  center: string;
  nodes: GNode[];
  edges: GEdge[];
}

/** What the canvas offers the rest of the workspace (registered by NetCanvas on mount). */
export interface CanvasApi {
  /** start a new neighbourhood around this node */
  root(id: string): Promise<void>;
  /** merge this node's neighbourhood into the view, placed next to `near` when it is new */
  expand(id: string, near?: string | null): Promise<void>;
  /** read these nodes' neighbourhoods again after an edit or undo */
  refresh(ids: string[]): Promise<void>;
  /** take a node off the canvas (deleted) */
  drop(id: string): void;
  /** take one edge off the canvas */
  dropEdge(src: string, dst: string, rel: string): void;
  /** is the node on the canvas? */
  has(id: string): boolean;
  /** back to the domain map */
  overview(): void;
  fit(): void;
  mode(): 'domains' | 'graph';
}

// The fixed vocabularies of mpnstudio/mpnnet/schema.py. A claim is never offered as a kind.
export const DOMAINS = ['general', 'mathematics', 'physics', 'chemistry', 'biology', 'medicine', 'history', 'geography', 'languages',
  'government', 'economics', 'engineering', 'computing', 'literature', 'art_culture', 'agriculture', 'astronomy',
  'philosophy', 'law', 'earth_sciences', 'social_sciences', 'people', 'organisations', 'scholarly_works',
  'religion', 'sport', 'music', 'film_media', 'technology', 'education', 'transport', 'psychology',
  'environment', 'military'];
export const KINDS = ['concept', 'lexeme', 'sense', 'work', 'person', 'organisation', 'venue', 'topic', 'procedure',
  'extracted_fact', 'migrated'];
export const REL_SUGGESTIONS = ['related to', 'part of', 'instance of', 'subclass of', 'cites', 'located in'];

const WORDS: Record<string, string> = { art_culture: 'Art and culture', film_media: 'Film and media' };
/** A domain or kind in plain words with a capital first letter ("social_sciences" -> "Social sciences"). */
export function word(s: string | null | undefined): string {
  if (!s) return '';
  if (WORDS[s]) return WORDS[s];
  const h = s.replace(/[_-]+/g, ' ');
  return h.charAt(0).toUpperCase() + h.slice(1);
}

class Net {
  /** top bar: the workspace state (/api/ws/state) */
  state = $state.raw<WsState | null>(null);
  ov = $state.raw<any>(null);
  ovErr = $state<string | null>(null);
  ovBusy = $state(false);
  res = $state.raw<any>(null);
  probed = $state.raw<WsSource[] | null>(null);

  /** the selected node and its details */
  sel = $state<string | null>(null);
  detail = $state.raw<any>(null);
  detailErr = $state<string | null>(null);
  detailBusy = $state(false);
  /** the explorer's search text, and the domain filter ('' with no search = the domain list) */
  q = $state('');
  domain = $state('');
  kind = $state('');
  /** connect mode: the source node and, once chosen, the target */
  connecting = $state.raw<{ src: string; srcTitle: string; dst: string | null; dstTitle: string | null } | null>(null);
  /** dialogs owned by the workspace */
  dialog = $state<null | 'add' | 'delete'>(null);
  busy = $state(false);

  canvas: CanvasApi | null = null;
  searchEl: HTMLInputElement | null = null;
  private mounted = false;
  private project: string | null = null;
  private detailSeq = 0;

  get editable() {
    return this.state?.read_only === false;
  }

  /** top bar: read the state again (after undo, redo, save version) and refresh whatever is shown */
  async reload() {
    try {
      const s = await get<WsState>('api/ws/state');
      if (this.project && s.id !== this.project) this.reset();
      this.project = s.id;
      this.state = s;
    } catch (e) {
      this.ovErr = errText(e);
      return;
    }
    if (!this.mounted) return;
    await this.loadOverview();
    this.loadResources();
    if (this.sel) {
      const id = this.sel;
      await this.loadDetail(id);
      if (this.detailErr && this.canvas?.has(id)) this.canvas.drop(id);
      else this.canvas?.refresh([id]);
    } else this.canvas?.refresh([]);
  }
  /** top bar: put the cursor in the search box */
  focusSearch() {
    this.searchEl?.focus();
    this.searchEl?.select();
  }

  /** the workspace mounted: load everything once */
  async start() {
    this.mounted = true;
    await this.reload();
  }
  stop() {
    this.mounted = false;
    this.canvas = null;
  }
  private reset() {
    this.ov = null;
    this.ovErr = null;
    this.res = null;
    this.probed = null;
    this.sel = null;
    this.detail = null;
    this.detailErr = null;
    this.q = '';
    this.domain = '';
    this.kind = '';
    this.connecting = null;
    this.dialog = null;
    this.canvas?.overview();
  }

  async loadOverview() {
    this.ovBusy = true;
    try {
      this.ov = await get('api/ws/overview');
      this.ovErr = null;
    } catch (e) {
      this.ov = null;
      this.ovErr = errText(e);
    }
    this.ovBusy = false;
  }
  async loadResources() {
    try {
      this.res = await get('api/ws/resources');
    } catch {
      /* the status bar keeps the last measured values */
    }
  }

  // ---------------------------------------------------------------- sources
  async useSource(index: number | null) {
    this.busy = true;
    try {
      const st = await post<any>('api/ws/connect', index == null ? {} : { source: index });
      this.probed = null;
      await this.reload();
      if (!st.connected) app.toast('None of the sources answered.', 'error');
    } catch (e) {
      app.toast(errText(e), 'error');
    }
    this.busy = false;
  }
  async probe() {
    this.busy = true;
    try {
      const r = await get<{ sources: WsSource[] }>('api/ws/probe');
      this.probed = r.sources;
    } catch (e) {
      app.toast(errText(e), 'error');
    }
    this.busy = false;
  }
  async addSource(mode: 'local' | 'remote', target: string, label: string): Promise<boolean> {
    this.busy = true;
    try {
      await post('api/ws/sources', { mode, target, label });
      this.probed = null;
      await this.reload();
      app.toast('Source added.');
      return true;
    } catch (e) {
      app.toast(errText(e), 'error');
      return false;
    } finally {
      this.busy = false;
    }
  }
  /** the sources with the freshest availability we know */
  get sources(): WsSource[] {
    const s = this.state?.sources ?? [];
    if (!this.probed) return s;
    return s.map((x) => ({ ...x, ...(this.probed!.find((p) => p.index === x.index) ?? {}) }));
  }

  // ---------------------------------------------------------------- selection
  /** select a node; `how` says what the canvas does with it */
  async select(id: string | null, how: 'root' | 'expand' | 'none' = 'none') {
    if (!id) {
      this.sel = null;
      this.detail = null;
      this.detailErr = null;
      return;
    }
    const near = this.sel;
    this.sel = id;
    // one request at a time: the node first, then its neighbourhood
    await this.loadDetail(id);
    if (this.sel !== id && this.detail?.node?.id !== this.sel) return;
    const nid = this.detail?.node?.id ?? id;
    if (how === 'root') await this.canvas?.root(nid);
    else if (how === 'expand') await this.canvas?.expand(nid, near);
  }
  /** the node being read, so the canvas can wait for it instead of reading the fork at the same moment */
  pending: Promise<void> = Promise.resolve();
  loadDetail(id: string) {
    const p = this.readDetail(id);
    this.pending = p.catch(() => {});
    return p;
  }
  private async readDetail(id: string) {
    const seq = ++this.detailSeq;
    this.detailBusy = true;
    try {
      const d = await get<any>(`api/ws/node/${encodeURIComponent(id)}`);
      if (seq !== this.detailSeq) return;
      this.detail = d;
      this.detailErr = null;
      // a Wikidata id or DOI resolves to the node id
      if (d.node?.id && d.node.id !== id && this.sel === id) this.sel = d.node.id;
    } catch (e) {
      if (seq !== this.detailSeq) return;
      this.detail = null;
      this.detailErr = errText(e);
    } finally {
      if (seq === this.detailSeq) this.detailBusy = false;
    }
  }
  /** browse a domain in the explorer */
  browse(domain: string) {
    this.q = '';
    this.domain = domain;
  }

  // ---------------------------------------------------------------- edits (forks)
  private async afterEdit(ids: string[]) {
    try {
      this.state = await get<WsState>('api/ws/state');
    } catch {
      /* keep the last state */
    }
    this.loadOverview();
    this.loadResources();
    if (this.sel) await this.loadDetail(this.sel);
    await this.canvas?.refresh(ids);
  }
  private async run<T>(fn: () => Promise<T>): Promise<T | null> {
    if (!this.editable) {
      app.toast('This is the protected original. Duplicate it to make an editable copy.', 'error');
      return null;
    }
    this.busy = true;
    try {
      return await fn();
    } catch (e) {
      app.toast(errText(e), 'error');
      return null;
    } finally {
      this.busy = false;
    }
  }
  async save(id: string, fields: Record<string, any>) {
    if (!Object.keys(fields).length) return true;
    const r = await this.run(() => patch(`api/ws/node/${encodeURIComponent(id)}`, fields));
    if (!r) return false;
    await this.afterEdit([id]);
    return true;
  }
  async duplicate(id: string) {
    const r = await this.run(() => post<any>(`api/ws/node/${encodeURIComponent(id)}/duplicate`, {}));
    if (!r) return;
    const nid = r.node.id;
    this.state = await get<WsState>('api/ws/state');
    this.loadOverview();
    app.toast(`Made “${r.node.title}”.`);
    await this.select(nid, this.canvas?.has(id) ? 'expand' : 'root');
  }
  async remove(id: string) {
    const r = await this.run(() => post<any>('api/ws/nodes/delete', { ids: [id] }));
    if (!r) return false;
    this.canvas?.drop(id);
    if (this.sel === id) this.select(null);
    await this.afterEdit([]);
    app.toast('Node deleted. Undo brings it back.');
    return true;
  }
  async create(fields: Record<string, any>) {
    const r = await this.run(() => post<any>('api/ws/nodes', fields));
    if (!r) return false;
    this.state = await get<WsState>('api/ws/state');
    this.loadOverview();
    await this.select(r.node.id, this.canvas?.mode() === 'graph' ? 'expand' : 'root');
    return true;
  }
  async connect(src: string, dst: string, rel: string) {
    const r = await this.run(() => post<any>('api/ws/edges', { src, dst, rel }));
    if (!r) return false;
    if (this.canvas && !this.canvas.has(dst)) await this.canvas.expand(dst, src);
    await this.afterEdit([src, dst]);
    return true;
  }
  async disconnect(src: string, relId: string, dst: string, label: string) {
    const r = await this.run(() => post<any>('api/ws/edges/remove', { src, rel: relId, dst }));
    if (!r) return false;
    this.canvas?.dropEdge(src, dst, label);
    await this.afterEdit([src, dst]);
    return true;
  }

  isGone(e: unknown) {
    return e instanceof ApiError && e.status === 410;
  }
}
export const net = new Net();
