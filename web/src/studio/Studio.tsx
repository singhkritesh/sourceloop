import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, errorMessage } from '../api/http';
import { studio } from '../api/studio';
import type { Invariant, Issue, StudioConfigValue, ValidationReport } from '../api/studioTypes';
import { Icon } from '../components/Icon';
import { Badge, Dialog, ErrorBox, SkeletonList } from '../components/ui';
import { getIn, isSameOrChild, normalizeErrorPath, setIn } from '../lib/paths';
import { sectionsOf } from '../lib/schemaForm';
import { can, useAuth } from '../state/auth';
import { toast } from '../state/ui';
import { FormCtx, type FormCtxValue } from './formCtx';
import { ProvidersPanel } from './ProvidersPanel';
import { IssueList, PublishDialog, reportFromError } from './PublishDialog';
import { SchemaForm } from './SchemaForm';
import { Versions } from './Versions';

type Save = 'saved' | 'dirty' | 'saving' | 'error';
const EMPTY: ValidationReport = { errors: [], warnings: [] };

/** Sections shown in the basic view: where models run, how the workflow behaves, and the demo scenarios. */
const BASIC_SECTIONS = new Set(['providers', 'llm', 'workflow', 'scenarios']);

export function Studio() {
  return (
    <Routes>
      <Route index element={<Navigate to="providers" replace />} />
      <Route path=":section" element={<StudioInner />} />
    </Routes>
  );
}

