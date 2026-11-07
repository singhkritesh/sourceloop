import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { approvals } from '../api/endpoints';
import { ApprovalDrawer, approvalTone as tone } from '../components/ApprovalDrawer';
import { approvalTitle, reasonText, roleLabel, stageName } from '../lib/friendly';
import { Badge, Empty, ErrorBox, fmtTime, SkeletonList } from '../components/ui';
import { can, useAuth } from '../state/auth';

type Tab = 'PENDING' | 'RESOLVED' | 'ALL';


export function Approvals() {
  const role = useAuth((s) => s.me?.role);
  const [tab, setTab] = useState<Tab>('PENDING');
  const [openId, setOpenId] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const q = useQuery({ queryKey: ['approvals', 'list'], queryFn: () => approvals.list(), refetchInterval: 10_000 });
  const rows = useMemo(() => (q.data?.items ?? []).filter((a) => (tab === 'ALL' ? true : tab === 'PENDING' ? a.status === 'PENDING' : a.status !== 'PENDING')), [q.data, tab]);
  const open = rows.find((a) => a.id === openId) ?? q.data?.items.find((a) => a.id === openId) ?? null;

  useEffect(() => {
    if (openId) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || e.metaKey || e.ctrlKey) return;
      if (e.key === 'j') setCursor((c) => Math.min(rows.length - 1, c + 1));
      else if (e.key === 'k') setCursor((c) => Math.max(0, c - 1));
      else if (e.key === 'Enter' && rows[cursor] && t.tagName !== 'BUTTON') setOpenId(rows[cursor].id);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [rows, cursor, openId]);

  return (
    <>
      <div className="page-head">
        <div className="grow"><h1>Approvals</h1><p>{role === 'EVALUATOR' ? 'Read-only view of every checkpoint.' : 'Decisions the agents need from you. They recommend; you decide, and every decision is recorded.'}</p></div>
        <span className="hint"><span className="kbd">j</span> <span className="kbd">k</span> move · <span className="kbd">Enter</span> open</span>
      </div>
      <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
        {(['PENDING', 'RESOLVED', 'ALL'] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className="tab" onClick={() => { setTab(t); setCursor(0); }}>
            {t[0] + t.slice(1).toLowerCase()}{t === 'PENDING' && q.data ? ` (${q.data.items.filter((a) => a.status === 'PENDING').length})` : ''}
          </button>
        ))}
      </div>
      {q.isLoading ? <SkeletonList rows={4} h={64} /> : q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> :
        rows.length === 0 ? <div className="card"><Empty icon="inbox" title={tab === 'PENDING' ? 'You\u2019re all caught up' : 'Nothing here'}>{tab === 'PENDING' ? 'No decisions are waiting on you. New ones appear here the moment an agent needs a person.' : 'No items match.'}</Empty></div> : (
          <div className="inbox">
            {rows.map((a, i) => (
              <button key={a.id} className={`appr ${i === cursor ? 'sel' : ''}`} onClick={() => { setCursor(i); setOpenId(a.id); }}>
                <span className="appr-tags"><Badge tone={tone(a.status)}>{a.status.toLowerCase()}</Badge>{a.overdue && <Badge tone="bad">OVERDUE</Badge>}</span>
                <div style={{ minWidth: 0 }}>
                  <b className="appr-title">{approvalTitle(a.kind)}</b> <span className="muted">· {stageName(a.stage).toLowerCase()} stage · for {roleLabel(a.target_role)}</span>
                  <div className="muted trunc">{a.reasons.map(reasonText).join('; ') || 'Routine checkpoint'}</div>
                </div>
                <span className="muted num">{fmtTime(a.created_at)}</span>
              </button>
            ))}
          </div>
        )}
      {open && <ApprovalDrawer a={open} onClose={() => setOpenId(null)} canResolve={can.resolveApprovals(role)} />}
    </>
  );
}
