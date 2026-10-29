import { Fragment, type ReactNode } from 'react';
import type { ProjectMessage } from '../api/types';
import type { MsgMeta } from '../lib/runReducer';
import { isAiGenerated } from '../lib/aiDisclosure';
import { messageTypeText, qualText, reasonText } from '../lib/friendly';
import { Badge } from './ui';

const short = (a: string) => (a.startsWith('supplier:') ? a.slice(9) : a);
const list = (v: unknown): string => (Array.isArray(v) ? v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') : String(v ?? ''));
const money = (v: unknown): string => (v == null ? '' : String(v));

type KV = [string, ReactNode];

/** All values are rendered as React text nodes (never HTML): agent / news text is untrusted. */
export function summarize(m: ProjectMessage, priceOf?: (offerId: string) => string | undefined): { kv: KV[]; headline?: ReactNode } {
  const p = m.payload;
  switch (m.type) {
    case 'BRIEF':
      return { kv: [['Category', String(p.category ?? '')], ['Industry', String(p.industry ?? '')], ['Volume', String(p.volume_units ?? '')], ['Certificates', list(p.required_certificates)],
        ['Excluded', list(p.excluded_countries) || 'none'], ['Max lead time', `${p.max_lead_time_weeks} wk`], ['Target price', money(p.target_unit_price)]] };
    case 'INVITE':
    case 'RFQ':
      return { kv: [['Category', String(p.category ?? '')], ['Volume', String(p.volume_units ?? '')], ['Certificates', list(p.required_certificates) || '-'], ['Lead time', `${p.max_lead_time_weeks ?? p.target_lead_time_weeks} wk`]] };
    case 'INTEREST_REPLY':
      return { kv: [['Interested', p.interested ? 'yes' : 'no'], ['Capacity', String(p.capacity_units ?? '-')], ...(p.reason_code ? [['Reason', reasonText(String(p.reason_code))] as KV] : [])] };
    case 'DOC_REQUEST':
      return { kv: [['Certificates required', list(p.required_certificates)]] };
    case 'DOC_SUBMISSION':
      return { kv: [['Certificates claimed', list((p.certificates as Array<{ type: string }> | undefined)?.map((c) => c.type))]] };
    case 'QUAL_RESULT': {
      const st = String(p.status ?? '');
      return { headline: <Badge tone={st === 'QUALIFIED' ? 'ok' : st === 'REVIEW' ? 'warn' : 'bad'}>{qualText(st)}</Badge>, kv: [['Reasons', Array.isArray(p.reasons) && p.reasons.length ? p.reasons.map((r) => reasonText(String(r))).join('; ') : 'none']] };
    }
    case 'OFFER':
    case 'COUNTER':
      return { headline: <span className="num" style={{ fontSize: 18, fontWeight: 700 }}>{money(p.unit_price)}</span>,
        kv: [['Round', String(p.round ?? '')], ['Lead time', `${p.lead_time_weeks} wk`], ['Capacity', String(p.capacity_units ?? '')], ...(p.note ? [['Note', String(p.note)] as KV] : [])] };
    case 'ACCEPT': {
      const price = priceOf?.(String(p.offer_id ?? ''));
      return { headline: price ? <span className="num" style={{ fontSize: 16, fontWeight: 700 }}>Accepted at {price}</span> : undefined, kv: price ? [] : [['Offer', String(p.offer_id ?? '').slice(0, 8)]] };
    }
    case 'REJECT':
      return { kv: [['Reason', String(p.reason_code ?? '')]] };
    case 'CONTRACT_DRAFT':
      return { kv: [['Clauses', list(p.clause_keys)], ['Deviations', list(p.deviations) || 'none']] };
    case 'REDLINE':
      return { kv: [['Clause', String(p.clause_key ?? '')], ['Action', String(p.action ?? '')], ...(p.alternative_key ? [['Alternative', String(p.alternative_key)] as KV] : [])] };
    case 'SIGN_REQUEST':
      return { kv: [['Contract', String(p.contract_id ?? '').slice(0, 8)]] };
    case 'ONBOARD_DATA':
      return { kv: Object.keys((p.fields as Record<string, unknown>) ?? {}).map((k) => [k.replace(/_/g, ' '), k.includes('bank') ? 'provided (finance confirms)' : String((p.fields as Record<string, unknown>)[k])] as KV) };
    case 'ESCALATE':
      return { kv: [['Kind', String(p.kind ?? '')], ['Reasons', Array.isArray(p.reasons) ? p.reasons.map((r) => reasonText(String(r))).join('; ') : '']] };
    default:
      return { kv: Object.entries(p).slice(0, 8).map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)] as KV) };
  }
}

export function MessageCard({ msg, meta, priceOf }: { msg?: ProjectMessage; meta?: MsgMeta; priceOf?: (id: string) => string | undefined }) {
  const type = msg?.type ?? meta?.type ?? 'MESSAGE';
  const from = msg?.from_agent ?? meta?.from ?? '';
  const to = msg?.to_agent ?? meta?.to ?? '';
  const s = msg ? summarize(msg, priceOf) : null;
  return (
    <article className={`msg ${msg ? '' : 'pending'}`} aria-label={`${type} from ${from} to ${to}`}>
      <div className="msg-head">
        <span className="msg-type" title={type}>{messageTypeText(type)}</span>
        {s?.headline}
        {isAiGenerated(from, msg?.ai_generated ?? meta?.aiGenerated) && <span title="This message was produced by an AI agent"><Badge tone="info">AI agent</Badge></span>}
        <span className="msg-peer">{short(from)} → {short(to)}</span>
      </div>
      {s && s.kv.length > 0 && <dl className="kv">{s.kv.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>}
      {!msg && <div className="hint">Loading payload…</div>}
    </article>
  );
}