function StudioInner() {
  const { section = 'providers' } = useParams();
  const [advanced, setAdvanced] = useState(() => { try { return localStorage.getItem('sourceloop.studio.advanced') === '1'; } catch { return false; } });
  const toggleAdvanced = (v: boolean) => { setAdvanced(v); try { localStorage.setItem('sourceloop.studio.advanced', v ? '1' : '0'); } catch { /* per-visit only */ } };
  const nav = useNavigate();
  const qc = useQueryClient();
  const role = useAuth((s) => s.me?.role);
  const canEdit = can.editDraft(role);
  const isAdmin = can.adminStudio(role);

  const schemaQ = useQuery({ queryKey: ['studio', 'schema'], queryFn: studio.schema, staleTime: Infinity });
  const draftQ = useQuery({ queryKey: ['studio', 'draft'], queryFn: studio.draft, staleTime: Infinity, refetchOnMount: 'always' });

  const [config, setConfig] = useState<StudioConfigValue | null>(null);
  const [save, setSave] = useState<Save>('saved');
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [report, setReport] = useState<ValidationReport>(EMPTY);
  const [validating, setValidating] = useState(false);
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [publishOpen, setPublishOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const cfgRef = useRef<StudioConfigValue | null>(null);
  const dirtyRef = useRef(false);
  const saveT = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const valT = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const valSeq = useRef(0);

  useEffect(() => {
    if (draftQ.data && !dirtyRef.current) {
      setConfig(draftQ.data.config);
      cfgRef.current = draftQ.data.config;
      setSavedAt(draftQ.data.updated_at);
      setSave('saved');
      void runValidate(draftQ.data.config);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftQ.data]);

  const runValidate = useCallback(async (cfg: StudioConfigValue) => {
    const my = ++valSeq.current;
    setValidating(true);
    try {
      const r = await studio.validate(cfg);
      if (my === valSeq.current) setReport({ errors: r.errors ?? [], warnings: r.warnings ?? [] });
    } catch (e) {
      const r = reportFromError(e);
      if (r && my === valSeq.current) setReport(r);
      else if (my === valSeq.current && !(e instanceof ApiError && e.status === 501)) toast.error(`Validation unavailable: ${errorMessage(e)}`);
    } finally {
      if (my === valSeq.current) setValidating(false);
    }
  }, []);

  const flush = useCallback(async () => {
    clearTimeout(saveT.current);
    if (!dirtyRef.current || !cfgRef.current) return;
    setSave('saving');
    try {
      const snapshot = cfgRef.current;
      const r = await studio.saveDraft(snapshot);
      if (cfgRef.current === snapshot) dirtyRef.current = false;
      setSave(dirtyRef.current ? 'dirty' : 'saved');
      setSavedAt(r?.updated_at ?? new Date().toISOString());
    } catch (e) {
      setSave('error');
      toast.error(`Draft not saved: ${errorMessage(e)}`);
      throw e;
    }
  }, []);

  const edit = useCallback((next: StudioConfigValue) => {
    cfgRef.current = next;
    dirtyRef.current = true;
    setConfig(next);
    setSave('dirty');
    clearTimeout(saveT.current);
    saveT.current = setTimeout(() => { void flush().catch(() => undefined); }, 1000);
    clearTimeout(valT.current);
    valT.current = setTimeout(() => { void runValidate(next); }, 600);
  }, [flush, runValidate]);

  useEffect(() => () => { clearTimeout(valT.current); if (dirtyRef.current) void flush().catch(() => undefined); }, [flush]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirtyRef.current) e.preventDefault(); };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, []);

  const reload = useCallback(async () => {
    dirtyRef.current = false;
    await qc.invalidateQueries({ queryKey: ['studio', 'draft'] });
    await draftQ.refetch();
  }, [qc, draftQ]);

  const errorMap = useMemo(() => {
    const m = new Map<string, Issue[]>();
    for (const i of [...report.errors, ...report.warnings.map((w) => ({ ...w, _w: true }))]) {
      const k = normalizeErrorPath(i.path);
      m.set(k, [...(m.get(k) ?? []), i]);
    }
    return m;
  }, [report]);

  const invariants = useMemo(() => new Map<string, Invariant>((schemaQ.data?.invariants ?? []).map((i) => [i.id, i])), [schemaQ.data]);

  const ctx: FormCtxValue | null = useMemo(() => {
    if (!schemaQ.data || !config) return null;
    return {
      root: schemaQ.data.schema, config, readOnly: !canEdit, isAdmin,
      errorsAt: (p) => errorMap.get(p) ?? [],
      errorsUnder: (p) => [...errorMap.entries()].filter(([k]) => isSameOrChild(p, k)).flatMap(([, v]) => v),
      invariants,
      modelsFor: (prov) => models[prov] ?? [],
      discover: async (prov) => {
        try { const r = await studio.testProvider(prov); if (r.ok) { setModels((m) => ({ ...m, [prov]: r.models })); toast.ok(`${r.models.length} models on ${prov}`); } else toast.error(r.error ?? 'Discovery failed'); }
        catch (e) { toast.error(errorMessage(e)); }
      },
    };
  }, [schemaQ.data, config, canEdit, isAdmin, errorMap, invariants, models]);

  if (schemaQ.isLoading || draftQ.isLoading) return <div style={{ padding: 20 }}><SkeletonList rows={6} /></div>;
  if (schemaQ.isError || draftQ.isError) {
    const e = schemaQ.error ?? draftQ.error;
    return <div style={{ padding: 20 }}>{e instanceof ApiError && e.status === 501
      ? <div className="callout warn"><Icon name="info" /><div><b>Studio API is not available on this server yet.</b><div className="muted">The backend answers 501 for /studio. Start the web app with VITE_MOCK_STUDIO=1 to work against fixtures.</div></div></div>
      : <ErrorBox error={e} onRetry={() => { void schemaQ.refetch(); void draftQ.refetch(); }} />}</div>;
  }
  if (!ctx || !config || !schemaQ.data) return null;

  const allSections = sectionsOf(schemaQ.data.schema);
  // Basic view keeps the few sections most people touch; the current section always stays visible so a deep link still works
  const sections = advanced ? allSections : allSections.filter((x) => BASIC_SECTIONS.has(x.key) || x.key === section);
  const errCount = (key: string) => report.errors.filter((e) => isSameOrChild(key, normalizeErrorPath(e.path))).length;
  const cur = allSections.find((s) => s.key === section);

  const jump = (path: string) => {
    const top = normalizeErrorPath(path).split('.')[0] ?? '';
    if (top !== section) nav(`/studio/${top}`);
    setTimeout(() => document.querySelector(`[data-path="${CSS.escape(normalizeErrorPath(path))}"]`)?.scrollIntoView({ block: 'center' }), 150);
  };
  const statusText = save === 'saving' ? 'Saving…' : save === 'dirty' ? 'Unsaved changes' : save === 'error' ? 'Save failed' : savedAt ? 'Draft saved' : 'Draft';

  return (
    <FormCtx.Provider value={ctx}>
      <div className="studio">
        <nav className="studio-nav" aria-label="Studio sections">
          <label className="row hint" style={{ padding: '4px 8px', gap: 6, cursor: 'pointer' }}>
            <input type="checkbox" checked={advanced} onChange={(e) => toggleAdvanced(e.target.checked)} /> Show advanced sections
          </label>
          {sections.map((s) => (
            <button key={s.key} aria-current={section === s.key ? 'page' : undefined} onClick={() => nav(`/studio/${s.key}`)}>
              {s.title.replace('Llm', 'LLM roles & models').replace('Data sources', 'Data sources')}
              {errCount(s.key) > 0 && <span className="badge bad cnt">{errCount(s.key)}</span>}
            </button>
          ))}
          <hr style={{ width: '100%', border: 0, borderTop: '1px solid var(--border)', margin: '6px 0' }} />
          <button aria-current={section === 'versions' ? 'page' : undefined} onClick={() => nav('/studio/versions')}>Versions</button>
          <button aria-current={section === 'invariants' ? 'page' : undefined} onClick={() => nav('/studio/invariants')}><Icon name="lock" style={{ width: 14, height: 14 }} />Invariants</button>
        </nav>

        <div className="studio-main">
          <div className="studio-bar">
            <span className={`badge ${save === 'error' ? 'bad' : save === 'saved' ? 'ok' : 'warn'}`} role="status" aria-live="polite">{statusText}</span>
            {save === 'error' && <button className="btn sm" onClick={() => void flush().catch(() => undefined)}>Retry save</button>}
            <span className={`badge ${report.errors.length ? 'bad' : 'ok'}`} role="status">
              {validating ? 'Validating…' : report.errors.length ? `${report.errors.length} error${report.errors.length > 1 ? 's' : ''}` : 'Valid'}{report.warnings.length > 0 && !validating ? ` · ${report.warnings.length} warning${report.warnings.length > 1 ? 's' : ''}` : ''}
            </span>
            {!canEdit && <Badge tone="info">Read-only</Badge>}
            <span className="spacer" />
            <button className="btn sm" onClick={() => void exportBundle()}><Icon name="download" />Export</button>
            {isAdmin && <button className="btn sm" onClick={() => setImportOpen(true)}><Icon name="upload" />Import</button>}
            <button className="btn primary sm" disabled={!isAdmin} title={isAdmin ? undefined : 'Only ADMIN can publish'} onClick={() => setPublishOpen(true)}>Publish…</button>
          </div>
          <div className="studio-content">
            {section === 'versions' ? <Versions isAdmin={isAdmin} onRolledBack={() => void reload()} /> :
              section === 'invariants' ? <InvariantsPage invariants={schemaQ.data.invariants} hits={report.errors} /> :
                cur ? (
                  <>
                    <div style={{ marginBottom: 14 }}><h1>{cur.title}</h1>{cur.spec.description && <p className="muted">{cur.spec.description}</p>}</div>
                    <SchemaForm key={cur.key} node={(schemaQ.data.schema.properties ?? {})[cur.key] ?? {}} path={[cur.key]} value={getIn(config, [cur.key])}
                      onChange={(v) => edit(setIn(config, [cur.key], v))} title={cur.title} />
                    {cur.key === 'providers' && <ProvidersPanel isAdmin={isAdmin} onModels={(n, m) => setModels((x) => ({ ...x, [n]: m }))} />}
                  </>
                ) : <Navigate to="/studio/providers" replace />}
          </div>
        </div>

        <aside className="studio-side" aria-label="Validation and invariants">
          <h4>Validation</h4>
          <div className="stack" style={{ margin: '8px 0 16px' }}>
            {report.errors.length === 0 && report.warnings.length === 0 && <p className="hint">No issues.</p>}
            {[...report.errors.map((i) => [i, 'bad'] as const), ...report.warnings.map((i) => [i, 'warn'] as const)].map(([i, tone], n) => (
              <button key={n} className={`callout ${tone}`} style={{ textAlign: 'left', cursor: 'pointer', font: 'inherit', color: 'inherit' }} onClick={() => jump(i.path)}>
                <Icon name={tone === 'bad' ? 'alert' : 'info'} /><div><code>{i.path || '(root)'}</code><div>{i.message}</div>{i.invariant_id && <div className="muted">{i.invariant_id}</div>}</div>
              </button>
            ))}
          </div>
          <h4>Invariants (read-only)</h4>
          <p className="hint" style={{ margin: '4px 0 6px' }}>Enforced in code at publish and at runtime. No setting can turn them off.</p>
          {schemaQ.data.invariants.map((inv) => (
            <div key={inv.id} className={`inv ${report.errors.some((e) => e.invariant_id === inv.id) ? 'hit' : ''}`}>
              <Icon name="lock" /><div><b style={{ fontSize: 12.5 }}>{inv.title ?? inv.description}</b><div className="hint">{inv.title ? inv.description : inv.reason}</div></div>
            </div>
          ))}
        </aside>
      </div>
      {publishOpen && <PublishDialog onClose={() => setPublishOpen(false)} onStale={() => void reload()} flush={flush} />}
      {importOpen && <ImportDialog onClose={() => setImportOpen(false)} onDone={() => void reload()} />}
    </FormCtx.Provider>
  );
}

