import { useMemo, useState } from 'react';
import type { ProjectMessage } from '../api/types';
import { laneOf, type FeedItem } from '../lib/feed';
import { approvalTitle, messageTypeText, reasonText, stageBlurb, stageName } from '../lib/friendly';
import type { CallRec } from '../lib/runReducer';
import { Icon } from './Icon';
import { MessageCard } from './MessageCard';

type MsgItem = Extract<FeedItem, { kind: 'message' }>;

function CallChip({ c }: { c: CallRec }) {
  const low = c.done && (c.confidence ?? 0) < 0.6;
  const pct = Math.round((c.confidence ?? 0) * 100);
  return (
    <div className={`chip ${low ? 'low' : ''} ${c.done ? '' : 'pending'}`} title={c.inputSummary || undefined}>
      <Icon name="tool" />
      <span className="trunc"><b>{c.tool}</b></span>
      {c.done ? (
        <>
          <span className="meter" aria-hidden="true"><i style={{ width: `${pct}%` }} /></span>
          <span className="conf num" aria-label={`confidence ${pct} percent`}>{pct}%</span>
          <span className="muted num">{c.sourcesCount ?? 0} src</span>
          {low && <span className="muted">low confidence</span>}
          {c.latencyMs !== undefined && <span className="muted num">{c.latencyMs}ms</span>}
        </>
      ) : <span>running…</span>}
    </div>
  );
}

const arrowFor = (from: string, to: string): '→' | '←' | '' => {
  const f = laneOf(from), t = laneOf(to);
  if (f === 'buyer') return '→';
  if (f === 'supplier') return '←';
  return t === 'buyer' ? '←' : t === 'supplier' ? '→' : '';
};

function Row({ lane, mid, children }: { lane: 'buyer' | 'orchestrator' | 'supplier'; mid?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="frow">
      <div className="cell buyer">{lane === 'buyer' && children}</div>
      <div className="mid">{lane === 'orchestrator' ? children : mid}</div>
      <div className="cell supplier">{lane === 'supplier' && children}</div>
    </div>
  );
}

function MsgRow({ it, priceOf }: { it: MsgItem; priceOf: (id: string) => string | undefined }) {
  const from = it.msg?.from_agent ?? it.meta?.from ?? '';
  const to = it.msg?.to_agent ?? it.meta?.to ?? '';
  const lane = laneOf(from);
  return (
    <Row lane={lane} mid={<span className="arrow mono muted" aria-hidden="true" style={{ textAlign: 'center' }}>{arrowFor(from, to)}</span>}>
      <MessageCard msg={it.msg} meta={it.meta} priceOf={priceOf} />
    </Row>
  );
}

export function Feed({ items, priceOf, onApproval }: { items: FeedItem[]; priceOf: (id: string) => string | undefined; onApproval: (id: string) => void }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <div className="feed" role="log" aria-label="Conversation">
      {items.map((it) => {
        switch (it.kind) {
          case 'stage':
            return <div className="stage-row" key={it.key}><span>{it.stage.from === it.stage.to ? 'Start' : 'Now'}: {stageName(it.stage.to)}{stageBlurb(it.stage.to) ? ` \u2014 ${stageBlurb(it.stage.to).toLowerCase()}` : ''}</span></div>;
          case 'call':
            return <Row key={it.key} lane={it.call.agent === 'buyer' || !it.call.agent.startsWith('supplier') ? 'buyer' : 'supplier'}><CallChip c={it.call} /></Row>;
          case 'block':
            return (
              <Row key={it.key} lane="orchestrator">
                <div className="callout bad" role="alert"><Icon name="shield" /><div><b>A limit was enforced</b><div>{it.block.ruleId ? reasonText(it.block.ruleId) : 'A rule stopped this message'} <span className="mono muted">{it.block.ruleId ?? ''}</span></div><div className="muted">{it.block.messageType ?? 'message'} from {it.block.side.replace('supplier:', '') || 'sender'} not delivered</div></div></div>
              </Row>
            );
          case 'escalation':
            return (
              <Row key={it.key} lane="orchestrator">
                <div className={`callout ${it.esc.resolved ? 'ok' : 'warn'}`}>
                  <Icon name={it.esc.resolved ? 'check' : 'alert'} />
                  <div>
                    <b>{it.esc.resolved ? `Decision made: ${it.esc.resolved.toLowerCase()}` : 'Asked a person to decide'}</b>
                    <div>{approvalTitle(it.esc.kind)}{it.esc.targetRole ? ` \u00b7 for ${it.esc.targetRole.toLowerCase()}` : ''}</div>
                    {it.esc.reasons.length > 0 && <div className="muted">{it.esc.reasons.map(reasonText).join('; ')}</div>}
                    {it.esc.approvalId && <button className="btn sm" style={{ marginTop: 4 }} onClick={() => onApproval(it.esc.approvalId)}>Open approval</button>}
                  </div>
                </div>
              </Row>
            );
          case 'message':
            return <MsgRow key={it.key} it={it} priceOf={priceOf} />;
          case 'group': {
            const first = it.items[0] as MsgItem;
            const t = first.msg?.type ?? first.meta?.type ?? 'MESSAGE';
            const from = first.msg?.from_agent ?? first.meta?.from ?? '';
            const lane = laneOf(from);
            const fanOut = lane === 'buyer';
            const isOpen = !!open[it.key];
            return (
              <div key={it.key}>
                <Row lane={lane} mid={<span className="arrow mono muted" aria-hidden="true" style={{ textAlign: 'center' }}>{arrowFor(from, first.msg?.to_agent ?? first.meta?.to ?? '')}</span>}>
                  <button className="group-btn" aria-expanded={isOpen} onClick={() => setOpen((o) => ({ ...o, [it.key]: !isOpen }))}>
                    <b>{it.items.length} × {messageTypeText(t).toLowerCase()}</b> <span className="muted">{fanOut ? 'to' : 'from'} {it.items.length} {fanOut ? 'recipients' : 'suppliers'} · {isOpen ? 'hide' : 'show'}</span>
                  </button>
                </Row>
                {isOpen && it.items.map((m) => <MsgRow key={m.key} it={m} priceOf={priceOf} />)}
              </div>
            );
          }
        }
      })}
    </div>
  );
}

export function useSupplierIds(messages: ProjectMessage[]): string[] {
  return useMemo(() => {
    const s = new Set<string>();
    for (const m of messages) for (const a of [m.from_agent, m.to_agent]) if (a.startsWith('supplier:')) s.add(a.slice(9));
    return [...s].sort();
  }, [messages]);
}
