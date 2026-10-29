import { STAGES, type ProjectStatus } from '../api/types';
import { stageBlurb } from '../lib/friendly';

export function Stepper({ stage, status }: { stage: string; status: ProjectStatus }) {
  const idx = stage === 'DONE' ? STAGES.length : STAGES.indexOf(stage as (typeof STAGES)[number]);
  return (
    <ol className="stepper" aria-label="Stage progress" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
      {STAGES.map((s, i) => {
        const done = i < idx || (status === 'DONE' && stage === 'DONE');
        const cur = i === idx && stage !== 'DONE';
        return (
          <li key={s} className={`step ${done ? 'done' : ''} ${cur ? `current ${status === 'RUNNING' ? 'running' : ''}` : ''}`} aria-current={cur ? 'step' : undefined}>
            <span className="bullet">{done ? '✓' : i + 1}</span>
            <span className="step-text"><span>{s.charAt(0) + s.slice(1).toLowerCase()}</span><small className="step-sub">{stageBlurb(s)}</small></span>
            <span className="sr-only">{done ? ' (complete)' : cur ? ' (current)' : ''}</span>
          </li>
        );
      })}
    </ol>
  );
}