async function exportBundle() {
  try {
    const b = await studio.exportBundle();
    const url = URL.createObjectURL(new Blob([JSON.stringify(b, null, 2)], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = `studio-config-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { toast.error(errorMessage(e)); }
}

function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [rep, setRep] = useState<ValidationReport | null>(null);
  let parsed: unknown = null, bad = false;
  if (text.trim()) { try { parsed = JSON.parse(text); } catch { bad = true; } }
  const run = async () => {
    setBusy(true); setRep(null);
    try { await studio.importBundle(parsed); toast.ok('Imported into the draft. Review and publish.'); onDone(); onClose(); }
    catch (e) { const r = reportFromError(e); if (r) setRep(r); else toast.error(errorMessage(e)); }
    finally { setBusy(false); }
  };
  return (
    <Dialog title="Import configuration" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={!parsed || bad || busy} onClick={() => void run()}>{busy ? 'Importing…' : 'Import into draft'}</button></>}>
      <div className="stack">
        <p className="hint">Imports replace the current draft after server-side validation. Provider keys are never part of a bundle.</p>
        <input type="file" accept="application/json,.json" aria-label="Choose bundle file" onChange={(e) => { const f = e.target.files?.[0]; if (f) void f.text().then(setText); }} />
        <textarea className="textarea" aria-label="Bundle JSON" placeholder="…or paste a bundle" value={text} onChange={(e) => setText(e.target.value)} aria-invalid={bad} />
        {bad && <span className="err-text">Not valid JSON.</span>}
        {rep && <><IssueList items={rep.errors} tone="bad" /><IssueList items={rep.warnings} tone="warn" /></>}
      </div>
    </Dialog>
  );
}

function InvariantsPage({ invariants, hits }: { invariants: Invariant[]; hits: Issue[] }) {
  return (
    <div>
      <h1>Invariants</h1>
      <p className="muted" style={{ margin: '4px 0 14px' }}>These safety properties live in code (<code>studio/invariants.py</code>). They are enforced when you publish and again at runtime. There is deliberately no field that can switch them off.</p>
      <div className="card pad">
        {invariants.map((inv) => (
          <div key={inv.id} className={`inv ${hits.some((h) => h.invariant_id === inv.id) ? 'hit' : ''}`}>
            <Icon name="lock" /><div><b>{inv.title ?? inv.description}</b> <code className="muted">{inv.id}</code><div className="hint">{inv.title ? inv.description : inv.reason}</div></div>
          </div>
        ))}
      </div>
    </div>
  );
}
