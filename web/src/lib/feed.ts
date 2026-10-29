import type { ProjectMessage } from '../api/types';
import type { BlockRec, CallRec, EscRec, MsgMeta, RunState, StageRec } from './runReducer';

export type FeedItem =
  | { kind: 'stage'; key: string; order: number; stage: StageRec }
  | { kind: 'call'; key: string; order: number; call: CallRec }
  | { kind: 'message'; key: string; order: number; id: string; meta?: MsgMeta; msg?: ProjectMessage }
  | { kind: 'block'; key: string; order: number; block: BlockRec }
  | { kind: 'escalation'; key: string; order: number; esc: EscRec }
  | { kind: 'group'; key: string; order: number; items: Extract<FeedItem, { kind: 'message' }>[] };

export type Lane = 'buyer' | 'orchestrator' | 'supplier';
export const laneOf = (agent: string): Lane => (agent === 'buyer' ? 'buyer' : agent.startsWith('supplier:') ? 'supplier' : 'orchestrator');

const rank = { stage: 0, call: 1, message: 2, block: 3, escalation: 4, group: 2 } as const;

export function buildFeed(run: RunState, messages: ProjectMessage[]): FeedItem[] {
  const byId = new Map(messages.map((m) => [m.id, m]));
  const items: FeedItem[] = [];
  for (const s of run.stages) items.push({ kind: 'stage', key: `st${s.seq}`, order: s.seq, stage: s });
  for (const c of run.calls) items.push({ kind: 'call', key: `c${c.key}`, order: c.seq - 0.5, call: c });
  for (const b of run.blocks) items.push({ kind: 'block', key: `b${b.seq}`, order: b.seq, block: b });
  for (const e of run.escalations) items.push({ kind: 'escalation', key: `e${e.seq}`, order: e.seq, esc: e });
  const seen = new Set<string>();
  for (const meta of Object.values(run.msgMeta)) {
    seen.add(meta.id);
    items.push({ kind: 'message', key: `m${meta.id}`, order: meta.auditSeq, id: meta.id, meta, msg: byId.get(meta.id) });
  }
  // REST-only messages (SSE not caught up yet): after everything known, by message seq
  const tail = run.audit.length ? run.lastEventId : 0;
  for (const m of messages) if (!seen.has(m.id)) items.push({ kind: 'message', key: `m${m.id}`, order: tail + 1 + m.seq / 1e6, id: m.id, msg: m });
  items.sort((a, b) => a.order - b.order || rank[a.kind] - rank[b.kind] || a.key.localeCompare(b.key));
  return groupFanout(items);
}

const sameBurst = (a: Extract<FeedItem, { kind: 'message' }>, b: Extract<FeedItem, { kind: 'message' }>): boolean => {
  const ma = a.msg ?? a.meta, mb = b.msg ?? b.meta;
  if (!ma || !mb) return false;
  const typeA = 'type' in ma ? ma.type : '', typeB = 'type' in mb ? mb.type : '';
  const fromA = 'from_agent' in ma ? ma.from_agent : ma.from, fromB = 'from_agent' in mb ? mb.from_agent : mb.from;
  const toA = 'to_agent' in ma ? ma.to_agent : ma.to, toB = 'to_agent' in mb ? mb.to_agent : mb.to;
  if (typeA !== typeB) return false;
  // fan-out (same sender, many recipients) or fan-in (many senders, same recipient)
  return (fromA === fromB && toA !== toB) || (toA === toB && fromA !== fromB);
};

/** collapse bursts of >= 3 same-type messages (fan-out invites, fan-in replies) into one group row */
export function groupFanout(items: FeedItem[], min = 3): FeedItem[] {
  const out: FeedItem[] = [];
  let run: Extract<FeedItem, { kind: 'message' }>[] = [];
  const flush = () => {
    if (run.length >= min) {
      const first = run[0] as Extract<FeedItem, { kind: 'message' }>;
      out.push({ kind: 'group', key: `g${first.key}`, order: first.order, items: run });
    } else out.push(...run);
    run = [];
  };
  for (const it of items) {
    if (it.kind !== 'message') { flush(); out.push(it); continue; }
    const last = run[run.length - 1];
    if (last && !sameBurst(last, it)) flush();
    run.push(it);
  }
  flush();
  return out;
}
