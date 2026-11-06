import { describe, expect, it } from 'vitest';
import { awardTerms, buildEdit } from './approvalEdit';

describe('award payment terms', () => {
  const ev = { ranking: [{ supplier: 'S1', terms: [{ days: 30, price: '10.0000' }, { days: 60, price: '10.1000' }] }, { supplier: 'S2' }] };
  it('lists the offered terms of one supplier', () => {
    expect(awardTerms(ev, 'S1').map((t) => t.days)).toEqual([30, 60]);
    expect(awardTerms(ev, 'S2')).toEqual([]);
  });
  it('sends a term only when it was offered for that supplier', () => {
    expect(buildEdit('AWARD', { supplier: 'S1', paymentDays: 60 }, ev)).toEqual({ supplier: 'S1', payment_days: 60 });
    expect(buildEdit('AWARD', { supplier: 'S1', paymentDays: 7 }, ev)).toEqual({ supplier: 'S1' });
    expect(buildEdit('AWARD', { supplier: 'S2', paymentDays: 60 }, ev)).toEqual({ supplier: 'S2' });
  });
});
