import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage } from '../api/http';
import { studio } from '../api/studio';
import type { DiffEntry, VersionRow } from '../api/studioTypes';
import { Badge, Dialog, Empty, ErrorBox, fmtTime, SkeletonList } from '../components/ui';
import { deepDiff, normalizeDiff } from '../lib/diff';
import { toast } from '../state/ui';

const show = (v: unknown): string => (v === undefined ? '(absent)' : typeof v === 'string' ? v : JSON.stringify(v));

export function Versions({ isAdmin, onRolledBack }: { isAdmin: boolean; onRolledBack: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['studio', 'versions'], queryFn: studio.versions });
  const [pick, setPick] = useState<number[]>([]);
  const [diffOf, setDiffOf] = useState<[number, number] | null>(null);
  const [rb, setRb] = useState<VersionRow | null>(null);
  const rollback = useMutation({
    mutationFn: (v: number) => studio.rollback(v),
    onSuccess: (r) => { toast.ok(`Rolled back: published as v${r.version}`); setRb(null); void qc.invalidateQueries({ queryKey: ['studio'] }); onRolledBack(); },
    onError: (e) => { toast.error(errorMessage(e)); setRb(null); },
  });
  const toggle = (v: number) => setPick((p) => (p.includes(v) ? p.filter((x) => x !== v) : [...p.slice(-1), v]));
  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <h2 className="grow">Published versions</h2>
        <button className="btn" disabled={pick.length !== 2} onClick={() => setDiffOf([Math.min(...pick), Math.max(...pick)] as [number, number])}>Compare selected{pick.length === 2 ? ` (v${Math.min(...pick)} → v${Math.max(...pick)})` : ''}</button>
      </div>
      {q.isLoading ? <SkeletonList rows={3} /> : q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> :
        !q.data?.length ? <div className="card"><Empty title="Nothing published yet">Publish the draft to create version 1.</Empty></div> : (
          <div className="card" style={{ overflow: 'auto' }}>
            <table className="table">
              <thead><tr><th /><th>Version</th><th>Note</th><th>By</th><th>When</th><th>Hash</th><th /></tr></thead>
              <tbody>
                {q.data.map((v) => (
                  <tr key={v.version}>
                    <td><input type="checkbox" aria-label={`Select v${v.version} for compare`} checked={pick.includes(v.version)} onChange={() => toggle(v.version)} /></td>
                    <td><b>v{v.version}</b> {v.active && <Badge tone="ok">active</Badge>} {v.rollback_of != null && <Badge tone="info">rollback of v{v.rollback_of}</Badge>}</td>
                    <td>{v.note || <span className="muted">no note</span>}</td>
                    <td className="muted">{v.created_by ?? '-'}</td>
                    <td className="muted num">{fmtTime(v.created_at)}</td>
                    <td className="mono muted">{v.content_hash.slice(0, 10)}</td>
                    <td>{isAdmin && !v.active && <button className="btn sm" onClick={() => setRb(v)}>Roll back</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {diffOf && <DiffDialog a={diffOf[0]} b={diffOf[1]} onClose={() => setDiffOf(null)} />}
      {rb && (
        <Dialog title={`Roll back to v${rb.version}?`} onClose={() => setRb(null)} footer={<>
          <button className="btn" onClick={() => setRb(null)}>Cancel</button>
          <button className="btn primary" disabled={rollback.isPending} onClick={() => rollback.mutate(rb.version)}>{rollback.isPending ? 'Rolling back…' : 'Roll back'}</button></>}>
          <p>This re-validates v{rb.version} and publishes it as a <b>new</b> version. History is never rewritten. Running projects keep their pinned version.</p>
        </Dialog>
      )}
    </div>
  );
}

export function DiffDialog({ a, b, onClose }: { a: number; b: number; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['studio', 'diff', a, b],
    queryFn: async (): Promise<DiffEntry[]> => {
      try {
        const server = normalizeDiff(await studio.diff(a, b));
        if (server && server.length) return server;
      } catch { /* fall back to a local diff of the two immutable versions */ }
      const [x, y] = await Promise.all([studio.version(a), studio.version(b)]);
      return deepDiff(x.config, y.config);
    },
  });
  return (
    <Dialog title={`Diff v${a} → v${b}`} wide onClose={onClose} footer={<button className="btn" onClick={onClose}>Close</button>}>
      {q.isLoading ? <SkeletonList rows={4} h={30} /> : q.isError ? <ErrorBox error={q.error} /> : !q.data?.length ? <p className="muted">These versions are identical.</p> : (
        <div>
          <p className="hint" style={{ marginBottom: 6 }}>{q.data.length} changed fields</p>
          <div className="diff-row" style={{ fontWeight: 600 }}><span>Field</span><span>Before</span><span>After</span></div>
          {q.data.map((d) => <div className="diff-row" key={d.path}><span className="path">{d.path}</span><span><span className="before">{show(d.before)}</span></span><span><span className="after">{show(d.after)}</span></span></div>)}
        </div>
      )}
    </Dialog>
  );
}
