import { DemoBanner } from '../components/DemoBanner';

const TERMS: [string, string][] = [
  ['Agent', 'A program that does a job for one side. The buyer agent finds and screens suppliers and negotiates. A supplier agent answers for its company.'],
  ['Orchestrator', 'The neutral referee in the middle. It follows fixed rules, passes messages between the two sides and records everything. It is ordinary code, not an AI.'],
  ['Limits (mandate)', 'The most a buyer will pay or the least a supplier will accept. They are enforced by code and each side never sees the other\u2019s limits.'],
  ['Qualification', 'Checking a supplier is allowed and able to deliver: valid certificates, not on a sanctions list, acceptable risk.'],
  ['Request for quote (RFQ)', 'The invitation to suppliers to offer a price and lead time for what you need.'],
  ['Negotiation', 'A few rounds of offers and counter-offers within the limits. A conduct check reviews each turn for things like going backwards or ignoring the other side.'],
  ['Needs a decision (escalation)', 'The run stopped because a person must decide. That happens only for a flagged supplier, the award, a changed protected contract clause, and the final bank-details and payment release. Everything else the agents and rules handle.'],
  ['Audit record', 'A tamper-evident log of every step. Each entry is chained to the previous one, so any change is detectable. \u201cVerified\u201d means the chain is intact.'],
  ['Live and replay', 'Live runs ask a local AI model, and what you type drives the run. Replay (used for tests) plays back recorded answers so a run is repeatable.'],
    ['Studio', 'The configuration editor for designers and administrators: models, wording, rules and clauses. Safety rules cannot be changed there.'],
];

export function Help() {
  return (
    <div className="stack">
      <div className="page-head"><div className="grow"><h1>Help</h1><p>What SourceLoop is, how to try it, and what the words mean.</p></div></div>
      <DemoBanner />
      <section className="card stack" style={{ padding: 16 }}>
        <h2>What is this?</h2>
        <p>SourceLoop shows how software agents could buy parts for a company. You say what you need; an agent finds suppliers, checks them, asks for quotes and negotiates. A person makes the big calls. Everything uses made-up data.</p>
        <h2>Try it in three steps</h2>
        <ol>
          <li>On <b>Projects</b>, click <b>New sourcing project</b> and describe what you need, for example “500 PCB assemblies, IATF16949 and RoHS, delivered within 8 weeks, no suppliers in Turkey”.</li>
          <li>Watch the progress bar at the top. When it says <b>Needs a decision</b>, click <b>Review and decide</b> and approve. Most steps need no one: you are asked only about a flagged supplier, the award, a changed protected clause and, at the end, bank details with payment release.</li>
          <li>When it says <b>Deal reached</b>, scroll down to see the suppliers, the price chart and the record of every step.</li>
        </ol>
        <h2>If something looks wrong</h2>
        <ul>
          <li>Nothing happens after starting: the run is probably waiting for you under <b>Approvals</b>.</li>
          <li>The result ignores what you typed: you are in demo mode (see the blue note above).</li>
          <li>Setup problems: run <code>./doctor.sh</code> in the project folder.</li>
        </ul>
      </section>
      <section className="card" style={{ padding: 16 }}>
        <h2>Glossary</h2>
        <dl className="glossary">
          {TERMS.map(([t, d]) => <div key={t} style={{ margin: '8px 0' }}><dt><b>{t}</b></dt><dd className="muted" style={{ margin: 0 }}>{d}</dd></div>)}
        </dl>
      </section>
    </div>
  );
}
