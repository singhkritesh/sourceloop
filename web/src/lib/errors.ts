import { ApiError, CODE_MESSAGES as MESSAGES } from '../api/http';

export function isRuntimeUnavailable(e: unknown): boolean {
  return e instanceof ApiError && e.code === 'RUNTIME_UNAVAILABLE';
}

export function friendlyMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 413 && !MESSAGES[e.code]) return MESSAGES.PAYLOAD_TOO_LARGE as string;
    const m = MESSAGES[e.code];
    if (m) return m;
    if (e.status === 429) return e.retryAfter ? `Too many requests. Try again in ${e.retryAfter}s.` : 'Too many requests. Please wait and try again.';
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

/** Extract `{field: message}` from a 422 body; tolerant of several envelope shapes. */
export function fieldErrors(e: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!(e instanceof ApiError)) return out;
  const err = (e.body as { error?: Record<string, unknown> } | undefined)?.error;
  const raw = err?.fields ?? err?.details ?? err?.errors;
  const put = (k: unknown, v: unknown) => {
    if (typeof v === 'string' && v) out[typeof k === 'string' && k ? k.replace(/^edit\./, '') : '_'] = v;
  };
  if (Array.isArray(raw)) {
    for (const r of raw) {
      if (r && typeof r === 'object') {
        const o = r as Record<string, unknown>;
        const loc = Array.isArray(o.loc) ? o.loc.filter((x) => x !== 'body').join('.') : o.field ?? o.path;
        put(loc, o.message ?? o.msg);
      }
    }
  } else if (raw && typeof raw === 'object') {
    for (const [k, v] of Object.entries(raw)) put(k, Array.isArray(v) ? v.join(', ') : v);
  }
  return out;
}

/** Project terminal failure caused by a server restart. Status values are additive: compare loosely. */
export function isLostOnRestart(p: { status: string; reason?: string | null }): boolean {
  // The API records this as status STOPPED / outcome FAILED / reason RESTART_LOST_RUNTIME; the reason is the stable signal.
  return (p.reason ?? '').includes('RESTART_LOST_RUNTIME');
}

export const LOST_ON_RESTART_TEXT =
  'The server restarted and this run could not be rebuilt. Demo-mode (replay) runs are normally rebuilt automatically and carry on from where they stopped; this one could not be, because it was a live-AI run (those cannot be reproduced exactly) or its inputs changed since it started. The recorded history and audit trail are intact. Create a new project to run it again.';

export function statusLabel(p: { status: string; reason?: string | null }): string {
  if (isLostOnRestart(p)) return 'lost on restart';
  return p.status.replace(/_/g, ' ').toLowerCase();
}

export function isTerminalStatus(status: string | undefined): boolean {
  return status === 'DONE' || status === 'STOPPED' || status === 'FAILED';
}
