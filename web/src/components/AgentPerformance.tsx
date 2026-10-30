import { useQuery } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import type { AgentPerf } from '../api/types';
import { Badge, Empty, ErrorBox, SkeletonList } from './ui';

const name = (a: string) => (a === 'buyer' ? 'Buyer agent' : a === 'award' ? 'Award advisor' : a.startsWith('supplier:') ? `Supplier agent ${a.slice(9)}` : a);
const pct = (v: number | null | undefined) => (v == null ? '-' : `${Math.round(v * 100)}%`);

function Card({ a }: { a: AgentPerf }) {
  const fb = Object.entries(a.fallbacks);
  const j = a.judge;
  return (
    <div className="card pad stat">
      <div className="muted" style={{ fontSize: 12 }}>{a.agent.startsWith('supplier') ? 'Supplier side' : 'Buyer side'}</div>
      <b>{name(a.agent)}</b>
      {a.decisions > 0 ? (
        <>
          <div className="row wrap" style={{ marginTop: 6 }}>
            <span className="num">{a.decisions} decision{a.decisions > 1 ? 's' : ''}</span>
            <Badge tone="accent">{a.model_chosen} by the model</Badge>
            {a.rule_used > 0 && <Badge tone="warn">{a.rule_used} by the rule fallback</Badge>}
          </div>
          <div className="muted num" style={{ fontSize: 12 }}>
            agrees with the rule {a.agreement_with_rule_pct ?? '-'}% · sure {pct(a.avg_confidence)} · {a.avg_ms ?? '-'} ms each{a.tokens ? ` · ${a.tokens} tokens` : ''}
          </div>
          {fb.length > 0 && <div className="muted" style={{ fontSize: 12 }}>fell back: {fb.map(([k, v]) => `${v} ${k.replace(/_/g, ' ').toLowerCase()}`).join(', ')}</div>}
        </>
      ) : <div className="muted" style={{ marginTop: 6 }}>Moves were rule-based (no model decisions).</div>}
      {(j.APPROPRIATE || j.CONCERN || j.INAPPROPRIATE) ? (
        <div className="row wrap" style={{ marginTop: 6 }}>
          <span className="muted" style={{ fontSize: 12 }}>conduct judge:</span>
          {j.APPROPRIATE ? <Badge tone="ok">{j.APPROPRIATE} appropriate</Badge> : null}
          {j.CONCERN ? <Badge tone="warn">{j.CONCERN} concern</Badge> : null}
          {j.INAPPROPRIATE ? <Badge tone="bad">{j.INAPPROPRIATE} inappropriate</Badge> : null}
        </div>
      ) : null}
    </div>
  );
}

/** How well the agents did their jobs: the model's own choices against the code's rule, how sure it was, and how the conduct judge rated each side. */
export function AgentPerformance({ projectId, running }: { projectId: string; running: boolean }) {
  const q = useQuery({ queryKey: ['agent-performance', projectId], queryFn: () => projects.agentPerformance(projectId), refetchInterval: running ? 4000 : false });
  if (q.isLoading) return <SkeletonList rows={3} h={70} />;
  if (q.isError) return <ErrorBox error={q.error} onRetry={() => void q.refetch()} />;
  const d = q.data;
  if (!d || !d.agents.length) return <div className="card"><Empty title="No agent decisions yet">When the agents negotiate, each choice they make is measured here.</Empty></div>;
  return (
    <div className="stack">
      {!d.live_model_decisions && (
        <div className="callout info" role="note"><div>No model made a choice on this project, so the moves were rule-based. Run a live project to see the model decide.</div></div>
      )}
      <div className="summary-grid">{d.agents.map((a) => <Card key={a.agent} a={a} />)}</div>
      {d.decisions.length > 0 && (
        <section className="card" aria-label="Every decision">
          <div className="card-head"><h2>Every decision</h2><span className="hint">{d.decisions.length} total. The rule's own move is what the code would have done.</span></div>
          <table className="table num">
            <thead><tr><th>Agent</th><th>Round</th><th>Rule would</th><th>Chosen</th><th>By</th><th>Sure</th><th>Speed</th></tr></thead>
            <tbody>
              {d.decisions.map((x) => (
                <tr key={x.seq}>
                  <td>{name(x.agent)}</td><td>{x.round || '-'}</td><td className="mono">{x.rule}</td>
                  <td className="mono">{x.chosen}{x.source === 'model' && !x.agrees_with_rule ? ' *' : ''}</td>
                  <td>{x.source === 'model' ? <Badge tone="accent">model</Badge> : <Badge tone="warn">{x.fallback ? (x.fallback.split(':')[0] ?? '').replace(/_/g, ' ').toLowerCase() : 'rule'}</Badge>}</td>
                  <td>{pct(x.confidence)}</td><td>{x.ms ?? '-'} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint" style={{ padding: '0 12px 10px' }}>* the model chose a different move from the rule. Every option was inside the agent's own limits, and the conduct judge still reviewed the result.</p>
        </section>
      )}
    </div>
  );
}
