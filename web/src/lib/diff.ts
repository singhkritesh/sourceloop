import type { DiffEntry, DiffResponse } from '../api/studioTypes';

const isObj = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);

/** Leaf-level structural diff of two JSON documents ("a.b.0.c" paths). */
export function deepDiff(a: unknown, b: unknown, path = ''): DiffEntry[] {
  if (JSON.stringify(a) === JSON.stringify(b)) return [];
  if (isObj(a) && isObj(b)) {
    const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort();
    return keys.flatMap((k) => deepDiff(a[k], b[k], path ? `${path}.${k}` : k));
  }
  if (Array.isArray(a) && Array.isArray(b) && a.length === b.length) {
    return a.flatMap((x, i) => deepDiff(x, b[i], path ? `${path}.${i}` : String(i)));
  }
  const kind = a === undefined ? 'added' : b === undefined ? 'removed' : 'changed';
  return [{ path, kind, before: a, after: b }];
}

/** Accepts the server diff in a few plausible shapes; returns null when it cannot be understood. */
export function normalizeDiff(resp: DiffResponse | null | undefined): DiffEntry[] | null {
  const list = Array.isArray(resp) ? resp : resp?.changes ?? resp?.diff;
  if (!Array.isArray(list)) return null;
  return list.map((e) => ({
    path: String(e.path ?? ''),
    kind: e.kind ?? (e.before === undefined && e.a === undefined ? 'added' : (e.after === undefined && e.b === undefined) ? 'removed' : 'changed'),
    before: e.before ?? e.a,
    after: e.after ?? e.b,
  }));
}
