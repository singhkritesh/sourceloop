/** Renders arbitrary JSON as plain, escaped text (agent/evidence data is untrusted). */
export function Tree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span className="muted">-</span>;
  if (typeof value !== 'object') return <span>{String(value)}</span>;
  if (depth > 6) return <span className="muted">…</span>;
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="muted">empty</span>;
  const allPrim = Array.isArray(value) && value.every((v) => typeof v !== 'object' || v === null);
  if (allPrim) return <span>{(value as unknown[]).map(String).join(', ')}</span>;
  return (
    <div className={depth === 0 ? 'tree' : undefined}>
      <ul>
        {entries.map(([k, v]) => (
          <li key={k}>
            <span className="k">{Array.isArray(value) ? `#${Number(k) + 1}` : k.replace(/_/g, ' ')}: </span>
            <Tree value={v} depth={depth + 1} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** first numeric `confidence` anywhere in the value (0..1) */
export function findConfidence(v: unknown, depth = 0): number | null {
  if (!v || typeof v !== 'object' || depth > 5) return null;
  for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
    if (k === 'confidence' && typeof x === 'number') return x;
    const r = findConfidence(x, depth + 1);
    if (r !== null) return r;
  }
  return null;
}

/** every `sources` list found in the value, flattened to strings */
export function findSources(v: unknown, depth = 0): string[] {
  if (!v || typeof v !== 'object' || depth > 5) return [];
  const out: string[] = [];
  for (const [k, x] of Object.entries(v as Record<string, unknown>)) {
    if (k === 'sources' && Array.isArray(x)) out.push(...x.map((s) => (typeof s === 'string' ? s : JSON.stringify(s))));
    else out.push(...findSources(x, depth + 1));
  }
  return out;
}
