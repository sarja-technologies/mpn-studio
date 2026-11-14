/** Calls `fn` when a pointer goes down outside the element, or Escape is pressed.
 *  If `fn` returns true for Escape, the key was consumed (an open menu closed) and goes no further. */
export function clickOutside(node: HTMLElement, fn: () => void | boolean) {
  let cb = fn;
  const down = (e: PointerEvent) => {
    if (!node.contains(e.target as Node)) cb();
  };
  const key = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && cb() === true) e.stopPropagation();
  };
  document.addEventListener('pointerdown', down, true);
  document.addEventListener('keydown', key);
  return {
    update(f: () => void | boolean) {
      cb = f;
    },
    destroy() {
      document.removeEventListener('pointerdown', down, true);
      document.removeEventListener('keydown', key);
    },
  };
}

/** Moves focus into the element when it appears. */
export function autofocus(node: HTMLElement) {
  queueMicrotask(() => node.focus());
}

// Ctrl+Enter (Cmd+Enter) submits the form from any field, including a multi-line one.
export function submitOnCtrlEnter(form: HTMLFormElement) {
  const on = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  };
  form.addEventListener('keydown', on);
  return { destroy: () => form.removeEventListener('keydown', on) };
}
