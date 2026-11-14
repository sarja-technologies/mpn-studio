// The home screen: every project on this computer, the recently opened ones, and the dialogs that create, open,
// duplicate, rename and remove them.
import { get, post, errText } from '../api';
import { desk, saveView } from '../desktop.svelte';

export type Kind = 'network' | 'linked' | 'fork';
export interface Project {
  id: string;
  name: string;
  description?: string;
  kind: Kind;
  created?: string;
  modified?: string;
  opened?: number | null;
  nodes?: number | null;
  bytes?: number;
  head?: number | null;
  mpns?: number;
  base?: string | null;
  network?: { name: string | null; nodes: number | null; relationships: number | null; storage_bytes: number | null; known?: string;
    sources: { mode: 'local' | 'remote'; label: string | null; target: string }[] };
  changes?: { nodes_added: number; nodes_edited: number; nodes_deleted: number; versions?: number; bytes: number };
  models: { id: string; name: string; state: string; core: string | null }[];
  damaged?: boolean;
}
export interface Availability {
  available: boolean;
  sources: { index: number; label: string; mode: string; available: boolean; reason: string | null; ms: number }[];
}

export type Dialog =
  | { kind: 'create'; tab?: 'blank' | 'duplicate' | 'import'; from?: string }
  | { kind: 'open' }
  | { kind: 'rename'; project: Project }
  | { kind: 'trash'; project: Project };

class Home {
  projects = $state.raw<Project[]>([]);
  recent = $state.raw<Project[]>([]);
  location = $state('');
  loaded = $state(false);
  error = $state<string | null>(null);
  dialog = $state<Dialog | null>(null);
  availability = $state<Record<string, Availability | 'checking'>>({});

  async load() {
    try {
      const r = await get<{ projects: Project[]; recent: Project[]; location: string }>('api/projects');
      this.projects = r.projects;
      this.recent = r.recent;
      this.location = r.location;
      this.error = null;
      for (const p of r.projects) if (p.kind !== 'network' && !this.availability[p.id]) this.check(p.id);
    } catch (e) {
      this.error = errText(e);
    }
    this.loaded = true;
  }
  async check(id: string) {
    this.availability = { ...this.availability, [id]: 'checking' };
    try {
      const a = await get<Availability>(`api/projects/${id}/availability`);
      this.availability = { ...this.availability, [id]: a };
    } catch {
      const { [id]: _, ...rest } = this.availability;
      this.availability = rest;
    }
  }
  byId(id: string | null | undefined) {
    return this.projects.find((p) => p.id === id) ?? null;
  }
  /** Open a project and show its workspace. The whole window reloads so every view reads the new project from scratch. */
  async open(id: string) {
    if (desk.project?.id === id) {
      desk.home = false;
      return;
    }
    desk.busy = 'Opening…';
    saveView('editor');
    try {
      await post(`api/projects/${id}/open`);
      location.reload();
    } catch (e) {
      desk.busy = null;
      throw e;
    }
  }
  /** After a create/duplicate/import that already opened the new project on the server. */
  enter() {
    saveView('editor');
    location.reload();
  }
}
export const home = new Home();

export function kindLine(p: Project, all: Project[]): string {
  if (p.kind === 'linked') return 'Protected original';
  if (p.kind === 'fork') {
    const base = all.find((x) => x.id === p.base);
    return `Editable fork of ${base?.name ?? p.network?.name ?? 'a linked network'}`;
  }
  const m = p.models?.[0];
  if (m) return m.core ? `Model with the ${m.core} core` : 'Model without a cognitive core';
  return 'Knowledge network';
}
