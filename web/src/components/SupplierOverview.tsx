import { useMemo } from 'react';
import type { Project, SupplierOverview as Ov } from '../api/types';
import { offersToMessages, priceText } from '../lib/offers';
import { NegotiationReview } from './NegotiationReview';
import { Ladder } from './Ladder';
import { Badge, Empty } from './ui';

/** What a supplier knows about a sourcing request: its own quotes and the buyer's replies, the closing result, and (only if it won) the contract. */
export function SupplierOverview({ project, data }: { project: Project; data: Ov }) {
  const mine = useMemo(() => data.offers.map((o) => ({ ...o, side: o.side === 'you' ? ('supplier' as const) : ('buyer' as const) })), [data.offers]);
  const msgs = useMemo(() => offersToMessages(mine), [mine]);
  const closed = data.outcome != null;
  return (
    <div className="stack biz">
      <section className="card pad stack" aria-label="Status">
        <h2>{closed ? (data.outcome === 'DEAL' ? 'You won this business' : 'This request is closed') : 'Request in progress'}</h2>
        <p className="text-2">
          {closed
            ? data.outcome === 'DEAL' ? 'The buyer awarded this request to you. The contract is below.' : 'The buyer has finished this sourcing request. Thank you for taking part.'
            : 'The buyer is evaluating quotes. You will see their replies here as they arrive.'}
        </p>
        <div className="row wrap"><Badge tone={closed ? (data.outcome === 'DEAL' ? 'ok' : '') : 'accent'}>{closed ? (data.outcome === 'DEAL' ? 'deal' : 'closed') : 'open'}</Badge>
          <span className="muted">Request {project.id.slice(0, 8)}</span></div>
      </section>
      <section className="card pad stack" aria-label="Your quotes and the buyer's replies">
        <h2>Your quotes and the buyer&rsquo;s replies</h2>
        {data.offers.length === 0 ? <Empty title="No quotes yet">When your agent quotes, or the buyer replies, the prices appear here.</Empty> : (
          <>
            <Ladder messages={msgs} />
            <table className="table">
              <thead><tr><th>Round</th><th>From</th><th>Unit price</th><th>Lead time</th></tr></thead>
              <tbody>{data.offers.map((o, i) => <tr key={i}><td>{o.round}</td><td>{o.side === 'you' ? 'You' : 'Buyer'}</td><td className="num"><b>{priceText(o.unit_price)}</b></td><td className="num">{o.lead_time_weeks} wk</td></tr>)}</tbody>
            </table>
          </>
        )}
      </section>
      <NegotiationReview turns={data.negotiation_review} />
      {data.contract && (
        <section className="card pad stack" aria-label="Contract">
          <h2>Contract</h2>
          <div className="row wrap"><Badge tone={data.contract.signed ? 'ok' : 'info'}>{data.contract.signed ? 'signed' : data.contract.status.toLowerCase()}</Badge><span className="muted">{data.contract.clauses.length} clauses</span></div>
          <div className="chips">{data.contract.clauses.map((c, i) => <span key={i} className="chip">{typeof c === 'string' ? c.replace(/_/g, ' ') : JSON.stringify(c)}</span>)}</div>
        </section>
      )}
    </div>
  );
}
