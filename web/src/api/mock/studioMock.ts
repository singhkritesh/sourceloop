/** Dev-only fixture backend for /studio/* (VITE_MOCK_STUDIO=1). Mirrors the route contract; state lives in memory. */
import fixture from './fixture.json';
import { ApiError } from '../http';
import type { Issue, StudioConfigValue, VersionRow } from '../studioTypes';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const delay = <T,>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

let draft: StudioConfigValue = clone(fixture.config) as StudioConfigValue;
let draftUpdated = new Date().toISOString();
const versions: Array<VersionRow & { config: StudioConfigValue }> = [
  { version: 1, content_hash: 'a'.repeat(64), created_by: 'system', created_at: new Date(Date.now() - 864e5).toISOString(), note: 'Bootstrap defaults', rollback_of: null, active: true, config: clone(draft) },
];
const providers: Record<string, boolean> = { ollama: false };

function validate(cfg: StudioConfigValue): { errors: Issue[]; warnings: Issue[] } {
  const errors: Issue[] = [], warnings: Issue[] = [];
  const g = (p: string[]): unknown => p.reduce<unknown>((o, k) => (o && typeof o === 'object' ? (o as Record<string, unknown>)[k] : undefined), cfg);
  for (const k of ['liability', 'termination', 'ip']) {
    if (g(['clauses', k, 'locked']) !== true) errors.push({ path: `clauses.${k}.locked`, code: 'INVARIANT', message: `Clause ${k} must stay locked`, invariant_id: 'INV_LOCKED_CLAUSES' });
  }
  if (g(['agents', 'compliance_screen', 'enabled']) === false) errors.push({ path: 'agents.compliance_screen.enabled', code: 'INVARIANT', message: 'Compliance screen cannot be disabled', invariant_id: 'INV_COMPLIANCE_ENABLED' });
  const au = g(['workflow', 'autonomy', 'CONTRACTING']);
  if (typeof au === 'number' && au > 2) errors.push({ path: 'workflow.autonomy.CONTRACTING', code: 'INVARIANT', message: 'Contracting autonomy is capped at L2', invariant_id: 'INV_CONTRACT_AUTONOMY_MAX_L2' });
  const roles = (g(['llm', 'roles']) ?? {}) as Record<string, { provider?: string }>;
  const provs = (g(['providers']) ?? {}) as Record<string, unknown>;
  for (const [r, m] of Object.entries(roles)) if (m.provider && !(m.provider in provs)) errors.push({ path: `llm.roles.${r}.provider`, code: 'UNKNOWN_PROVIDER', message: `Provider ${m.provider} is not configured`, invariant_id: null });
  if (g(['llm', 'mode']) === 'live') warnings.push({ path: 'llm.mode', code: 'LIVE_MODE', message: 'Live mode makes real model calls and breaks replay determinism', invariant_id: null });
  return { errors, warnings };
}

export async function mockStudio<T>(method: string, path: string, body: unknown, query?: Record<string, string | number | undefined | null>): Promise<T> {
  const p = path.replace(/^\/studio/, '');
  const b = (body ?? {}) as Record<string, unknown>;
  const out = (v: unknown) => delay(clone(v) as T);
  if (method === 'GET' && p === '/schema') return out({ schema: fixture.schema, invariants: fixture.invariants });
  if (method === 'GET' && p === '/draft') return out({ config: draft, base_version: versions.at(-1)?.version ?? null, updated_at: draftUpdated });
  if (method === 'PUT' && p === '/draft') { draft = clone(b.config as StudioConfigValue); draftUpdated = new Date().toISOString(); return out({ config: draft, base_version: versions.at(-1)?.version ?? null, updated_at: draftUpdated }); }
  if (method === 'POST' && p === '/validate') return out(validate((b.config as StudioConfigValue) ?? draft));
  if (method === 'POST' && p === '/publish') {
    const rep = validate(draft);
    if (rep.errors.length) throw new ApiError(422, 'VALIDATION_FAILED', 'Draft has validation errors', 'mock', undefined, rep);
    const v = (versions.at(-1)?.version ?? 0) + 1;
    versions.forEach((x) => { x.active = false; });
    versions.push({ version: v, content_hash: (v.toString(16) + 'b').repeat(32).slice(0, 64), created_by: 'mock-admin', created_at: new Date().toISOString(), note: String(b.note ?? ''), rollback_of: null, active: true, config: clone(draft) });
    return delay({ version: v, content_hash: versions.at(-1)?.content_hash } as T);
  }
  if (method === 'GET' && p === '/versions') return out(versions.map(({ config: _c, ...row }) => row).reverse());
  let m = /^\/versions\/(\d+)$/.exec(p);
  if (method === 'GET' && m) { const v = versions.find((x) => x.version === Number(m?.[1])); if (!v) throw new ApiError(404, 'NOT_FOUND', 'version not found'); return out({ version: v.version, config: v.config }); }
  if (method === 'GET' && p === '/active') { const v = versions.find((x) => x.active) ?? versions[0]; return out({ version: v?.version, config: v?.config }); }
  if (method === 'POST' && p === '/rollback') {
    const src = versions.find((x) => x.version === Number(b.to_version));
    if (!src) throw new ApiError(404, 'NOT_FOUND', 'version not found');
    const v = (versions.at(-1)?.version ?? 0) + 1;
    versions.forEach((x) => { x.active = false; });
    versions.push({ ...clone(src), version: v, rollback_of: src.version, active: true, created_at: new Date().toISOString(), created_by: 'mock-admin', note: `Rollback to v${src.version}` });
    draft = clone(src.config);
    return delay({ version: v } as T);
  }
  if (method === 'GET' && p === '/diff') {
    const a = versions.find((x) => x.version === Number(query?.a)), c = versions.find((x) => x.version === Number(query?.b));
    if (!a || !c) throw new ApiError(404, 'NOT_FOUND', 'version not found');
    return out([]); // empty: the UI computes the diff client-side from the two version configs
  }
  if (method === 'GET' && p === '/providers') return out(Object.entries((draft.providers ?? {}) as Record<string, { kind: string; base_url: string; is_local: boolean }>).map(([name, d]) => ({ name, kind: d.kind, base_url: d.base_url, is_local: d.is_local, has_key: !!providers[name] })));
  m = /^\/providers\/([^/]+)\/key$/.exec(p);
  if (m && method === 'PUT') { if (typeof b.secret !== 'string' || !b.secret) throw new ApiError(422, 'VALIDATION_ERROR', 'secret required'); providers[decodeURIComponent(m[1] as string)] = true; return out({ has_key: true }); }
  if (m && method === 'DELETE') { providers[decodeURIComponent(m[1] as string)] = false; return out({ has_key: false }); }
  m = /^\/providers\/([^/]+)\/test$/.exec(p);
  if (m && method === 'POST') return delay({ ok: true, models: ['gemma4:e2b-mlx', 'qwen3.8:27b-mlx', 'llama3.3:70b'] } as T, 400);
  if (method === 'GET' && p === '/export') return out({ schema_version: 1, config: draft, versions: versions.map(({ config: _c, ...r }) => r) });
  if (method === 'POST' && p === '/import') { const bundle = b.bundle as { config?: StudioConfigValue }; if (!bundle?.config) throw new ApiError(422, 'VALIDATION_ERROR', 'bundle.config missing'); draft = clone(bundle.config); return out({ ok: true }); }
  throw new ApiError(404, 'NOT_FOUND', `mock: no route ${method} ${p}`);
}
