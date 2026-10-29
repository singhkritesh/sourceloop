import { useState } from 'react';
import { Icon } from './Icon';
import type { NextStep } from '../lib/friendly';

/** The first thing to read on a project: what is happening and what happens next. */
export function NextStepCard({ step, busy, onApprovals, onStart, onResume }: {
  step: NextStep; busy?: boolean; onApprovals: () => void; onStart: () => void; onResume: () => void;
}) {
  return (
    <section className={`next ${step.tone}`} aria-label="What happens next">
      <div className="next-icon" aria-hidden="true"><Icon name={step.tone === 'ok' ? 'check' : step.tone === 'bad' || step.tone === 'warn' ? 'alert' : 'info'} /></div>
      <div className="grow">
        <div className="next-label">{step.action === 'approvals' ? 'Needs a decision' : 'Where things stand'}</div>
        <div className="next-head">{step.headline}</div>
        <p className="next-detail">{step.detail}</p>
      </div>
      {step.action === 'approvals' && <button className="btn primary" onClick={onApprovals}>Review and decide</button>}
      {step.action === 'start' && <button className="btn primary" disabled={busy} onClick={onStart}><Icon name="play" />Start the run</button>}
      {step.action === 'resume' && <button className="btn" disabled={busy} onClick={onResume}><Icon name="refresh" />Resume</button>}
    </section>
  );
}

const KEY = 'sourceloop.welcome.dismissed';
const read = (): boolean => { try { return localStorage.getItem(KEY) === '1'; } catch { return false; } };
const write = () => { try { localStorage.setItem(KEY, '1'); } catch { /* storage unavailable: just hide for this visit */ } };

/** First-visit orientation. Dismissible; remembered per browser. */
export function Welcome({ role }: { role?: string }) {
  const [hidden, setHidden] = useState(read);
  if (hidden) return null;
  const buyer = role === 'BUYER';
  const steps: [string, string][] = buyer
    ? [
      ['Describe the need', 'Start a project and say what you want to buy.'],
      ['Agents do the legwork', 'Your agent finds and screens suppliers, requests quotes and negotiates for you, within the limits you set.'],
      ['You make the big calls', 'The run pauses for a person at compliance flags, technical pass / fail, the award, contract clauses, bank details and payment release.'],
      ['Everything is on the record', 'Every step is recorded and every decision is attributed to the person who made it.'],
    ]
    : role === 'SUPPLIER'
      ? [
        ['You are invited to quote', 'Buyers invite you to sourcing requests. They appear here under neutral request numbers.'],
        ['Your agent quotes for you', 'It stays within the prices and capacity you configured. The buyer never sees your limits.'],
        ['You see your own thread', 'Your quotes, the buyer’s replies and the final result. Nothing about other suppliers.'],
      ]
      : role === 'ADMIN'
        ? [
          ['Manage your people', 'Under Organization: add people, set their roles, disable access, reset passwords.'],
          ['Tune your own agents', 'Under Organization › Agent settings: timeouts, tool-call limits, model and wording. Each save is a version; running projects keep theirs.'],
          ['See how your agents behave', 'Open any project and use the Agents & workflow and Agent activity tabs.'],
          ['Safety rules stay with SBN', 'Limits, checkpoints, locked clauses and the audit trail are not configurable here.'],
        ]
        : role === 'SUPER_ADMIN' || role === 'EVALUATOR'
          ? [
            ['Every organization', 'Under Organizations: open any buyer or supplier organization, manage its people and its own-side settings.'],
            ['Every project, every agent', 'Open any project and use the Agents & workflow and Agent activity tabs to see exactly how the agents behaved.'],
            ['The platform configuration', role === 'SUPER_ADMIN' ? 'Studio holds the versioned configuration all organizations build on; Settings holds run-time limits.' : 'Studio and Settings are visible read-only.'],
          ]
          : [
            ['Your inbox is Approvals', 'When an agent reaches a decision that belongs to your role, it appears there.'],
            ['Each item explains itself', 'You see what you are deciding, why it was flagged, and what happens if you approve or reject.'],
            ['Agents recommend, you decide', 'Nothing high-stakes happens without a person.'],
          ];
  return (
    <section className="welcome" aria-label="How SourceLoop works">
      <div className="row">
        <h2 className="grow">{buyer ? 'How SourceLoop works' : 'Welcome to SourceLoop'}</h2>
        <button className="btn ghost sm" onClick={() => { write(); setHidden(true); }}>Got it, hide this</button>
      </div>
      <ol className="welcome-steps">
        {steps.map(([t, d], i) => <li key={t}><span className="welcome-n" aria-hidden="true">{i + 1}</span><div><b>{t}</b><p>{d}</p></div></li>)}
      </ol>
    </section>
  );
}
