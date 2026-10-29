import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import { isTerminalStatus } from '../lib/errors';
import type { Project, ProjectMessage } from '../api/types';
import { Icon } from '../components/Icon';
import { Ladder } from '../components/Ladder';
import { Feed, useSupplierIds } from '../components/Split';
import { actionText, reasonText } from '../lib/friendly';
import { Badge, ErrorBox, SkeletonList } from '../components/ui';
import { initialRun } from '../lib/runReducer';
import { buildFeed, groupFanout, type FeedItem } from '../lib/feed';
import { can, useAuth } from '../state/auth';
import { useRunStore, useRunStream } from '../state/run';

async function loadAllMessages(id: string): Promise<ProjectMessage[]> {
  const out: ProjectMessage[] = [];
  let after = 0;
  for (let i = 0; i < 20; i++) {
    const { items } = await projects.messages(id, after);
    out.push(...items);
    if (items.length < 500) break;
    after = (items[items.length - 1] as ProjectMessage).seq;
  }
  return out;
}

/** The agent workflow, as an inspector: buyer agent | referee | supplier agents, every message, and the audit trail. Never shown to business users;
 *  an organization admin gets its own side, SBN's super admin / evaluator everything. The page header, controls and progress live in ProjectHeader. */
export function Workspace({ project, supplierView }: { project: Project; supplierView: boolean }) {
  const id = project.id;
  const nav = useNavigate();
  const isSupplier = supplierView;
  const status = project.status;
  const terminal = isTerminalStatus(status);
  const msgs = useQuery({ queryKey: ['messages', id], queryFn: () => loadAllMessages(id), refetchInterval: status === 'RUNNING' ? 6000 : false });
  useRunStream(id, terminal);

  const storeId = useRunStore((s) => s.projectId);
  const storeRun = useRunStore((s) => s.run);
  const run = useMemo(() => (storeId === id ? storeRun : initialRun()), [storeId, id, storeRun]);
  const conn = useRunStore((s) => s.conn);
  const connInfo = useRunStore((s) => s.connInfo);
  const messages = useMemo(() => msgs.data ?? [], [msgs.data]);
  const priceMap = useMemo(() => new Map(messages.filter((m) => m.payload.unit_price != null).map((m) => [m.id, String(m.payload.unit_price)])), [messages]);
  const priceOf = (oid: string) => priceMap.get(oid);

  const [supplier, setSupplier] = useState('all');
  const suppliers = useSupplierIds(messages);
  const feed = useMemo(() => {
    const f = buildFeed(run, messages);
    if (supplier === 'all') return f;
    const mine = `supplier:${supplier}`;
    const keep = (it: FeedItem): boolean => {
      if (it.kind === 'message') {
        const a = it.msg?.from_agent ?? it.meta?.from ?? '', b = it.msg?.to_agent ?? it.meta?.to ?? '';
        return a === mine || b === mine || (!a.startsWith('supplier:') && !b.startsWith('supplier:'));
      }
      if (it.kind === 'group') return it.items.some(keep);
      return true;
    };
    const flat = f.flatMap((it) => (it.kind === 'group' ? it.items : [it])).filter(keep);
    return groupFanout(flat);
  }, [run, messages, supplier]);

  const [follow, setFollow] = useState(project.status !== 'DONE' && project.status !== 'STOPPED');
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (follow && el) el.scrollTo({ top: el.scrollHeight, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }, [feed.length, follow]);
  const live = feed.length ? feedLabel(feed[feed.length - 1] as FeedItem) : '';

  return (
    <div className="ws wf">
      <div className="wf-bar row wrap">
        <span className="hint">Agent workflow &mdash; how the agents and the referee handled this project</span>
        <span className="spacer" />
        {run.errors.length > 0 && <span className="callout bad" role="alert"><Icon name="alert" /><span>{run.errors[run.errors.length - 1]?.message} ({run.errors[run.errors.length - 1]?.code})</span></span>}
        <span className={`conn ${conn}`} title={connInfo.error ?? undefined}><i />{conn === 'live' ? 'Live' : conn === 'reconnecting' ? `Reconnecting${connInfo.delayMs ? ` in ${Math.round(connInfo.delayMs / 1000)}s` : '…'}` : conn === 'connecting' ? 'Connecting…' : conn === 'closed' ? (terminal ? 'Up to date' : 'Disconnected') : ''}</span>
        <label className="row hint"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Scroll to latest</label>
      </div>
      <div className={`ws-body ${isSupplier ? 'noside' : ''}`}>
        <div className="ws-main" ref={scroller}>
          <span className="sr-only" aria-live="polite">{live}</span>
          {!isSupplier || messages.some((m) => m.type === 'OFFER' || m.type === 'COUNTER') ? (
            <details className="panel" open={messages.some((m) => m.type === 'OFFER')}>
              <summary className="panel-head" style={{ cursor: 'pointer' }}><h3>Negotiation ladder</h3><span className="hint">how each supplier&rsquo;s price moved, round by round</span></summary>
              <div className="panel-body"><Ladder messages={messages} /></div>
            </details>
          ) : null}
          <div className="lane-head" aria-hidden="false">
            <div className="b"><Icon name="folder" />{isSupplier ? 'Buyer' : 'Buyer\u2019s AI agent'}</div>
            <div className="o" title="A neutral rule-based referee, not an AI">Referee (rules)</div>
            <div className="s">{isSupplier ? 'Your AI agent' : 'Supplier AI agents'}
              {suppliers.length > 1 && !isSupplier && (
                <select className="select" style={{ width: 130, height: 26, marginLeft: 'auto' }} value={supplier} onChange={(e) => setSupplier(e.target.value)} aria-label="Filter supplier">
                  <option value="all">All ({suppliers.length})</option>
                  {suppliers.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              )}
            </div>
          </div>
          {msgs.isLoading ? <div style={{ padding: 16 }}><SkeletonList rows={4} h={60} /></div> : msgs.isError ? <div style={{ padding: 16 }}><ErrorBox error={msgs.error} /></div> :
            feed.length === 0 ? <div className="empty">{project.status === 'DRAFT' ? 'Nothing has happened yet. Start the run and you will see each step here as it happens.' : 'Waiting for the first step\u2026'}</div> :
              <Feed items={feed} priceOf={priceOf} onApproval={() => nav('/approvals')} />}
        </div>
        {!isSupplier && <AuditPanel id={id} />}
      </div>
    </div>
  );
}

function feedLabel(it: FeedItem): string {
  switch (it.kind) {
    case 'stage': return `Stage ${it.stage.to}`;
    case 'message': return `Message ${it.msg?.type ?? it.meta?.type ?? ''}`;
    case 'group': return `${it.items.length} messages`;
    case 'block': return `Policy block ${it.block.ruleId ?? ''}`;
    case 'escalation': return `Escalation ${it.esc.kind}`;
    case 'call': return `Subagent ${it.call.tool}`;
  }
}

function AuditPanel({ id }: { id: string }) {
  const role = useAuth((s) => s.me?.role);
  const audit = useRunStore((s) => s.run.audit);
  const finished = useRunStore((s) => s.run.finished);
  const canVerify = can.verifyAudit(role);
  const v = useQuery({ queryKey: ['verify', id], queryFn: () => projects.verify(id), enabled: canVerify && audit.length > 0, staleTime: 0 });
  const refetch = v.refetch;
  useEffect(() => { if (canVerify && finished) void refetch(); }, [finished, canVerify, refetch]);
  const rows = useMemo(() => [...audit].sort((a, b) => b.seq - a.seq).slice(0, 300), [audit]);
  return (
    <aside className="ws-side" aria-label="Audit timeline">
      <div className="panel-head"><h3>Activity record</h3><span className="spacer" /><span className="hint num">{audit.length} events</span></div>
      {canVerify && (
        <div className="panel-body" style={{ paddingTop: 0 }}>
          {v.isLoading ? <Badge>verifying…</Badge> : v.isError ? <Badge tone="bad">verify failed</Badge> : v.data ? (
            v.data.valid
              ? <div className="callout ok" role="status"><Icon name="shield" /><div><b>Hash chain verified</b><div className="muted">Tamper-evident: no event was altered or removed.</div><div className="muted num">{v.data.events} events · head <span className="mono">{v.data.head_hash?.slice(0, 10)}</span></div></div></div>
              : <div className="callout bad" role="alert"><Icon name="alert" /><div><b>Chain broken</b><div>The record was altered. First bad event: #{v.data.first_bad_seq}</div></div></div>
          ) : <span className="hint">Waiting for events…</span>}
          <button className="btn sm" style={{ marginTop: 6 }} onClick={() => void v.refetch()} disabled={v.isFetching}><Icon name="refresh" />Re-verify</button>
        </div>
      )}
      {rows.length === 0 ? <p className="hint" style={{ padding: 14 }}>No events yet.</p> : (
        <details className="log-toggle">
          <summary>Show every event ({audit.length})</summary>
          <ul className="tl">
            {rows.map((a) => (
              <li key={a.seq}>
                <span className="sq num">#{a.seq}</span>
                <span><b>{actionText(a.action)}</b>{a.ruleId && <span className="muted"> · {reasonText(a.ruleId)}</span>}<div className="raw">{a.action}{a.ruleId ? ` · ${a.ruleId}` : ''}</div><div className="h">{a.hash.slice(0, 16)}</div></span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </aside>
  );
}
