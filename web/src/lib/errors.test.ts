import { describe, expect, it } from 'vitest';
import { ApiError } from '../api/http';
import { fieldErrors, friendlyMessage, isLostOnRestart, isRuntimeUnavailable, statusLabel } from './errors';
import { buildEdit, editOptions, isEditable } from './approvalEdit';

describe('error mapping', () => {
  it('maps new codes to clear messages', () => {
    expect(friendlyMessage(new ApiError(413, 'PAYLOAD_TOO_LARGE', 'x'))).toMatch(/too large/);
    expect(friendlyMessage(new ApiError(413, 'WHATEVER', 'x'))).toMatch(/too large/);
    expect(friendlyMessage(new ApiError(429, 'QUOTA_EXCEEDED', 'x'))).toMatch(/quota/);
    expect(friendlyMessage(new ApiError(409, 'RUNTIME_UNAVAILABLE', 'x'))).toMatch(/restarted/);
    expect(isRuntimeUnavailable(new ApiError(409, 'RUNTIME_UNAVAILABLE', 'x'))).toBe(true);
    expect(isRuntimeUnavailable(new ApiError(409, 'VERSION', 'x'))).toBe(false);
  });
  it('429 with Retry-After and unknown codes', () => {
    expect(friendlyMessage(new ApiError(429, 'RATE_LIMITED', 'x', undefined, 7))).toMatch(/7s/);
    expect(friendlyMessage(new ApiError(500, 'BOOM', 'server said'))).toBe('server said');
  });
  it('extracts field errors from several shapes', () => {
    const mk = (error: unknown) => new ApiError(422, 'INVALID_EDIT', 'm', undefined, undefined, { error });
    expect(fieldErrors(mk({ details: { include: 'unknown supplier' } }))).toEqual({ include: 'unknown supplier' });
    expect(fieldErrors(mk({ fields: [{ field: 'edit.fail', message: 'bad id' }] }))).toEqual({ fail: 'bad id' });
    expect(fieldErrors(mk({ details: [{ loc: ['body', 'edit', 'supplier'], msg: 'nope' }] }))).toEqual({ supplier: 'nope' });
    expect(fieldErrors(new Error('x'))).toEqual({});
  });
  it('lost on restart and unknown statuses render safely', () => {
    expect(isLostOnRestart({ status: 'FAILED', reason: 'RESTART_LOST_RUNTIME' })).toBe(true);
    expect(isLostOnRestart({ status: 'STOPPED', reason: 'RESTART_LOST_RUNTIME' })).toBe(true); // what the API actually returns
    expect(isLostOnRestart({ status: 'FAILED', reason: 'other' })).toBe(false);
    expect(isLostOnRestart({ status: 'STOPPED', reason: 'award_rejected' })).toBe(false);
    expect(statusLabel({ status: 'FAILED', reason: 'RESTART_LOST_RUNTIME' })).toBe('lost on restart');
    expect(statusLabel({ status: 'SOME_NEW_STATE' })).toBe('some new state');
  });
});

describe('edit shape builder', () => {
  const flagged = { flagged: { S1: { status: 'REVIEW' }, S2: { status: 'BLOCKED' } } };
  const cands = { candidates: ['S1', 'S2'] };
  const rank = { ranking: [{ supplier: 'S2' }, { supplier: 'S1' }] };
  it('options come from evidence only', () => {
    expect(editOptions('COMPLIANCE_FLAG', flagged)).toEqual(['S1', 'S2']);
    expect(editOptions('TECHNICAL_QUAL', cands)).toEqual(['S1', 'S2']);
    expect(editOptions('AWARD', rank)).toEqual(['S2', 'S1']);
    expect(editOptions('AWARD', null)).toEqual([]);
  });
  it('builds per-kind shapes', () => {
    expect(buildEdit('COMPLIANCE_FLAG', { selected: ['S1', 'S1', 'ZZ'] }, flagged)).toEqual({ include: ['S1'] });
    expect(buildEdit('TECHNICAL_QUAL', { selected: ['S2'] }, cands)).toEqual({ fail: ['S2'] });
    expect(buildEdit('AWARD', { supplier: 'S1' }, rank)).toEqual({ supplier: 'S1' });
    expect(buildEdit('CLAUSE', { acceptChange: false }, {})).toEqual({ accept_change: false });
  });
  it('rejects invalid input and non-editable kinds', () => {
    expect(buildEdit('AWARD', { supplier: 'ZZ' }, rank)).toBeNull();
    expect(buildEdit('COMPLIANCE_FLAG', { selected: [] }, flagged)).toBeNull();
    expect(buildEdit('CLAUSE', {}, {})).toBeNull();
    expect(buildEdit('SIGNATURE', { selected: ['S1'] }, {})).toBeNull();
    expect(isEditable('BANK_DETAILS')).toBe(false);
    expect(isEditable('AWARD')).toBe(true);
  });
});
