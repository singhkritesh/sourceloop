import type { Negotiation as Neg, NegStep } from '../api/types';
import { approvalTitle } from '../lib/friendly';
import { priceText } from '../lib/offers';
import { Badge } from './ui';

const MOVE: Record<string, string> = {
  ACCEPT: 'accept', COUNTER_STEADY: 'steady counter', COUNTER_FIRM: 'firm counter', COUNTER_SPLIT: 'split the difference', COUNTER_GENEROUS: 'generous counter', WALK: 'walk away',
};

function Step({ s, last }: { s: NegStep; last: boolean }) {
  const buyer = s.by === 'buyer';
  const who = s.move ? (s.move.source === 'model' ? `the model chose ${MOVE[s.move.chosen] ?? s.move.chosen}${s.move.confidence != null ? ` (${Math.round(s.move.confidence * 100)}% sure)` : ''}` : `the rules chose ${MOVE[s.move.chosen] ?? s.move.chosen}${s.move.fallback ? ' because the model was unsure' : ''}`) : '';
  return (
    <li className={`neg-step ${buyer ? 'buyer' : 'supplier'}`}>
      <div className="neg-bubble">
        <div className="row wrap"><b>{buyer ? 'Your agent' : 'Supplier agent'}</b>
          <span>{s.round === 1 && !buyer ? 'quoted' : buyer ? 'countered at' : 'replied with'}</span>
          <b className="num">{priceText(s.unit_price)}</b><span className="muted">· {s.lead_time_weeks} wk</span>
          {last && <Badge tone="info">latest</Badge>}</div>
        {who && <div className="muted" style={{ fontSize: 12.5 }}>Round {s.round}: {who}.</div>}
        {s.judge && s.judge.level !== 'APPROPRIATE' && <div className="neg-judge"><Badge tone={s.judge.level === 'CONCERN' ? 'warn' : 'bad'}>{s.judge.level.toLowerCase()}</Badge> {s.judge.notes.join(' ')}</div>}
      </div>
    </li>
  );
}

/** How the agents negotiated: every quote and counter in order, who chose each move, the buyer's own limits, and when a person is asked. */
export function Negotiation({ data }: { data?: Neg }) {
  if (!data || !data.threads.length) return null;
  const l = data.limits;
  return (
    <section className="card pad stack" aria-label="How the agents negotiated">
      <div><h2>How the agents negotiated</h2><p className="hint">{data.agents_decide}</p></div>
      {l && (
        <dl className="facts" aria-label="Your negotiation limits">
          {l.target_unit_price && <div><dt>Your target price</dt><dd>{priceText(l.target_unit_price)}</dd></div>}
          {l.highest_unit_price && <div><dt>Highest price your agent may accept</dt><dd>{priceText(l.highest_unit_price)}</dd></div>}
          {l.max_rounds != null && <div><dt>Most rounds</dt><dd>{l.max_rounds}</dd></div>}
          {l.min_step_pct != null && <div><dt>Smallest move per counter</dt><dd>{l.min_step_pct}%</dd></div>}
        </dl>
      )}
      <div className="neg-threads">
        {data.threads.map((t) => (
          <div key={t.supplier_id} className="neg-thread">
            <h3>{t.name}</h3>
            <ol className="plain-list neg-list">{t.steps.map((s, i) => <Step key={i} s={s} last={i === t.steps.length - 1} />)}</ol>
          </div>
        ))}
      </div>
      <details className="neg-when"><summary>When a person has to decide</summary>
        <ul className="plain-list">{data.when_a_person_decides.map((w) => <li key={w.kind}><b>{approvalTitle(w.kind)}</b><div className="muted" style={{ fontSize: 12.5 }}>{w.text}</div></li>)}</ul>
        <p className="muted" style={{ fontSize: 12.5 }}>Everything else is settled by the agents and the rules, and recorded.</p>
      </details>
    </section>
  );
}
