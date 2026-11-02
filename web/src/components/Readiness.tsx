import type { Candidate } from '../api/types';
import { qualText, reasonText } from '../lib/friendly';
import { Badge } from './ui';

const FIELD = (f: string) => f.replace(/_/g, ' ');
const isGap = (c: Candidate) => c.qualification === 'REVIEW' && c.reasons.length > 0 && c.reasons.every((r) => r.startsWith('MISSING_DATA'));

/** Discovery and qualification as one answer: who can be sourced from now, who is missing information (and what the agents did about it), who is out. */
export function Readiness({ list }: { list: Candidate[] }) {
  if (!list.length) return null;
  const ready = list.filter((c) => c.qualification === 'QUALIFIED');
  const missing = list.filter((c) => c.qualification === 'REVIEW');
  const out = list.filter((c) => c.qualification === 'REJECTED' || c.qualification === 'BLOCKED');
  const pending = list.filter((c) => !c.qualification);
  return (
    <section className="card pad stack" aria-label="Sourcing readiness">
      <div><h2>Who is ready for sourcing</h2>
        <p className="hint">Discovery and qualification in one view. The agents search, screen and ask for missing information themselves.</p></div>
      <div className="ready-grid">
        <div className="ready-col"><h3><Badge tone="ok">Ready</Badge> <span className="num">{ready.length}</span></h3>
          <ul className="plain-list">{ready.map((c) => (
            <li key={c.supplier_id}><b>{c.name}</b> <span className="muted">{c.country}</span>
              {c.reasons.some((r) => r.startsWith('DECLARED_BY_SUPPLIER')) && <div className="muted" style={{ fontSize: 12.5 }}>Cleared on figures the supplier declared itself ({c.reasons.filter((r) => r.startsWith('DECLARED')).map((r) => FIELD(r.split(':')[1] ?? '')).join(', ')}); not independently verified.</div>}
            </li>))}
            {!ready.length && <li className="muted">None yet</li>}</ul></div>
        <div className="ready-col"><h3><Badge tone="warn">Missing information</Badge> <span className="num">{missing.length}</span></h3>
          <ul className="plain-list">{missing.map((c) => (
            <li key={c.supplier_id}><b>{c.name}</b> <span className="muted">{c.country}</span>
              <div className="muted" style={{ fontSize: 12.5 }}>
                {c.info?.asked_for?.length ? <>Agent asked for {c.info.asked_for.map(FIELD).join(', ')}{c.info.answered ? ' and got an answer, but it did not clear the rules.' : '; no usable answer yet.'} </> : null}
                {c.reasons.map((r) => reasonText(r)).join('; ')}
                {isGap(c) && !c.info && ' The agents will ask this supplier for it.'}
              </div></li>))}
            {!missing.length && <li className="muted">None</li>}</ul></div>
        <div className="ready-col"><h3><Badge tone="bad">Not eligible</Badge> <span className="num">{out.length}</span></h3>
          <ul className="plain-list">{out.map((c) => (
            <li key={c.supplier_id}><b>{c.name}</b> <span className="muted">{qualText(c.qualification ?? '')}</span>
              <div className="muted" style={{ fontSize: 12.5 }}>{c.reasons.map((r) => reasonText(r)).join('; ')}</div></li>))}
            {!out.length && <li className="muted">None</li>}</ul></div>
      </div>
      {pending.length > 0 && <p className="muted">{pending.length} still being screened.</p>}
    </section>
  );
}
