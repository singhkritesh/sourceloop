import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, errorMessage } from '../api/http';
import { studio } from '../api/studio';
import type { Issue, ValidationReport } from '../api/studioTypes';
import { Icon } from '../components/Icon';
import { Dialog } from '../components/ui';
import { toast } from '../state/ui';

export function reportFromError(e: unknown): ValidationReport | null {
  if (!(e instanceof ApiError) || typeof e.body !== 'object' || !e.body) return null;
  const b = e.body as Record<string, unknown>;
  const r = (b.report ?? (b.error as Record<string, unknown> | undefined)?.report ?? b) as Partial<ValidationReport>;
  return Array.isArray(r.errors) ? { errors: r.errors, warnings: r.warnings ?? [] } : null;
}

export function IssueList({ items, tone }: { items: Issue[]; tone: 'bad' | 'warn' }) {
  if (!items.length) return null;
  return (
    <div className={`callout ${tone}`}><Icon name={tone === 'bad' ? 'alert' : 'info'} />
      <ul className="err-list grow">{items.map((i, n) => <li key={n}><code>{i.path || '(root)'}</code> {i.message}{i.invariant_id && <span className="badge warn" style={{ marginLeft: 6 }}><Icon name="lock" style={{ width: 11, height: 11 }} />{i.invariant_id}</span>}</li>)}</ul>
    </div>
  );
}

export function PublishDialog({ onClose, onStale, flush }: { onClose: () => void; onStale: () => void; flush: () => Promise<void> }) {
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const [rep, setRep] = useState<ValidationReport | null>(null);
  const [stale, setStale] = useState(false);
  const v = useQuery({ queryKey: ['studio', 'validate-publish'], queryFn: async () => { await flush(); return studio.validate(); }, staleTime: 0, gcTime: 0 });
  const report = rep ?? v.data ?? null;
  const publish = useMutation({
    mutationFn: async () => { await flush(); return studio.publish(note.trim()); },
    onSuccess: (r) => { toast.ok(`Published v${r.version}`); void qc.invalidateQueries({ queryKey: ['studio'] }); onClose(); },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) { setStale(true); return; }
      const r = reportFromError(e);
      if (r && e instanceof ApiError && e.status === 422) setRep(r); else toast.error(errorMessage(e));
    },
  });
  const blocked = !!report && report.errors.length > 0;
  return (
    <Dialog title="Publish configuration" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={publish.isPending || v.isLoading || blocked || stale || note.trim().length < 3} onClick={() => publish.mutate()}>{publish.isPending ? 'Publishing…' : 'Publish new version'}</button></>}>
      <div className="stack">
        <p className="hint">Publishing creates an immutable version. New projects pin it; running projects keep theirs.</p>
        <div className="field"><label htmlFor="pn">Release note</label><input id="pn" className="input" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changed and why" /></div>
        {v.isLoading && <p className="muted">Validating draft…</p>}
        {report && report.errors.length === 0 && <div className="callout ok"><Icon name="check" /><div>Draft is valid.{report.warnings.length > 0 && ` ${report.warnings.length} warning(s) below.`}</div></div>}
        {report && <IssueList items={report.errors} tone="bad" />}
        {report && <IssueList items={report.warnings} tone="warn" />}
        {stale && <div className="callout warn" role="alert"><Icon name="alert" /><div className="grow"><b>The draft is stale.</b> Another version was published or the draft changed elsewhere.
          <div style={{ marginTop: 6 }}><button className="btn sm" onClick={() => { onStale(); onClose(); }}>Reload latest draft</button></div></div></div>}
      </div>
    </Dialog>
  );
}
