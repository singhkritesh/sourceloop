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
  const lost = runtimeLost || isLostOnRestart(project);
  const act = useMutation({
    mutationFn: (kind: 'start' | 'resume' | 'stop') => projects[kind](project.id),
    onSuccess: (_d, kind) => { toast.ok(kind === 'stop' ? 'Run stopped' : 'Run started'); void qc.invalidateQueries({ queryKey: ['project', project.id] }); void qc.invalidateQueries({ queryKey: ['overview', project.id] }); },
    onError: (e) => { if (isRuntimeUnavailable(e)) setRuntimeLost(true); toast.error(errorMessage(e)); },
  });
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
        {canRun && can.stop(role) && project.status === 'RUNNING' && <button className="btn danger" onClick={() => setConfirmStop(true)}><Icon name="stop" />Stop</button>}
      </div>
      {!supplierView && <Stepper stage={project.stage} status={project.status} />}
      {lost && (
        <div className="callout bad" role="alert"><Icon name="alert" />
          <div><b>Lost on restart.</b> {LOST_ON_RESTART_TEXT} Resume and approval actions are disabled.</div></div>
      )}
      {!lost && !supplierView && (
        <NextStepCard busy={act.isPending} onApprovals={() => nav('/approvals')} onStart={() => act.mutate('start')} onResume={() => act.mutate('resume')}
          step={nextStep({ status: project.status, stage: project.stage, outcome: project.outcome, lost, role, reason: project.reason, pendingKinds: waitingOn })} />
      )}
      {project.reason && !lost && supplierView && <div className="callout info"><Icon name="info" /><div>{project.reason}</div></div>}
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
