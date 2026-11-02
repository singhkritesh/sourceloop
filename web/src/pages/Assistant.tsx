import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { approvals } from '../api/endpoints';
import { ApiError, errorMessage } from '../api/http';
import type { Approval } from '../api/types';
import { approvalInfo, approvalTitle, reasonText } from '../lib/friendly';
import { announceNew, enableNotifications, notificationsOn, notificationsSupported } from '../lib/notify';
import { Empty, fmtTime } from '../components/ui';
import { can, useAuth } from '../state/auth';
import { toast } from '../state/ui';

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** The facts a person needs to answer in one tap, in plain words. */
function facts(a: Approval): string[] {
  const ev = obj(a.evidence);
  if (a.kind === 'AWARD') {
    const r = Array.isArray(ev.ranking) ? ev.ranking.map(obj) : [];
    const rec = obj(a.recommendation).suggested;
    return r.slice(0, 4).map((x) => `${x.supplier === rec ? '★ ' : ''}${String(x.supplier)}${x.price ? ` · ${x.price} per unit` : ''}`);
  }
  if (a.kind === 'SUPPLIER_ACCEPT') return [`Price ${String(ev.price ?? '')} per unit`, `Lead time ${String(ev.lead_time_weeks ?? '')} weeks`, ...(Array.isArray(ev.deviations) && ev.deviations.length ? [`Changed clauses: ${ev.deviations.join(', ')}`] : [])];
  if (a.kind === 'COMPLIANCE_FLAG') return Object.entries(obj(ev.flagged)).map(([s, v]) => `${s}: ${(Array.isArray(obj(v).reasons) ? (obj(v).reasons as string[]).map(reasonText).join('; ') : '')}`);
  if (a.kind === 'HOLD_RELEASE') return [`Supplier ${String(ev.supplier ?? '')}`, 'Bank details come from the supplier’s onboarding'];
  if (a.kind === 'CLAUSE') return [`${String(ev.clause ?? '').replace(/_/g, ' ')} · asked by ${String(ev.supplier ?? '')}`];
  return a.reasons.map(reasonText);
}

function Ask({ a, canResolve }: { a: Approval; canResolve: boolean }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<'idle' | 'decline' | 'other'>('idle');
  const [reason, setReason] = useState('');
  const info = approvalInfo(a.kind);
  const m = useMutation({
    mutationFn: (b: { status: 'APPROVED' | 'REJECTED' | 'EDITED'; edit?: Record<string, unknown> }) =>
      approvals.resolve(a.id, { status: b.status, comment: reason.trim(), version: a.version, ...(b.edit ? { edit: b.edit } : {}) }),
    onSuccess: (r) => { toast.ok(`Done: ${r.status.toLowerCase()}`); void qc.invalidateQueries({ queryKey: ['approvals'] }); void qc.invalidateQueries({ queryKey: ['project', a.project_id] }); },
    onError: (e) => { toast.error(e instanceof ApiError && e.status === 409 ? 'This was already decided. It has been reloaded.' : errorMessage(e)); void qc.invalidateQueries({ queryKey: ['approvals'] }); },
  });
  const ranking = a.kind === 'AWARD' && Array.isArray(obj(a.evidence).ranking) ? (obj(a.evidence).ranking as unknown[]).map((r) => String(obj(r).supplier)) : [];
  const rec = String(obj(a.recommendation).suggested ?? '');
  const ok = reason.trim().length >= 10;
  return (
    <div className="chat-row agent">
      <div className="chat-bubble">
        <b>{approvalTitle(a.kind)}</b>
        <p>{info?.ask ?? 'A decision is needed.'}</p>
        <ul className="chat-facts">{facts(a).map((f, i) => <li key={i}>{f}</li>)}</ul>
        <div className="muted" style={{ fontSize: 12 }}>{fmtTime(a.created_at)} · <Link to={`/projects/${a.project_id}`}>open project</Link></div>
        {canResolve && a.status === 'PENDING' && (mode !== 'idle' ? (
          <div className="stack" style={{ marginTop: 8 }}>
            <label className="muted" htmlFor={`why-${a.id}`} style={{ fontSize: 12.5 }}>Why? (at least 10 characters)</label>
            <textarea id={`why-${a.id}`} className="input" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="chat-quick">
              {mode === 'decline' && <button className="btn danger" disabled={!ok || m.isPending} onClick={() => m.mutate({ status: 'REJECTED' })}>Send decline</button>}
              {mode === 'other' && ranking.filter((x) => x !== rec).slice(0, 3).map((x) => (
                <button key={x} className="btn primary" disabled={!ok || m.isPending} onClick={() => m.mutate({ status: 'EDITED', edit: { supplier: x } })}>Award {x}</button>))}
              <button className="btn" onClick={() => setMode('idle')}>Back</button>
            </div>
          </div>
        ) : (
          <div className="chat-quick">
            <button className="btn primary" disabled={m.isPending} onClick={() => m.mutate({ status: 'APPROVED' })}>{a.kind === 'AWARD' && rec ? `Approve ${rec}` : 'Approve'}</button>
            {ranking.length > 1 && <button className="btn" onClick={() => setMode('other')}>Pick another…</button>}
            <button className="btn" disabled={m.isPending} onClick={() => setMode('decline')}>Decline…</button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Decisions as a conversation: each is a short message from your agent with one-tap answers. Phone-first. */
export function Assistant() {
  const role = useAuth((s) => s.me?.role);
  const q = useQuery({ queryKey: ['approvals', 'list'], queryFn: () => approvals.list(), refetchInterval: 8_000 });
  const [notifOn, setNotifOn] = useState(notificationsOn());
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const pending = useMemo(() => items.filter((a) => a.status === 'PENDING'), [items]);
  const done = useMemo(() => items.filter((a) => a.status !== 'PENDING').slice(0, 8), [items]);
  useEffect(() => { announceNew(pending.map((a) => ({ id: a.id, title: approvalTitle(a.kind) }))); }, [pending]);
  const canResolve = can.resolveApprovals(role);
  return (
    <div className="chat">
      <div className="page-head">
        <div className="grow"><h1>Your agent</h1><p>Decisions come to you here, one at a time. Answer with a tap.</p></div>
        {notificationsSupported() && !notifOn && <button className="btn" onClick={() => void enableNotifications().then((g) => { setNotifOn(g); if (!g) toast.warn('Notifications are blocked in this browser.'); })}>Notify me</button>}
        {notifOn && <span className="hint">Notifications on</span>}
      </div>
      {pending.length === 0 ? <div className="card"><Empty icon="inbox" title="Nothing needs you right now">The agents are handling everything else. You will be told when a decision comes up.</Empty></div> : (
        <div className="chat-thread" aria-live="polite">{pending.map((a) => <Ask key={a.id} a={a} canResolve={canResolve} />)}</div>
      )}
      {done.length > 0 && (
        <div className="chat-thread done" aria-label="Recent decisions">
          <h2 className="hint" style={{ margin: '14px 0 6px' }}>Recent</h2>
          {done.map((a) => <div key={a.id} className="chat-row me"><div className="chat-bubble">{approvalTitle(a.kind)}: <b>{a.status.toLowerCase()}</b></div></div>)}
        </div>
      )}
    </div>
  );
}
