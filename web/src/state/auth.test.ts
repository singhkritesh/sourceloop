import { describe, expect, it } from 'vitest';
import type { Me, Role } from '../api/types';
import { can, isInspector, isSupplierScoped } from './auth';

const me = (role: Role, kind: 'BUYER' | 'SUPPLIER' = 'BUYER'): Me => ({ id: 'u', email: 'u@x.test', role, org: { id: 'o', name: 'Org', kind } });
const ALL: Role[] = ['BUYER', 'SUPPLIER', 'COMPLIANCE', 'QUALITY', 'LEGAL', 'FINANCE', 'EVALUATOR', 'DESIGNER', 'ADMIN', 'SUPER_ADMIN'];
const who = (pred: (r: Role) => boolean) => ALL.filter(pred);

describe('who may do what in the UI (the server enforces every one of these)', () => {
  it('only inspectors get the agent workflow: SBN super admin, evaluator and organization admins', () => {
    expect(who(isInspector)).toEqual(['EVALUATOR', 'ADMIN', 'SUPER_ADMIN']);
    for (const r of ['BUYER', 'COMPLIANCE', 'QUALITY', 'LEGAL', 'FINANCE', 'SUPPLIER', 'DESIGNER'] as Role[]) expect(isInspector(r)).toBe(false);
  });
  it('the admin of a SUPPLIER organization gets the supplier view, not the buyer’s side', () => {
    expect(isSupplierScoped(me('SUPPLIER', 'SUPPLIER'))).toBe(true);
    expect(isSupplierScoped(me('ADMIN', 'SUPPLIER'))).toBe(true);
    expect(isSupplierScoped(me('ADMIN', 'BUYER'))).toBe(false);
    expect(isSupplierScoped(me('SUPER_ADMIN'))).toBe(false);
    expect(isSupplierScoped(null)).toBe(false);
  });
  it('platform configuration is SBN’s: an organization admin has no Studio or Settings', () => {
    expect(who(can.seeStudio)).toEqual(['EVALUATOR', 'DESIGNER', 'SUPER_ADMIN']);
    expect(who(can.seeSettings)).toEqual(['EVALUATOR', 'SUPER_ADMIN']);
    expect(who(can.editSettings)).toEqual(['SUPER_ADMIN']);
    expect(who(can.adminStudio)).toEqual(['SUPER_ADMIN']);
  });
  it('the organization console is for admins; SBN also lists every organization; the evaluator only reads', () => {
    expect(who(can.seeOrganization)).toEqual(['EVALUATOR', 'ADMIN', 'SUPER_ADMIN']);
    expect(who(can.editOrganization)).toEqual(['ADMIN', 'SUPER_ADMIN']);
    expect(who(can.manageOrganizations)).toEqual(['EVALUATOR', 'SUPER_ADMIN']);
  });
  it('only a buyer starts a project; buyer, org admin and SBN can resume or stop one', () => {
    expect(who(can.createProject)).toEqual(['BUYER']);
    expect(who(can.controlRun)).toEqual(['BUYER']);
    expect(who(can.resume)).toEqual(['BUYER', 'ADMIN', 'SUPER_ADMIN']);
    expect(who(can.stop)).toEqual(['BUYER', 'ADMIN', 'SUPER_ADMIN']);
  });
  it('approvals: buyer-side roles decide; the evaluator reads; suppliers have none', () => {
    expect(who(can.seeApprovals)).toEqual(['BUYER', 'COMPLIANCE', 'QUALITY', 'LEGAL', 'FINANCE', 'EVALUATOR', 'ADMIN', 'SUPER_ADMIN']);
    expect(who(can.resolveApprovals)).toEqual(['BUYER', 'COMPLIANCE', 'QUALITY', 'LEGAL', 'FINANCE', 'ADMIN', 'SUPER_ADMIN']);
    expect(can.seeApprovals('SUPPLIER')).toBe(false);
  });
});
