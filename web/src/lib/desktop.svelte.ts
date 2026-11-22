// Desktop shell state: the open project, the project list, native dialogs, settings. In a plain browser (dev server)
// the shell is absent: projects are still reachable through the API, and path pickers fall back to typing a path.
import { configureBackend, get, post, put, del, errText } from './api';

const internals = () => (typeof window !== 'undefined' ? (window as any).__TAURI_INTERNALS__ : undefined);
export const isTauri = () => !!internals();
export const native = <T = any>(cmd: string, args: Record<string, unknown> = {}): Promise<T> => {
  const i = internals();
  if (!i) return Promise.reject(new Error('This needs the MPN Studio desktop window.'));
  return i.invoke(cmd, args);
};

export interface ProjectRow {
  id: string;
  name: string;
  description?: string;
  created?: string;
  modified?: string;
  origin?: { kind: string; detail?: string; imported?: string } | null;
  bytes: number;
  head?: number | null;
  nodes?: number;
  mpns?: number;
  damaged?: boolean;
}
export interface DesktopInfo {
  desktop: boolean;
  version: string;
  architecture: string;
  legacy_architecture_name: string;
  paths: Record<string, string>;
  project: { id: string; name: string; kind?: 'network' | 'linked' | 'fork'; core?: string | null; head: number | null; path: string; origin?: any } | null;
  projects: ProjectRow[];
  settings: any;
}

/** Ask the shell where the local backend is and bind the API client to it. No-op in a browser. */
export async function bootBackend(): Promise<void> {
  if (!isTauri()) return;
  const b = await native<{ base: string; token: string }>('backend_info');
  configureBackend(b.base, b.token);
}

// Which screen a reload returns to: the home screen (default, also at launch) or the workspace right after opening a project.
function readView(): string | null {
  try {
    return sessionStorage.getItem('mpn-view');
  } catch {
    return null;
  }
}
export function saveView(v: 'editor' | 'studio') {
  try {
    sessionStorage.setItem('mpn-view', v);
  } catch {
    /* private mode: the Studio is shown after a reload */
  }
}

class Desk {
  ready = $state(false);
  bootError = $state<string | null>(null);
  info = $state.raw<DesktopInfo | null>(null);
  /** the home screen is shown over the open project's workspace, or instead of it when no project is open */
  home = $state(readView() !== 'editor');
  settings = $state<null | 'storage' | 'compute' | 'backups' | 'catalogue'>(null);
  help = $state<null | 'names' | 'shortcuts' | 'about'>(null);
  busy = $state<string | null>(null);

  get project() {
    return this.info?.project ?? null;
  }
  get needsProject() {
    return this.ready && !this.info?.project;
  }
  async refresh() {
    this.info = await get<DesktopInfo>('api/desktop/info');
    if (typeof document !== 'undefined') document.title = this.info.project ? `${this.info.project.name} - MPN Studio` : 'MPN Studio';
    if (isTauri()) native('set_window_title', { title: document.title }).catch(() => {});
  }
  /** every open/create/import ends by reloading so that all views read the new project from scratch */
  reloadApp() {
    location.reload();
  }
  async openProject(id: string) {
    this.busy = 'Opening the network...';
    saveView('editor');
    try {
      await post(`api/desktop/projects/${id}/open`);
      this.reloadApp();
    } finally {
      this.busy = null;
    }
  }
  async createProject(name: string, description = '') {
    await post('api/desktop/projects', { name, description });
    this.reloadApp();
  }
  async closeProject() {
    saveView('studio');
    await post('api/desktop/project/close');
    this.reloadApp();
  }
}
export const desk = new Desk();

// ---------------------------------------------------------------- native pickers
// The desktop window shows the system file chooser. In a browser there is none, so the path is typed.
export async function pickFolder(title: string): Promise<string | null> {
  if (isTauri()) return (await native<string | null>('pick_folder', { title })) ?? null;
  return window.prompt(`${title}\nFull path of the folder:`)?.trim() || null;
}
export async function pickFile(title: string, extensions: string[] = []): Promise<string | null> {
  if (isTauri()) return (await native<string | null>('pick_file', { title, extensions })) ?? null;
  return window.prompt(`${title}\nFull path of the file:`)?.trim() || null;
}
export const openInFileManager = (path: string) => (isTauri() ? native('reveal_path', { path }) : Promise.resolve());

export { get, post, put, del, errText };
