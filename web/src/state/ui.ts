import { create } from 'zustand';

type Theme = 'system' | 'light' | 'dark';
const KEY = 'sl.theme';

function readTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}
export function applyTheme(t: Theme): void {
  const el = document.documentElement;
  if (t === 'system') el.removeAttribute('data-theme');
  else el.setAttribute('data-theme', t);
}

interface Toast { id: number; kind: 'ok' | 'error' | 'info' | 'warn'; text: string }
interface UiState {
  theme: Theme;
  cycleTheme: () => void;
  toasts: Toast[];
  toast: (kind: Toast['kind'], text: string, ms?: number) => void;
  dismiss: (id: number) => void;
}
let n = 0;
export const useUi = create<UiState>((set, get) => ({
  theme: readTheme(),
  cycleTheme: () => {
    const order: Theme[] = ['system', 'light', 'dark'];
    const next = order[(order.indexOf(get().theme) + 1) % 3] as Theme;
    try { localStorage.setItem(KEY, next); } catch { /* per-viewer convenience only */ }
    applyTheme(next);
    set({ theme: next });
  },
  toasts: [],
  toast: (kind, text, ms = kind === 'error' ? 8000 : 4000) => {
    const id = ++n;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { id, kind, text }] }));
    setTimeout(() => get().dismiss(id), ms);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
export const toast = {
  ok: (t: string) => useUi.getState().toast('ok', t),
  error: (t: string) => useUi.getState().toast('error', t),
  info: (t: string) => useUi.getState().toast('info', t),
  warn: (t: string) => useUi.getState().toast('warn', t),
};
