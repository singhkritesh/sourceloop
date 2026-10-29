import { useMemo } from 'react';
import type { BuyerOverview as Ov, Candidate, Project } from '../api/types';
import { approvalTitle, qualText, reasonText, roleTitle, stageName } from '../lib/friendly';
import { offersToMessages, priceText } from '../lib/offers';
import { NegotiationReview } from './NegotiationReview';
import { Ladder } from './Ladder';
import { Badge, fmtTime } from './ui';

const QUAL_TONE: Record<string, 'ok' | 'warn' | 'bad' | ''> = { QUALIFIED: 'ok', REVIEW: 'warn', REJECTED: 'bad', BLOCKED: 'bad' };
const asList = (v: unknown): string => (Array.isArray(v) ? v.join(', ') : v == null ? '' : String(v));

function Need({ p }: { p: Project }) {
  const b = (p.brief ?? {}) as Record<string, unknown>;
  const rows: [string, string][] = [
    ['Product category', asList(b.category).replace(/_/g, ' ')], ['Industry', asList(b.industry).replace(/_/g, ' ')],
    ['Volume', b.volume_units != null ? `${b.volume_units} units` : ''], ['Certificates required', asList(b.required_certificates)],
    ['Countries excluded', asList(b.excluded_countries) || 'none'], ['Longest acceptable lead time', b.max_lead_time_weeks != null ? `${b.max_lead_time_weeks} weeks` : ''],
    ['Target unit price', priceText(b.target_unit_price as string | undefined)],
  ].filter((r): r is [string, string] => !!r[1]);
  return (
    <section className="card pad stack" aria-label="What you asked for">
      <h2>What you asked for</h2>
      {p.need_text && <p className="text-2">{p.need_text}</p>}
      {rows.length > 0 && <dl className="facts">{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
    </section>
  );
}

function Candidates({ list }: { list: Candidate[] }) {
  if (!list.length) return (
    <section className="card pad" aria-label="Candidate suppliers"><h2>Candidate suppliers</h2>
      <p className="muted" style={{ marginTop: 6 }}>Suppliers appear here once the agents have searched the network and screened them.</p></section>
  );
  return (
    <section className="card" aria-label="Candidate suppliers">
      <div className="card-head"><h2>Candidate suppliers</h2><span className="hint">{list.length} invited to quote</span></div>
      <table className="table">
        <thead><tr><th>Supplier</th><th>Country</th><th>Screening</th><th>Offers</th><th>Latest price</th></tr></thead>
        <tbody>
          {list.map((c) => (
            <tr key={c.supplier_id}>
              <td><b>{c.name}</b> {c.awarded && <Badge tone="ok">awarded</Badge>}<div className="muted mono" style={{ fontSize: 11 }}>{c.supplier_id}</div></td>
              <td>{c.country}</td>
              <td>
                {c.qualification ? <Badge tone={QUAL_TONE[c.qualification] ?? ''}>{qualText(c.qualification)}</Badge> : <span className="muted">not screened yet</span>}
                {c.reasons.length > 0 && <ul className="reason-list">{c.reasons.map((r) => <li key={r}>{reasonText(r)}</li>)}</ul>}
              </td>
              <td className="num">{c.rounds ? `${c.rounds} round${c.rounds > 1 ? 's' : ''}` : <span className="muted">none yet</span>}</td>
              <td className="num">{c.latest_offer ? <><b>{priceText(c.latest_offer.unit_price)}</b><div className="muted" style={{ fontSize: 12 }}>{c.latest_offer.lead_time_weeks} wk lead time</div></> : <span className="muted">-</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function BuyerOverview({ project, data }: { project: Project; data: Ov }) {
  const names = useMemo(() => Object.fromEntries(data.candidates.map((c) => [c.supplier_id, c.name])), [data.candidates]);
  const msgs = useMemo(() => offersToMessages(data.offers, data.award?.supplier_id), [data.offers, data.award]);
  return (
    <div className="stack biz">
      <Need p={project} />
      <Candidates list={data.candidates} />
      {data.offers.length > 0 && (
        <section className="card pad stack" aria-label="How prices moved">
          <div><h2>How prices moved</h2><p className="hint">Each line is one supplier. Dots are the supplier&rsquo;s quotes, squares are your counter-offers.</p></div>
          <Ladder messages={msgs} names={names} />
        </section>
      )}
      <NegotiationReview turns={data.negotiation_review} names={names} />
      {(data.award || data.contract) && (
        <section className="card pad stack" aria-label="Outcome">
          <h2>{data.contract?.signed ? 'Deal signed' : data.award ? 'Awarded' : 'Contract'}</h2>
          {data.award && (
            <p><b>{data.award.name}</b>{data.award.unit_price ? <> at <b>{priceText(data.award.unit_price)}</b> per unit</> : null}{data.award.lead_time_weeks ? <>, {data.award.lead_time_weeks} weeks lead time</> : null}.</p>
          )}
          {data.contract && (
            <div className="stack">
              <div className="row wrap"><Badge tone={data.contract.signed ? 'ok' : 'info'}>{data.contract.signed ? 'signed' : data.contract.status.toLowerCase()}</Badge>
                <span className="muted">{data.contract.clauses.length} clauses</span>
                {!!data.contract.deviations?.length && <Badge tone="warn">{data.contract.deviations.length} deviation{data.contract.deviations.length > 1 ? 's' : ''} from the standard terms</Badge>}</div>
              <div className="chips">{data.contract.clauses.map((c, i) => <span key={i} className="chip">{typeof c === 'string' ? c.replace(/_/g, ' ') : JSON.stringify(c)}</span>)}</div>
            </div>
          )}
        </section>
      )}
      <div className="split2">
        <section className="card pad stack" aria-label="Decisions">
          <h2>Decisions</h2>
          {data.decisions.length === 0 ? <p className="muted">People will be asked to decide at the key points. Their decisions are listed here.</p> : (
            <ol className="plain-list">
              {data.decisions.map((d, i) => (
                <li key={i}>
                  <div className="row wrap"><b>{approvalTitle(d.kind)}</b>
                    <Badge tone={d.status === 'PENDING' ? 'warn' : d.status === 'REJECTED' ? 'bad' : 'ok'}>{d.status === 'PENDING' ? `waiting for ${roleTitle(d.role)}` : d.status.toLowerCase()}</Badge></div>
                  <div className="muted" style={{ fontSize: 12.5 }}>{roleTitle(d.role)}{d.resolved_at ? ` · ${fmtTime(d.resolved_at)}` : ''}</div>
                  {d.comment && <div className="quote">&ldquo;{d.comment}&rdquo;</div>}
                </li>
              ))}
            </ol>
          )}
        </section>
        <section className="card pad stack" aria-label="Progress">
          <h2>Progress</h2>
          {data.milestones.length === 0 ? <p className="muted">Nothing has happened yet.</p> : (
            <ol className="plain-list">{data.milestones.map((m) => <li key={m.stage}><b>{stageName(m.stage)}</b><div className="muted" style={{ fontSize: 12.5 }}>{fmtTime(m.at)}</div></li>)}</ol>
          )}
        </section>
      </div>
    </div>
  );
}
