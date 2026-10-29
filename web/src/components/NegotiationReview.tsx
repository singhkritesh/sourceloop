import type { TurnReview } from '../api/types';
import { Badge } from './ui';

const WHO: Record<string, string> = { buyer: 'The buyer', supplier: 'The supplier', you: 'You' };

/** Every negotiation turn is judged for appropriateness. Show the verdict in plain words; say nothing scary when everything was normal. */
export function NegotiationReview({ turns, names }: { turns?: TurnReview[]; names?: Record<string, string> }) {
  if (!turns || turns.length === 0) return null;
  const flagged = turns.filter((t) => t.level !== 'APPROPRIATE');
  return (
    <section className="card pad stack" aria-label="Negotiation review">
      <div><h2>Negotiation review</h2>
        <p className="hint">Each turn of the negotiation is checked for fair conduct: moving the right way, not stalling, not lowballing, not giving up early.</p></div>
      {flagged.length === 0 ? (
        <div className="row wrap"><Badge tone="ok">all appropriate</Badge><span className="muted">{turns.length} turn{turns.length > 1 ? 's' : ''} reviewed</span></div>
      ) : (
        <ol className="plain-list">
          {flagged.map((t, i) => (
            <li key={i}>
              <div className="row wrap"><Badge tone={t.level === 'INAPPROPRIATE' ? 'bad' : 'warn'}>{t.level === 'INAPPROPRIATE' ? 'not appropriate' : 'concern'}</Badge>
                <b>{WHO[t.actor] ?? t.actor}</b>{t.supplier_id && names?.[t.supplier_id] ? <span className="muted">· {names[t.supplier_id]}</span> : null}
                {t.round != null && <span className="muted">· round {t.round}</span>}</div>
              <div className="muted" style={{ fontSize: 12.5 }}>{t.notes.length ? t.notes.join('; ') : 'flagged for review'}</div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
