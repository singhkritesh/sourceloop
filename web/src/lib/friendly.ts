/** Plain-language labels and explanations for the technical codes the API returns. Display only: never used for logic. */

export interface ApprovalInfo { title: string; ask: string; why: string; approve: string; reject: string; edit?: string }

export const APPROVAL_INFO: Record<string, ApprovalInfo> = {
  REQUIREMENT: {
    title: 'Confirm the sourcing need',
    ask: 'Check that the agent understood what you want to buy.',
    why: 'Everything downstream (which suppliers are invited, what limits apply) is built from this brief.',
    approve: 'The agents start looking for suppliers using this brief.',
    reject: 'The run stops and nothing is sent to suppliers.',
  },
  COMPLIANCE_FLAG: {
    title: 'Review flagged suppliers',
    ask: 'Some suppliers could not be cleared automatically. Decide whether they stay in the process.',
    why: 'Automated screening never clears a watchlist or missing-data case on its own. A person has to.',
    approve: 'The recommended suppliers move on to technical qualification.',
    reject: 'The flagged suppliers are dropped; the run continues with the rest or stops if none remain.',
    edit: 'Choose which flagged suppliers to include anyway.',
  },
  TECHNICAL_QUAL: {
    title: 'Confirm technical pass / fail',
    ask: 'Check the technical assessment of each supplier (certificates, capacity, lead time).',
    why: 'Quality owns the pass / fail decision. The agent only recommends.',
    approve: 'Suppliers that passed go to the quote request.',
    reject: 'The shortlist is rejected and the run stops.',
    edit: 'Mark specific suppliers as failing.',
  },
  AWARD: {
    title: 'Choose the winning supplier',
    ask: 'The negotiation is finished. Confirm who gets the business.',
    why: 'An award commits the company, so it always needs a human decision.',
    approve: 'The recommended supplier wins and the contract stage starts.',
    reject: 'No award is made and the run stops.',
    edit: 'Award to a different supplier from the offers received.',
  },
  CLAUSE: {
    title: 'Decide on a contract clause change',
    ask: 'A party wants to change a contract clause.',
    why: 'Legal decides on clause changes, and protected clauses (liability, termination, IP) can never change automatically.',
    approve: 'The proposed change is accepted.',
    reject: 'The original clause stays.',
    edit: 'Accept or decline the proposed change.',
  },
  SIGNATURE: {
    title: 'Approve the contract for signature',
    ask: 'Review the final contract terms before signing.',
    why: 'Signing is binding, so Legal must approve it.',
    approve: 'The contract is signed and the deal is recorded.',
    reject: 'The contract is not signed and the run stops.',
  },
  BANK_DETAILS: {
    title: 'Verify supplier bank details',
    ask: 'Confirm the supplier’s bank details are genuine before any payment setup.',
    why: 'Changed or new bank details are a common fraud route, so Finance verifies them.',
    approve: 'The bank details are accepted for the supplier record.',
    reject: 'The details are refused and the hand-off is held.',
  },
  HOLD_RELEASE: {
    title: 'Release the payment hold',
    ask: 'A payment hold was placed on this deal. Release it when Finance is satisfied.',
    why: 'Only Finance can release a hold.',
    approve: 'The hold is released.',
    reject: 'The hold stays in place.',
  },
  MANDATE_BREACH: {
    title: 'An agent tried to exceed its limits',
    ask: 'An agent attempted something outside its approved limits. It was blocked.',
    why: 'Limits are enforced in code; the blocked message was never delivered.',
    approve: 'Acknowledge and let the run continue.',
    reject: 'Stop the run.',
  },
  LOW_CONFIDENCE: {
    title: 'The agent is not confident',
    ask: 'An assessment came back with low confidence, so a person should look.',
    why: 'Low-confidence results are never acted on automatically.',
    approve: 'Accept the assessment as it is.',
    reject: 'Reject it and stop the run.',
  },
  LLM_UNAVAILABLE: {
    title: 'The AI model was unavailable',
    ask: 'A step needing the AI model could not complete.',
    why: 'The system fails safe instead of guessing.',
    approve: 'Continue without that step.',
    reject: 'Stop the run.',
  },
  STALLED: {
    title: 'The negotiation is stuck',
    ask: 'The two sides stopped making progress.',
    why: 'Bounded negotiation escalates instead of looping forever.',
    approve: 'Let the agents try again.',
    reject: 'End with no deal.',
  },
  BUDGET: {
    title: 'AI usage budget reached',
    ask: 'The run used up its AI token budget.',
    why: 'Budgets stop runaway cost.',
    approve: 'Continue.',
    reject: 'Stop the run.',
  },
  RULE: {
    title: 'A business rule needs a decision',
    ask: 'A configured rule asked for human review.',
    why: 'Your Studio rules can only add stops, never remove safety checks.',
    approve: 'Continue.',
    reject: 'Stop the run.',
  },
};

