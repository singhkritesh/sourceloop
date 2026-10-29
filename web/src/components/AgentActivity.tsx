import { useQuery } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import { toolText } from '../lib/friendly';
import { Badge, Empty, ErrorBox, SkeletonList } from './ui';

const who = (agent: string, sid: string | null) => (agent === 'buyer' ? 'Buyer agent' : sid ? `Supplier agent ${sid}` : 'Supplier agent');

/** How each agent behaved on this project: one row per tool call. Admin / evaluator only; an organization admin sees only its own side's agents. */
export function AgentActivity({ projectId, running }: { projectId: string; running: boolean }) {
  const q = useQuery({ queryKey: ['activity', projectId], queryFn: () => projects.activity(projectId), refetchInterval: running ? 4000 : false });
  if (q.isLoading) return <SkeletonList rows={4} h={48} />;
  if (q.isError) return <ErrorBox error={q.error} onRetry={() => void q.refetch()} />;
  const { items, summary } = q.data ?? { items: [], summary: [] };
  if (!items.length) return <div className="card"><Empty title="No agent activity yet">Each tool an agent calls is listed here with its confidence and speed.</Empty></div>;
  return (
    <div className="stack">
      <div className="summary-grid">
        {summary.map((s) => (
          <div className="card pad stat" key={`${s.agent}${s.tool}`}>
            <div className="muted" style={{ fontSize: 12 }}>{s.agent === 'buyer' ? 'Buyer agent' : 'Supplier agent'}</div>
            <b>{toolText(s.tool)}</b>
            <div className="row wrap" style={{ marginTop: 6 }}><span className="num">{s.calls} call{s.calls > 1 ? 's' : ''}</span>
              {s.failed > 0 && <Badge tone="bad">{s.failed} failed</Badge>}</div>
            <div className="muted num" style={{ fontSize: 12 }}>confidence {Math.round(s.avg_confidence * 100)}% · {s.avg_latency_ms} ms avg{s.tokens ? ` · ${s.tokens} tokens` : ''}</div>
          </div>
        ))}
      </div>
      <section className="card" aria-label="Agent calls">
        <div className="card-head"><h2>Every call</h2><span className="hint">{items.length} total</span></div>
        <table className="table num">
          <thead><tr><th>#</th><th>Agent</th><th>Tool</th><th>Result</th><th>Confidence</th><th>Speed</th><th>Model</th></tr></thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.ord}>
                <td>{i.ord}</td><td>{who(i.agent, i.supplier_id)}</td><td>{toolText(i.tool)}</td>
                <td>{i.ok === false ? <Badge tone="bad">{i.error ?? 'failed'}</Badge> : <Badge tone="ok">ok</Badge>}</td>
                <td><span className="chip"><span className="meter"><i style={{ width: `${Math.round(i.confidence * 100)}%` }} /></span>{Math.round(i.confidence * 100)}%</span></td>
                <td>{i.latency_ms} ms</td><td>{i.model_id ? <span className="mono">{i.model_id}</span> : <span className="muted">rules only</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
