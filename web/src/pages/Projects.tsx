import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { projects } from '../api/endpoints';
import { errorMessage } from '../api/http';
import { SCENARIOS, type Scenario } from '../api/types';
import { Icon } from '../components/Icon';
import { Welcome } from '../components/Guide';
import { DemoBanner } from '../components/DemoBanner';
import { Dialog, Empty, ErrorBox, fmtTime, SkeletonList, StatusBadge } from '../components/ui';
import { stageName } from '../lib/friendly';
import { can, useAuth } from '../state/auth';
import { toast } from '../state/ui';

export const SCENARIO_INFO: Record<Scenario, { title: string; desc: string }> = {
  happy: { title: 'Happy path', desc: 'Everything goes well and ends in a signed contract.' },
  no_deal: { title: 'No deal', desc: 'Buyer and supplier limits never overlap, so no deal is possible.' },
  below_floor: { title: 'Below floor', desc: 'The buyer pushes below the supplier\u2019s limit and is blocked.' },
  locked_clause: { title: 'Locked clause', desc: 'A protected clause is touched and goes to Legal.' },
  poisoned_news: { title: 'Poisoned news', desc: 'A news article tries to trick the agent; the attack is contained.' },
  inflated_claims: { title: 'Inflated claims', desc: 'A supplier claims certificates the registry cannot confirm.' },
};

const SAMPLE_NEED = '500 PCB assemblies, IATF16949 and RoHS certified, lead time under 8 weeks';

