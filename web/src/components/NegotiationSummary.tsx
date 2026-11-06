import { BankCheckView } from './BankCheck';
import { Badge } from './ui';
import type { NegotiationSummary as Summary } from '../api/summaryTypes';

/** What happened in the negotiation, in plain words, built by code from the tamper-evident record. The person reads it, then confirms. */
export function NegotiationSummaryView({ s }: { s: Summary }) {
  return (
    <section className="stack" aria-label="Negotiation summary">
      <h4>Negotiation summary</h4>
      <p className="muted">{s.how_to_read}</p>
      {s.deal && (
        <div className="callout info"><div>
          {s.deal.name && <><b>{s.deal.name}</b>{' · '}</>}
          {s.deal.unit_price && <>agreed price <b className="num">{s.deal.unit_price}</b> per unit</>}
          {s.deal.payment_days != null && <>{' · '}Net {s.deal.payment_days} days</>}
          {s.deal.deviations.length > 0 && <>{' · '}deviations: {s.deal.deviations.join(', ')}</>}
          {s.deal.signed && <>{' · '}signed</>}
        </div></div>
      )}
      {s.your_limits && <p className="muted">Your own limits: target <b className="num">{s.your_limits.target_unit_price}</b>, highest <b className="num">{s.your_limits.highest_unit_price}</b>. Suppliers never see these.</p>}
      {s.threads.length === 0 && <span className="muted">No quotes were exchanged yet.</span>}
      {s.threads.map((t) => (
        <details key={t.supplier_id} open={s.threads.length === 1}>
          <summary><b>{t.name}</b> · {t.rounds} round{t.rounds === 1 ? '' : 's'}
            {t.supplier_moved_pct && <> · quote moved {t.supplier_moved_pct}% from {t.opening_quote} to {t.latest_quote}</>}</summary>
          <ol className="stack" style={{ margin: '8px 0', paddingLeft: 18 }}>
            {t.steps.map((st, i) => <li key={i}>{st.text}{st.why && <span className="muted"> {st.why}</span>}</li>)}
          </ol>
          {t.leverage.length > 0 && <ul className="stack" style={{ margin: '4px 0', paddingLeft: 18 }}>{t.leverage.map((l, i) => <li key={i}>{l.text}</li>)}</ul>}
          {t.conduct.length > 0 && <div className="row wrap">{t.conduct.map((c, i) => <Badge key={i} tone="warn">{c.by}: {c.text}</Badge>)}</div>}
        </details>
      ))}
      {s.payment && <BankCheckView data={s.payment} />}
      {s.assumptions.map((a) => <p key={a} className="muted">Assumption: {a}</p>)}
      <p className="muted num" title="The summary is bound to this fingerprint of the record">Record: {s.record.events} events, latest {s.record.head_hash?.slice(0, 12)}…</p>
    </section>
  );
}
