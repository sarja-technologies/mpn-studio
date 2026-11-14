// The open project's workspace chrome: which of its dialogs is showing, and the actions of the top bar that differ
// between a project with its own network ("network") and a linked network or fork ("linked", "fork").
import { errText, post } from '../api';
import { desk } from '../desktop.svelte';
import { app } from '../state.svelte';
import { net } from '../net/net.svelte';

export type WorkDialog = null | 'versions' | 'export' | 'model';

class Work {
  dialog = $state<WorkDialog>(null);
  /** the full model library over the workspace (cores, models, .mpn files) */
  library = $state(false);

  get kind(): 'network' | 'linked' | 'fork' {
    return (desk.project?.kind as any) ?? 'network';
  }
  get isNet() {
    return this.kind !== 'network';
  }
  get canUndo() {
    return this.isNet ? !!net.state?.changes?.can_undo : !!app.status?.undo;
  }
  get canRedo() {
    return this.isNet ? !!net.state?.changes?.can_redo : !!app.status?.redo;
  }
  async undo() {
    if (!this.isNet) return app.undo();
    if (!this.canUndo) return;
    try {
      await post('api/ws/undo');
      await net.reload();
      app.toast('Undone.');
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }
  async redo() {
    if (!this.isNet) return app.redo();
    if (!this.canRedo) return;
    try {
      await post('api/ws/redo');
      await net.reload();
      app.toast('Redone.');
    } catch (e) {
      app.toast(errText(e), 'error');
    }
  }
  async save() {
    if (this.kind === 'linked') return;
    desk.busy = 'Saving…';
    try {
      if (this.isNet) {
        await post('api/ws/save');
        await net.reload();
      } else await post('api/desktop/project/save');
      app.toast('Saved. Every edit is also written the moment you make it.');
    } catch (e) {
      app.toast(errText(e), 'error');
    } finally {
      desk.busy = null;
    }
  }
}
export const work = new Work();