export function Projects() {
  const role = useAuth((s) => s.me?.role);
  const q = useQuery({ queryKey: ['projects'], queryFn: projects.list, refetchInterval: 8000 });
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();
  const sample = useMutation({
    mutationFn: async () => {
      const p = await projects.create({ title: 'Sample: PCB assemblies', need_text: SAMPLE_NEED, scenario: 'happy', need_index: 0, seed: 42 });
      await projects.start(p.id);
      return p;
    },
    onSuccess: (p) => { void qc.invalidateQueries({ queryKey: ['projects'] }); toast.ok('Sample project started. It will pause when it needs your decision.'); nav(`/projects/${p.id}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <>
      <div className="page-head">
        <div className="grow"><h1>Sourcing projects</h1><p>{role === 'SUPPLIER' ? 'Projects you have been invited to.' : 'Each project takes one need from finding suppliers through to a signed contract.'}</p></div>
        {can.createProject(role) && <button className="btn" disabled={sample.isPending} onClick={() => sample.mutate()} title="Creates a ready-made project and runs it"><Icon name="play" />{sample.isPending ? 'Starting…' : 'Run a sample project'}</button>}
        {can.createProject(role) && <button className="btn primary" onClick={() => setOpen(true)}><Icon name="plus" />New sourcing project</button>}
      </div>
      <DemoBanner compact />
      <Welcome role={role} />
      {q.isLoading ? <SkeletonList rows={5} /> : q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> : (
        <div className="card">
          {q.data?.items.length ? (
            <table className="table">
              <thead><tr><th>Project</th><th>Where it is</th><th>Status</th><th className="num">Updated</th></tr></thead>
              <tbody>
                {q.data.items.map((p) => (
                  <tr key={p.id} className="click" tabIndex={0} onClick={() => nav(`/projects/${p.id}`)} onKeyDown={(e) => { if (e.key === 'Enter') nav(`/projects/${p.id}`); }}>
                    <td><b>{p.title}</b>{p.need_text && <div className="muted trunc" style={{ maxWidth: 420 }}>{p.need_text}</div>}</td>
                    <td>{stageName(p.stage)}</td>
                    <td><StatusBadge status={p.status} reason={p.reason} outcome={p.outcome} /></td>
                    <td className="num muted">{fmtTime(p.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty title="No projects yet">{can.createProject(role) ? 'Describe what you need to buy and the agents will find, screen and negotiate with suppliers. Click \u201cRun a sample project\u201d to see a full run in a minute, or \u201cNew sourcing project\u201d to start your own.' : 'Nothing has been shared with you yet. When a buyer invites your company to quote, it will appear here.'}</Empty>
          )}
        </div>
      )}
      {open && <NewProject onClose={() => setOpen(false)} />}
    </>
  );
}

function NewProject({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [title, setTitle] = useState('');
  const [need, setNeed] = useState('');
  const [scenario, setScenario] = useState<Scenario>('happy');
  const [needIndex, setNeedIndex] = useState(0);
  const [seed, setSeed] = useState(42);
  const [autoStart, setAutoStart] = useState(true);
  const m = useMutation({
    mutationFn: async () => {
      const p = await projects.create({ title: title.trim(), need_text: need, scenario, need_index: needIndex, seed });
      if (autoStart) await projects.start(p.id);
      return p;
    },
    onSuccess: (p) => { void qc.invalidateQueries({ queryKey: ['projects'] }); toast.ok('Project created'); nav(`/projects/${p.id}`); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (title.trim()) m.mutate(); };
  return (
    <Dialog title="New sourcing project" onClose={onClose} footer={<>
      <button className="btn" type="button" onClick={onClose}>Cancel</button>
      <button className="btn primary" form="newproj" disabled={!title.trim() || m.isPending}>{m.isPending ? 'Creating…' : autoStart ? 'Create and start' : 'Create'}</button>
    </>}>
      <form id="newproj" className="stack" onSubmit={submit}>
        <div className="field"><label htmlFor="np-t">Project name</label><input id="np-t" className="input" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} required placeholder="e.g. PCB assemblies Q4" /></div>
        <div className="field"><label htmlFor="np-n">What do you need to buy?</label>
          <textarea id="np-n" className="textarea" style={{ fontFamily: 'inherit' }} maxLength={4000} value={need} onChange={(e) => setNeed(e.target.value)} placeholder="500 PCB assemblies, IATF16949, lead time under 8 weeks…" />
          <div className="example-chips" aria-label="Examples">
            {['500 PCB assemblies, IATF16949, lead time under 8 weeks', 'Aluminium die-cast housings, 2,000 units a quarter', 'Custom wire harnesses for industrial controllers'].map((x) => <button type="button" key={x} onClick={() => setNeed(x)}>{x}</button>)}
          </div>
          <span className="hint">{need.length}/4000 · synthetic data only</span></div>
        <DemoBanner compact />
        <details className="adv">
          <summary className="hint" style={{ cursor: 'pointer' }}>Advanced: choose a demo scenario, sample need and random seed</summary>
        <div className="field" role="radiogroup" aria-label="Scenario"><span className="label">What kind of run do you want to see?</span>
          <div className="scenario-grid">
            {SCENARIOS.map((s) => (
              <button type="button" key={s} role="radio" aria-checked={scenario === s} className="scenario" onClick={() => setScenario(s)}>
                <b>{SCENARIO_INFO[s].title}</b><span>{SCENARIO_INFO[s].desc}</span>
              </button>
            ))}
          </div></div>

        <div className="row wrap" style={{ marginTop: 8 }}>
          <div className="field grow"><label htmlFor="np-i">Need index (0-11)</label><input id="np-i" className="input" type="number" min={0} max={11} value={needIndex} onChange={(e) => setNeedIndex(Math.min(11, Math.max(0, Number(e.target.value) || 0)))} /></div>
          <div className="field grow"><label htmlFor="np-s">Seed</label><input id="np-s" className="input" type="number" min={0} max={10000} value={seed} onChange={(e) => setSeed(Math.min(10000, Math.max(0, Number(e.target.value) || 0)))} /></div>
        </div>
        </details>
        <label className="row"><input type="checkbox" checked={autoStart} onChange={(e) => setAutoStart(e.target.checked)} /> Start right away <span className="hint">(the run still pauses whenever a person must decide)</span></label>
      </form>
    </Dialog>
  );
}
