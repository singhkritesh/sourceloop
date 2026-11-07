import { useQuery } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import { Badge, Drawer, ErrorBox, fmtTime, SkeletonList } from './ui';

/** The audit log in plain sentences, in a panel over whatever page the person is on. Each side reads its own view: a buyer the whole project, a supplier its own thread. */
export function RecordDrawer({ projectId, title, onClose }: { projectId: string; title: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ['record', projectId], queryFn: () => projects.record(projectId), refetchInterval: 8000 });
  const chain = q.data?.chain;
  return (
    <Drawer onClose={onClose} title={<>Audit log {chain && <Badge tone={chain.valid ? 'ok' : 'bad'}>{chain.valid ? 'chain verified' : 'chain broken'}</Badge>}</>}
      subtitle={<>{title}{chain ? ` \u00b7 ${chain.events} recorded steps` : ' \u00b7 your side of the record'}</>}>
      {q.isLoading ? <SkeletonList rows={6} h={36} /> : q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> : !q.data?.items.length ? <span className="muted">Nothing has been recorded yet.</span> : (
        <ol className="stack" style={{ listStyle: 'none', margin: 0, padding: 0, gap: 8 }}>
          {q.data.items.map((i) => (
            <li key={i.seq} className="row" style={{ alignItems: 'flex-start', gap: 10 }}>
              <span className="muted num" style={{ minWidth: 28, textAlign: 'right' }}>{i.seq}</span>
              <div className="grow"><div>{i.what}</div><div className="muted">{i.who} · {fmtTime(i.ts)}</div></div>
            </li>
          ))}
        </ol>
      )}
      <p className="hint" style={{ marginTop: 12 }}>Every step is written to a tamper-evident record before it happens. This view shows it in words; prices and limits are in the negotiation summary, not here.</p>
    </Drawer>
  );
}
