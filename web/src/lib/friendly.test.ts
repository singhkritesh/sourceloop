import { describe, expect, it } from 'vitest';
import { actionText, APPROVAL_INFO, approvalTitle, nextStep, plainStatus, reasonText, roleTitle, toolText } from './friendly';

describe('friendly text', () => {
  it('has a title and explanation for every approval kind the backend can raise', () => {
    const kinds = ['REQUIREMENT', 'COMPLIANCE_FLAG', 'TECHNICAL_QUAL', 'AWARD', 'CLAUSE', 'SIGNATURE', 'SUPPLIER_ACCEPT', 'BANK_DETAILS', 'HOLD_RELEASE', 'MANDATE_BREACH', 'LOW_CONFIDENCE', 'LLM_UNAVAILABLE', 'STALLED', 'BUDGET', 'RULE'];
    for (const k of kinds) {
      const i = APPROVAL_INFO[k];
      expect(i, k).toBeTruthy();
      expect(i?.title && i.ask && i.why && i.approve && i.reject).toBeTruthy();
    }
  });
  it('falls back to readable text for unknown codes', () => {
    expect(approvalTitle('NEW_THING')).toBe('New thing');
    expect(reasonText('SOMETHING_ODD')).toBe('Something odd');
    expect(actionText('brand_new_action')).toBe('Brand new action');
  });
  it('explains reason codes with parameters', () => {
    expect(reasonText('MISSING_DATA:lead_time_weeks,years_in_business')).toBe('Missing data: lead time weeks, years in business');
    expect(reasonText('CERT_EXPIRED:ISO9001')).toBe('Certificate expired: ISO9001');
    expect(reasonText('WATCHLIST_HIT')).toMatch(/watchlist/i);
  });
  it('plain status distinguishes deal, no deal and restart loss', () => {
    expect(plainStatus('DONE', 'DEAL').label).toMatch(/deal reached/);
    expect(plainStatus('DONE', 'NO_DEAL').label).toMatch(/no deal/);
    expect(plainStatus('STOPPED', null, true).label).toMatch(/restart/);
    expect(plainStatus('ESCALATED', null).label).toMatch(/decision/);
  });
  it('next step tells the right person to act and others to wait', () => {
    const base = { status: 'ESCALATED', stage: 'QUALIFICATION', outcome: null, lost: false, reason: null, pendingKinds: [{ kind: 'COMPLIANCE_FLAG', role: 'COMPLIANCE' }] };
    expect(nextStep({ ...base, role: 'COMPLIANCE' }).headline).toMatch(/Your decision is needed/);
    expect(nextStep({ ...base, role: 'BUYER' }).headline).toMatch(/Waiting for Compliance/);
    expect(nextStep({ ...base, role: 'BUYER' }).action).toBeUndefined(); // not the buyer's decision: no button
    expect(nextStep({ ...base, role: 'COMPLIANCE' }).action).toBe('approvals');
    expect(nextStep({ ...base, pendingKinds: [], status: 'DRAFT', role: 'BUYER' }).action).toBe('start');
    expect(nextStep({ ...base, pendingKinds: [], status: 'DONE', outcome: 'DEAL', role: 'BUYER' }).headline).toBe('Deal reached');
    expect(nextStep({ ...base, lost: true, role: 'BUYER' }).headline).toMatch(/interrupted/);
  });
  it('names roles and agent tools for people, with a readable fallback', () => {
    expect(roleTitle('SUPER_ADMIN')).toBe('SBN super admin');
    expect(roleTitle('ADMIN')).toBe('Organization admin');
    expect(roleTitle('SOMETHING_NEW')).toBe('Something new');
    expect(toolText('regional_news_risk')).toBe('Regional news risk');
    expect(toolText('buyer_lead')).toBe('Buyer lead agent');
    expect(toolText('brand_new_tool')).toBe('Brand new tool');
  });
});
