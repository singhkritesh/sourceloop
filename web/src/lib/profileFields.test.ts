import { describe, expect, it } from 'vitest';
import { parseProfile } from './profileFields';

describe('parseProfile', () => {
  it('leaves blank fields out and keeps valid numbers', () => {
    expect(parseProfile({ years_in_business: '12', lead_time_weeks: ' ', financial_risk: '0.2' })).toEqual({ values: { years_in_business: 12, financial_risk: 0.2 }, errors: {} });
  });
  it('reports a figure outside its range or that is not a number', () => {
    const r = parseProfile({ financial_risk: '7', capacity_units: 'lots', lead_time_weeks: '200' });
    expect(r.values).toEqual({});
    expect(Object.keys(r.errors).sort()).toEqual(['capacity_units', 'financial_risk', 'lead_time_weeks']);
  });
});
