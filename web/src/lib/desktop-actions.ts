// File-menu level actions of the desktop app. Each one reports its real outcome (or the server's refusal) as a toast.
import { app } from './state.svelte';
import { studio } from './studio/studio.svelte';
import { desk, errText, get, isTauri, native, pickFile, pickFolder, post } from './desktop.svelte';
import { fmtBytes, fmtInt } from './api';

async function run<T>(label: string, f: () => Promise<T>): Promise<T | undefined> {
  desk.busy = label;
  try {
    return await f();
  } catch (e) {
    app.toast(errText(e), 'error');
  } finally {
    desk.busy = null;
  }
}

export const newProject = () => ((studio.tab = 'networks'), (desk.home = true), undefined);

export async function saveProject() {
  const r = await run('Saving...', () => post<{ head: number }>('api/desktop/project/save'));
  if (r) app.toast(`Saved at revision ${r.head}. Edits are stored as you make them; this also compacts the project file.`);
}

export async function exportProject() {
  const p = desk.project;
  if (!p) return;
  const base = await pickFolder('Choose where to save the export');
  if (!base) return;
  const r = await run('Exporting...', () => post<any>('api/desktop/project/export', { dest: `${base.replace(/\/+$/, '')}/${p.id}-rev${app.head}` }));
  if (r) app.toast(`Exported ${fmtInt(r.counts?.canonical_live_nodes ?? 0)} nodes to ${r.path}.`);
}

export async function importBundle() {
  const dir = await pickFolder('Choose the bundle folder (the one that contains manifest.json)');
  if (!dir) return;
  const r = await run('Importing...', () => post<{ id: string }>('api/desktop/projects/import', { kind: 'bundle', path: dir }));
  if (r) desk.reloadApp();
}

export async function importStore() {
  const f = await pickFile('Choose an MPN Studio store file', ['sqlite', 'db']);
  if (!f) return;
  const r = await run('Importing...', () => post<{ id: string }>('api/desktop/projects/import', { kind: 'store', path: f }));
  if (r) desk.reloadApp();
}

/** A backup file always becomes a new project; nothing that exists is overwritten. */
export async function openBackupFile(file?: string) {
  const f = file ?? (await pickFile('Choose an MPN Studio backup file', ['mpnbak']));
  if (!f) return;
  const r = await run('Restoring...', () => post<{ id: string }>('api/desktop/backups/restore', { file: f }));
  if (r) desk.reloadApp();
}

/** Files handed to the app by the system (double-click in the file manager, "Open with"). */
export async function openFilesFromSystem() {
  if (!isTauri()) return;
  const files = await native<string[]>('take_open_files').catch(() => [] as string[]);
  const f = files[0];
  if (!f) return;
  if (f.endsWith('.mpn')) {
    desk.home = true;
    await studio.openMpn(f);
  } else await openBackupFile(f);
}

export async function backupNow() {
  const r = await run('Backing up...', () => post<{ file: string; bytes: number }>('api/desktop/project/backup'));
  if (r) app.toast(`Backed up (${fmtBytes(r.bytes)}) to ${r.file}.`);
}

export async function restoreBackup(id: string) {
  const r = await run('Restoring...', () => post<{ id: string }>('api/desktop/backups/restore', { id }));
  if (r) desk.reloadApp();
}

export async function quitApp() {
  await get('api/health').catch(() => {});
  if (isTauri()) native('quit_app').catch(() => {});
}
