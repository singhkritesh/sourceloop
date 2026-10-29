import { describe, expect, it } from 'vitest';
import { isAiGenerated } from './aiDisclosure';

describe('isAiGenerated', () => {
  it('prefers the server flag', () => {
    expect(isAiGenerated('buyer', false)).toBe(false);
    expect(isAiGenerated('orchestrator', true)).toBe(true);
  });
  it('falls back to the sender kind', () => {
    expect(isAiGenerated('buyer')).toBe(true);
    expect(isAiGenerated('supplier:S1')).toBe(true);
    expect(isAiGenerated('orchestrator')).toBe(false);
    expect(isAiGenerated('')).toBe(false);
  });
});
