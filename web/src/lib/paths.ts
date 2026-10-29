export type Path = ReadonlyArray<string | number>;

export function getIn(obj: unknown, path: Path): unknown {
  let cur: unknown = obj;
  for (const k of path) {
    if (cur === null || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string | number, unknown>)[k];
  }
  return cur;
}

/** immutable set; creates containers as needed (array if next key is a number) */
export function setIn<T>(obj: T, path: Path, value: unknown): T {
  if (path.length === 0) return value as T;
  const [k, ...rest] = path as [string | number, ...(string | number)[]];
  const base: Record<string | number, unknown> | unknown[] = Array.isArray(obj)
    ? [...obj]
    : obj && typeof obj === 'object' ? { ...(obj as Record<string, unknown>) } : typeof k === 'number' ? [] : {};
  (base as Record<string | number, unknown>)[k] = setIn((base as Record<string | number, unknown>)[k], rest, value);
  return base as T;
}

export function deleteIn<T>(obj: T, path: Path): T {
  if (path.length === 0) return obj;
  if (obj === null || typeof obj !== 'object') return obj;
  const [k, ...rest] = path as [string | number, ...(string | number)[]];
  if (rest.length === 0) {
    if (Array.isArray(obj)) return obj.filter((_, i) => i !== k) as unknown as T;
    const { [k as string]: _gone, ...others } = obj as Record<string, unknown>;
    return others as T;
  }
  const child = (obj as Record<string | number, unknown>)[k];
  return setIn(obj, [k], deleteIn(child, rest));
}

export const pathKey = (p: Path): string => p.join('.');

/**
 * Normalise a server error path to the dotted form used by the form: accepts "a.b.0.c", "/a/b/0/c", "a[b][0]",
 * and strips leading "config." / "body." / "body.config." wrappers.
 */
export function normalizeErrorPath(raw: string): string {
  let s = raw.trim();
  s = s.replace(/\[(?:'|")?([^\]'"]+)(?:'|")?\]/g, '.$1');
  if (s.startsWith('/')) s = s.slice(1).replace(/\//g, '.');
  const parts = s.split('.').filter((x) => x !== '');
  while (parts[0] === 'body' || parts[0] === 'config') parts.shift();
  return parts.join('.');
}

/** true when `errPath` is the field itself or inside it */
export const isSameOrChild = (field: string, errPath: string): boolean =>
  errPath === field || errPath.startsWith(field + '.');