export const approvalTitle = (kind: string): string => APPROVAL_INFO[kind]?.title ?? titleCase(kind);
export const approvalInfo = (kind: string): ApprovalInfo =>
  APPROVAL_INFO[kind] ?? { title: titleCase(kind), ask: 'A person needs to review this.', why: 'The system escalated it.', approve: 'The run continues.', reject: 'The run stops.' };

export function titleCase(s: string): string {
  const t = s.replace(/[._]/g, ' ').toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export const ROLE_LABEL: Record<string, string> = {
  BUYER: 'the buyer', COMPLIANCE: 'Compliance', QUALITY: 'Quality', LEGAL: 'Legal', FINANCE: 'Finance', ADMIN: 'the organization admin', SUPER_ADMIN: 'SBN',
  EVALUATOR: 'an evaluator',
};

/** How a role is shown to people (badges, tables). */
export const ROLE_TITLE: Record<string, string> = {
  BUYER: 'Buyer', SUPPLIER: 'Supplier', COMPLIANCE: 'Compliance', QUALITY: 'Quality', LEGAL: 'Legal', FINANCE: 'Finance', EVALUATOR: 'SBN evaluator',
  DESIGNER: 'SBN designer', ADMIN: 'Organization admin', SUPER_ADMIN: 'SBN super admin',
};
export const roleTitle = (r: string): string => ROLE_TITLE[r] ?? titleCase(r);

const TOOL_TEXT: Record<string, string> = {
  research_suppliers: 'Supplier research', compliance_screen: 'Compliance screening', regional_news_risk: 'Regional news risk', weather_logistics: 'Weather and logistics',
  landed_cost: 'Landed cost', demand_forecast: 'Demand forecast', buyer_verification: 'Buyer verification', capacity_margin: 'Capacity and margin',
  quote_generation: 'Quote generation', compliance_docs: 'Compliance documents', buyer_lead: 'Buyer lead agent', supplier_lead: 'Supplier lead agent',
};
export const toolText = (t: string): string => TOOL_TEXT[t] ?? titleCase(t);
export const roleLabel = (r: string): string => ROLE_LABEL[r] ?? r.toLowerCase();

const REASON_TEXT: Record<string, string> = {
  CONFIRM_REQUIREMENT: 'Needs your confirmation before the search starts',
  WATCHLIST_HIT: 'Possible match on a sanctions or watchlist',
  FORCED_LABOR_RISK: 'Forced-labor risk indicator',
  EXCLUDED_COUNTRY: 'Located in a country you excluded',
  CATEGORY_MISMATCH: 'Does not make this kind of product',
  LEAD_TIME: 'Lead time is longer than allowed',
  CAPACITY: 'Not enough capacity',
  FINANCIAL_RISK: 'Financial risk too high',
  YEARS_IN_BUSINESS: 'Too new in the business',
  PRICE_BAND: 'Price is outside the expected market range',
  PRICE_BAND_NO_INDEX: 'No market price index to check against',
  LOCKED_CLAUSE: 'Touches a protected contract clause',
  CLAUSE_NOT_IN_LIBRARY: 'Clause is not in the approved library',
  CLAUSE_NOT_NEGOTIABLE: 'Clause cannot be negotiated',
  PRICE_BELOW_FLOOR: 'Price is below the supplier’s limit',
  PRICE_ABOVE_MANDATE: 'Price is above the buyer’s limit',
  ROUND_CAP: 'Maximum negotiation rounds reached',
  STEP_TOO_SMALL: 'Price move too small to count',
  CLAIM_NOT_PROVABLE: 'Certificate claimed but not provable',
  EXCLUDED_COUNTRY_INVITE: 'Located in a country you excluded',
  LEAD_TIME_EXCEEDS_MANDATE: 'Lead time is longer than the buyer allows',
  LEAD_TIME_UNACHIEVABLE: 'Lead time the supplier cannot meet',
  CAPACITY_EXCEEDED: 'Quantity above the supplier’s capacity',
  ACCEPT_OUTSIDE_MANDATE: 'Acceptance would break the buyer’s limits',
  ACCEPT_BELOW_FLOOR: 'Acceptance would break the supplier’s limits',
  TARGET_DISCLOSURE_NOT_ALLOWED: 'Target price may not be shared',
  UNKNOWN_OFFER: 'Refers to an offer that does not exist',
};

/** "MISSING_DATA:lead_time_weeks,years_in_business" -> "Missing data: lead time weeks, years in business" */
export function reasonText(code: string): string {
  const [head, rest] = [code.split(':')[0] ?? code, code.includes(':') ? code.slice(code.indexOf(':') + 1) : ''];
  const items = rest ? rest.split(',').map((x) => x.replace(/_/g, ' ')).join(', ') : '';
  if (head === 'MISSING_DATA') return `Missing data: ${items}`;
  if (head === 'CERT_EXPIRED') return `Certificate expired: ${items}`;
  if (head === 'UNVERIFIED_CLAIM') return `Unverified certificate claim: ${items}`;
  const base = REASON_TEXT[head];
  if (base) return items ? `${base} (${items})` : base;
  return titleCase(code.replace(/:/g, ': '));
}

const STAGE_TEXT: Record<string, { name: string; blurb: string }> = {
  NEED: { name: 'Need', blurb: 'Define what to buy' },
  DISCOVERY: { name: 'Discovery', blurb: 'Find suppliers' },
  QUALIFICATION: { name: 'Qualification', blurb: 'Check compliance and capability' },
  SOURCING: { name: 'Sourcing', blurb: 'Quotes and negotiation' },
  CONTRACTING: { name: 'Contracting', blurb: 'Agree the contract' },
  ONBOARDING: { name: 'Onboarding', blurb: 'Hand off to operations' },
  DONE: { name: 'Done', blurb: 'Finished' },
};
export const stageBlurb = (s: string): string => STAGE_TEXT[s]?.blurb ?? '';
export const stageName = (s: string): string => STAGE_TEXT[s]?.name ?? titleCase(s);

const ACTION_TEXT: Record<string, string> = {
  run_started: 'Run started', run_paused: 'Paused for a person', run_resumed: 'Run resumed', run_finished: 'Run finished',
  stage_changed: 'Moved to the next stage', message_delivered: 'Message delivered between agents',
  escalation_created: 'Asked a person to decide', approval_resolved: 'A person made a decision',
  policy_block: 'A limit was enforced', policy_waived: 'A person overrode a rule', tool_denied: 'A tool call was refused',
  ledger_hold_created: 'Payment hold placed', ledger_hold_released: 'Payment hold released',
};
export const actionText = (a: string): string => ACTION_TEXT[a] ?? titleCase(a);

export interface PlainStatus { label: string; tone: 'ok' | 'warn' | 'bad' | 'info' | 'accent' | '' }
export function plainStatus(status: string, outcome: string | null | undefined, lostOnRestart = false): PlainStatus {
  if (lostOnRestart) return { label: 'Interrupted by a restart', tone: 'warn' };
  switch (status) {
    case 'DRAFT': return { label: 'Ready to start', tone: '' };
    case 'RUNNING': return { label: 'In progress', tone: 'accent' };
    case 'ESCALATED': return { label: 'Waiting for a decision', tone: 'warn' };
    case 'DONE': return outcome === 'DEAL' ? { label: 'Completed · deal reached', tone: 'ok' } : outcome === 'NO_DEAL' ? { label: 'Completed · no deal', tone: 'warn' } : { label: 'Completed', tone: 'ok' };
    case 'STOPPED': return { label: 'Stopped', tone: 'bad' };
    case 'FAILED_RECOVERABLE': return { label: 'Hit a problem · can resume', tone: 'bad' };
    case 'STALLED': return { label: 'Stuck · needs attention', tone: 'bad' };
    case 'BUDGET_EXCEEDED': return { label: 'Paused · AI budget reached', tone: 'bad' };
    case 'FAILED': return { label: 'Failed', tone: 'bad' };
    default: return { label: titleCase(status), tone: '' };
  }
}

export interface NextStep { headline: string; detail: string; tone: 'ok' | 'warn' | 'bad' | 'info' | 'accent' | '' ; action?: 'approvals' | 'start' | 'resume' }
export interface NextStepInput { status: string; stage: string; outcome: string | null | undefined; pendingKinds: { kind: string; role: string }[]; lost: boolean; role?: string; reason?: string | null }

/** What a person should read first on a project: what is happening and what happens next. */
export function nextStep(i: NextStepInput): NextStep {
  if (i.lost) return { headline: 'This run was interrupted', detail: 'The server restarted and this run could not be rebuilt (it was a live-AI run, or its inputs changed). The record is intact and nothing was sent outside. Start a new project to run it again.', tone: 'warn' };
  const mine = i.pendingKinds.filter((p) => p.role === i.role);
  if (i.pendingKinds.length) {
    const first = i.pendingKinds[0] as { kind: string; role: string };
    const info = approvalInfo(first.kind);
    if (mine.length) return { headline: `Your decision is needed: ${info.title.toLowerCase()}`, detail: `${info.ask} ${info.why}`, tone: 'warn', action: 'approvals' };
    return { headline: `Waiting for ${roleLabel(first.role)}: ${info.title.toLowerCase()}`, detail: `${info.ask} The agents are paused until ${roleLabel(first.role)} decides.`, tone: 'info' };
  }
  switch (i.status) {
    case 'DRAFT': return { headline: 'Ready when you are', detail: 'Start the run and the buyer agent will take the need through discovery, qualification, sourcing and contracting. It pauses for a person at every important decision.', tone: 'accent', action: 'start' };
    case 'RUNNING': return { headline: `The agents are working on ${stageName(i.stage).toLowerCase()}`, detail: `${stageBlurb(i.stage)}. You will be asked to decide when a human checkpoint comes up. This page updates live.`, tone: 'accent' };
    case 'DONE': return i.outcome === 'DEAL'
      ? { headline: 'Deal reached', detail: 'A supplier was chosen, the contract was approved and every step of the process is on record.', tone: 'ok' }
      : { headline: 'Finished without a deal', detail: i.reason ?? 'The two sides could not agree within their limits, so nothing was awarded. The record shows why.', tone: 'warn' };
    case 'STOPPED': return { headline: 'This run was stopped', detail: i.reason ?? 'It will not continue. The record of what happened is kept.', tone: 'bad' };
    case 'ESCALATED': return { headline: 'Paused for a person', detail: 'A decision is needed before the agents can continue.', tone: 'warn', action: 'approvals' };
    case 'FAILED_RECOVERABLE': case 'STALLED': case 'BUDGET_EXCEEDED': return { headline: plainStatus(i.status, i.outcome).label, detail: i.reason ?? 'You can resume the run once the cause is fixed.', tone: 'bad', action: 'resume' };
    default: return { headline: plainStatus(i.status, i.outcome).label, detail: i.reason ?? '', tone: '' };
  }
}

const MESSAGE_TEXT: Record<string, string> = {
  BRIEF: 'Sourcing brief', INVITE: 'Invitation to quote', INTEREST_REPLY: 'Interest reply', DOC_REQUEST: 'Document request', DOC_SUBMISSION: 'Documents submitted',
  QUAL_RESULT: 'Qualification result', RFQ: 'Request for quote', OFFER: 'Offer', COUNTER: 'Counter-offer', ACCEPT: 'Offer accepted', REJECT: 'Offer declined',
  CONTRACT_DRAFT: 'Contract draft', REDLINE: 'Contract change request', SIGN_REQUEST: 'Signature request', ONBOARD_DATA: 'Onboarding details', ESCALATE: 'Escalation',
};
export const messageTypeText = (t: string): string => MESSAGE_TEXT[t] ?? titleCase(t);

const QUAL_TEXT: Record<string, string> = { QUALIFIED: 'Qualified', REVIEW: 'Needs review', REJECTED: 'Not qualified', BLOCKED: 'Blocked' };
export const qualText = (s: string): string => QUAL_TEXT[s] ?? titleCase(s);
