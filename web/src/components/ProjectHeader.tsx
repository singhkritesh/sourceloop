import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import { errorMessage } from '../api/http';
import type { Decision, Me, Project } from '../api/types';
import { isLostOnRestart, isRuntimeUnavailable, LOST_ON_RESTART_TEXT } from '../lib/errors';
import { nextStep } from '../lib/friendly';
import { can } from '../state/auth';
import { toast } from '../state/ui';
import { NextStepCard } from './Guide';
import { Icon } from './Icon';
import { Stepper } from './Stepper';
import { Badge, Dialog, StatusBadge } from './ui';

/** Title, status, run controls, stage progress and "what happens next". Every role sees this; what is under it depends on the perspective. */
export function ProjectHeader({ project, me, decisions, supplierView, inspect }: {
  project: Project; me: Me; decisions: Decision[]; supplierView: boolean; inspect: boolean;
}) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const role = me.role;
  const [runtimeLost, setRuntimeLost] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const [reopening, setReopening] = useState(false);
  const [target, setTarget] = useState('');
  const [highest, setHighest] = useState('');
  const lost = runtimeLost || isLostOnRestart(project);
  const act = useMutation({
    mutationFn: (kind: 'start' | 'resume' | 'stop') => projects[kind](project.id),
    onSuccess: (_d, kind) => { toast.ok(kind === 'stop' ? 'Run stopped' : 'Run started'); void qc.invalidateQueries({ queryKey: ['project', project.id] }); void qc.invalidateQueries({ queryKey: ['overview', project.id] }); },
    onError: (e) => { if (isRuntimeUnavailable(e)) setRuntimeLost(true); toast.error(errorMessage(e)); },
  });
  const again = useMutation({
    mutationFn: () => projects.rerun(project.id),
    onSuccess: (r) => { toast.ok('Started again from the same need'); nav(`/projects/${r.id}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const reopen = useMutation({
    mutationFn: () => projects.reopen(project.id, { target_price: target.trim(), max_price: highest.trim() }),
    onSuccess: (r) => { setReopening(false); toast.ok('Negotiation reopened with your new limits'); void qc.invalidateQueries({ queryKey: ['projects'] }); nav(`/projects/${r.id}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const money = /^\d+(\.\d{1,4})?$/;
  const limitsOk = money.test(target.trim()) && money.test(highest.trim()) && Number(target) > 0 && Number(target) <= Number(highest);
  const canReopen = role === 'BUYER' && !supplierView && !lost && !project.reopened_to && !['DRAFT', 'RUNNING'].includes(project.status)
    && project.outcome !== 'DEAL' && !['ONBOARDING', 'DONE'].includes(project.stage);
  // who is the run waiting on? (an overview lists every checkpoint of the project)
  const waitingOn = useMemo(() => (project.status !== 'ESCALATED' ? [] : decisions.filter((d) => d.status === 'PENDING').map((d) => ({ kind: d.kind, role: d.role }))), [project.status, decisions]);
  const canRun = !supplierView;
  return (
    <div className="ws-head">
      <div className="row wrap">
        <button className="btn ghost sm" onClick={() => nav('/projects')} aria-label="Back to projects"><Icon name="chevron" style={{ transform: 'rotate(180deg)' }} /></button>
        <h1 className="trunc" style={{ maxWidth: '40vw' }}>{project.title}</h1>
        <StatusBadge status={project.status} reason={project.reason} outcome={project.outcome} />
        {project.outcome && !['DEAL', 'NO_DEAL'].includes(project.outcome) && !supplierView && <Badge tone="warn">{project.outcome.replace(/_/g, ' ').toLowerCase()}</Badge>}
        {inspect && project.llm_mode && <span title={project.llm_mode === 'replay' ? 'Demo mode: AI steps replay recorded answers, so runs are repeatable and need no model.' : 'Live AI model'}><Badge tone="info">{project.llm_mode === 'replay' ? 'demo mode' : project.llm_mode}</Badge></span>}
        <span className="spacer" />
        {canRun && can.controlRun(role) && project.status === 'DRAFT' && <button className="btn primary" disabled={act.isPending} onClick={() => act.mutate('start')}><Icon name="play" />Start run</button>}
        {canRun && can.resume(role) && !lost && !(project.status === 'ESCALATED' && waitingOn.length > 0) && ['ESCALATED', 'FAILED_RECOVERABLE', 'STALLED', 'BUDGET_EXCEEDED'].includes(project.status) && <button className="btn" disabled={act.isPending} onClick={() => act.mutate('resume')}><Icon name="refresh" />Resume</button>}
        {canReopen && <button className="btn" onClick={() => setReopening(true)}><Icon name="refresh" />Reopen with new limits</button>}
        {canRun && can.stop(role) && project.status === 'RUNNING' && <button className="btn danger" onClick={() => setConfirmStop(true)}><Icon name="stop" />Stop</button>}
      </div>
      {!supplierView && (project.reopened_from || project.reopened_to) && (
        <div className="callout info"><Icon name="info" /><div>
          {project.reopened_from && <>This negotiation was reopened from <a href={`/projects/${project.reopened_from}`} onClick={(e) => { e.preventDefault(); nav(`/projects/${project.reopened_from}`); }}>the earlier one</a> with new limits.</>}
          {project.reopened_to && <>This negotiation was stopped and reopened as <a href={`/projects/${project.reopened_to}`} onClick={(e) => { e.preventDefault(); nav(`/projects/${project.reopened_to}`); }}>a new project</a>. Nothing in it was changed.</>}
        </div></div>
      )}
      {!supplierView && <Stepper stage={project.stage} status={project.status} />}
      {lost && (
        <div className="callout bad" role="alert"><Icon name="alert" />
          <div><b>Lost on restart.</b> {LOST_ON_RESTART_TEXT} Resume and approval actions are disabled.
            {role === 'BUYER' && !supplierView && <> <button className="btn primary sm" disabled={again.isPending} onClick={() => again.mutate()}><Icon name="refresh" />Run again from the same need</button></>}</div></div>
      )}
      {!lost && !supplierView && (
        <NextStepCard busy={act.isPending} onApprovals={() => nav('/approvals')} onStart={() => act.mutate('start')} onResume={() => act.mutate('resume')}
          step={nextStep({ status: project.status, stage: project.stage, outcome: project.outcome, lost, role, reason: project.reason, pendingKinds: waitingOn })} />
      )}
      {project.reason && !lost && supplierView && <div className="callout info"><Icon name="info" /><div>{project.reason}</div></div>}
      {reopening && (
        <Dialog title="Reopen with new limits" onClose={() => setReopening(false)} footer={<>
          <button className="btn" onClick={() => setReopening(false)}>Cancel</button>
          <button className="btn primary" disabled={!limitsOk || reopen.isPending} onClick={() => reopen.mutate()}>{reopen.isPending ? 'Reopening…' : 'Reopen and start'}</button></>}>
          <p>This stops the current negotiation and starts a new one from the same need. Nothing is signed, and the current record stays as it is. Suppliers cannot confirm terms of the old one any more.</p>
          <div className="field"><label htmlFor="ro-target">Target price per unit</label>
            <input id="ro-target" className="input" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} autoFocus /></div>
          <div className="field"><label htmlFor="ro-max">Highest price you will pay per unit</label>
            <input id="ro-max" className="input" inputMode="decimal" value={highest} onChange={(e) => setHighest(e.target.value)} />
            <span className="hint">Only the agents and the platform's rules see this limit. Suppliers never do.</span></div>
          {!limitsOk && (target || highest) && <span className="err-text" role="alert">Enter two prices, the target not above the highest.</span>}
        </Dialog>
      )}
      {confirmStop && (
        <Dialog title="Stop this run?" onClose={() => setConfirmStop(false)} footer={<>
          <button className="btn" onClick={() => setConfirmStop(false)}>Keep running</button>
          <button className="btn danger" onClick={() => { setConfirmStop(false); act.mutate('stop'); }}>Stop run</button></>}>
          <p>The project ends as <b>stopped by user</b>. It cannot be resumed.</p>
        </Dialog>
      )}
    </div>
  );
}
