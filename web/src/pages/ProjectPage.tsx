import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import { AgentActivity } from '../components/AgentActivity';
import { AgentPerformance } from '../components/AgentPerformance';
import { BuyerOverview } from '../components/BuyerOverview';
import { ProjectHeader } from '../components/ProjectHeader';
import { SupplierOverview } from '../components/SupplierOverview';
import { ErrorBox, SkeletonList } from '../components/ui';
import { isInspector, isSupplierScoped, useAuth } from '../state/auth';
import { Workspace } from './Workspace';

type Tab = 'overview' | 'workflow' | 'activity' | 'performance';

/** One project, seen from the viewer's own side. Business users get the business view only. Inspectors (SBN super admin / evaluator,
 *  an organization admin for its own side) also get the agent workflow and how each agent behaved. */
export function ProjectPage() {
  const { id = '' } = useParams();
  const me = useAuth((s) => s.me);
  const [tab, setTab] = useState<Tab>('overview');
  const proj = useQuery({ queryKey: ['project', id], queryFn: () => projects.get(id), refetchInterval: (q) => (q.state.data?.status === 'RUNNING' ? 3000 : 12000) });
  const live = proj.data?.status === 'RUNNING' || proj.data?.status === 'ESCALATED';
  const ov = useQuery({ queryKey: ['overview', id], queryFn: () => projects.overview(id), enabled: proj.isSuccess, refetchInterval: live ? 4000 : 20000 });

  if (proj.isLoading) return <div style={{ padding: 20 }}><SkeletonList rows={6} h={56} /></div>;
  if (proj.isError) return <div style={{ padding: 20 }}><ErrorBox error={proj.error} onRetry={() => void proj.refetch()} /><p style={{ marginTop: 12 }}><Link to="/projects">Back to projects</Link></p></div>;
  const p = proj.data;
  if (!p || !me) return null;
  const supplierView = isSupplierScoped(me);
  const inspect = isInspector(me.role);
  const decisions = ov.data?.perspective === 'buyer' ? ov.data.decisions : [];
  const tabs: [Tab, string][] = [['overview', 'Overview'], ['workflow', 'Agents & workflow'], ['activity', 'Agent activity'], ['performance', 'Agent performance']];

  return (
    <div className="projpage">
      <ProjectHeader project={p} me={me} decisions={decisions} supplierView={supplierView} inspect={inspect} />
      {inspect && (
        <div className="tabs proj-tabs" role="tablist" aria-label="Project views">
          {tabs.map(([t, label]) => <button key={t} role="tab" aria-selected={tab === t} className="tab" onClick={() => setTab(t)}>{label}</button>)}
        </div>
      )}
      <div className="proj-body">
        {(!inspect || tab === 'overview') && (
          ov.isLoading ? <SkeletonList rows={4} h={90} /> : ov.isError ? <ErrorBox error={ov.error} onRetry={() => void ov.refetch()} /> :
            ov.data?.perspective === 'supplier' ? <SupplierOverview project={p} data={ov.data} /> : ov.data ? <BuyerOverview project={p} data={ov.data} /> : null
        )}
        {inspect && tab === 'workflow' && <Workspace project={p} supplierView={supplierView} />}
        {inspect && tab === 'performance' && <AgentPerformance projectId={id} running={p.status === 'RUNNING'} />}
        {inspect && tab === 'activity' && <AgentActivity projectId={id} running={p.status === 'RUNNING'} />}
      </div>
    </div>
  );
}
