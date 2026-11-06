/** The figures a supplier can save for its agent to give when a buyer asks. A closed list: no prices, limits or floors. */
export const PROFILE_FIELDS = [
  { key: 'capacity_units', label: 'Capacity (units per order)', hint: 'Whole units you can supply in one order.', min: 1, max: 1_000_000_000, step: 1 },
  { key: 'lead_time_weeks', label: 'Lead time (weeks)', hint: 'Usual weeks from order to delivery.', min: 0, max: 104, step: 1 },
  { key: 'financial_risk', label: 'Financial risk (0 to 1)', hint: '0 is no risk, 1 is the highest. Use the figure from your latest assessment.', min: 0, max: 1, step: 0.01 },
  { key: 'years_in_business', label: 'Years in business', hint: 'Whole years since founding.', min: 0, max: 300, step: 1 },
] as const;

/** Text inputs to the numbers that go to the server; a blank field is left out, anything outside its range is reported. */
export function parseProfile(input: Record<string, string>): { values: Record<string, number>; errors: Record<string, string> } {
  const values: Record<string, number> = {};
  const errors: Record<string, string> = {};
  for (const f of PROFILE_FIELDS) {
    const raw = (input[f.key] ?? '').trim();
    if (!raw) continue;
    const n = Number(raw);
    if (!Number.isFinite(n) || n < f.min || n > f.max) errors[f.key] = `Enter a number from ${f.min} to ${f.max}.`;
    else values[f.key] = n;
  }
  return { values, errors };
}
