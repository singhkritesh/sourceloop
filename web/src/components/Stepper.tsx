import { type ProjectStatus } from '../api/types';

// Discovery and qualification are one step for people: find suppliers and see who is ready.
const STEPS: { key: string; name: string; sub: string; stages: string[] }[] = [
  { key: 'NEED', name: 'Need', sub: 'What you want to buy', stages: ['NEED'] },
  { key: 'FIND', name: 'Find and qualify', sub: 'Search, screen, ask for what is missing', stages: ['DISCOVERY', 'QUALIFICATION'] },
  { key: 'SOURCING', name: 'Sourcing', sub: 'Quotes and negotiation', stages: ['SOURCING'] },
  { key: 'CONTRACTING', name: 'Contract', sub: 'Terms and signature', stages: ['CONTRACTING'] },
  { key: 'ONBOARDING', name: 'Onboarding', sub: 'Bank details and payment release', stages: ['ONBOARDING'] },
];

export function Stepper({ stage, status }: { stage: string; status: ProjectStatus }) {
  const idx = stage === 'DONE' ? STEPS.length : STEPS.findIndex((x) => x.stages.includes(stage));
  return (
    <ol className="stepper" aria-label="Stage progress" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {STEPS.map((s, i) => {
        const done = i < idx || (status === 'DONE' && stage === 'DONE');
        const cur = i === idx && stage !== 'DONE';
        return (
          <li key={s.key} className={`step ${done ? 'done' : ''} ${cur ? `current ${status === 'RUNNING' ? 'running' : ''}` : ''}`} aria-current={cur ? 'step' : undefined}>
            <span className="bullet">{done ? '✓' : i + 1}</span>
            <span className="step-text"><span>{s.name}</span><small className="step-sub">{s.sub}</small></span>
            <span className="sr-only">{done ? ' (complete)' : cur ? ' (current)' : ''}</span>
          </li>
        );
      })}
    </ol>
  );
}
