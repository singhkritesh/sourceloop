import { create } from 'zustand';
import { auth } from '../api/endpoints';
import { setUnauthenticatedHandler } from '../api/http';
import type { Me, Role } from '../api/types';

interface AuthState {
  me: Me | null;
  status: 'loading' | 'authed' | 'anon';
  bootstrap: () => Promise<void>;
  setMe: (m: Me) => void;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  me: null,
  status: 'loading',
  bootstrap: async () => {
    try {
      set({ me: await auth.me(), status: 'authed' });
    } catch {
      set({ me: null, status: 'anon' });
    }
  },
  setMe: (me) => set({ me, status: 'authed' }),
  logout: async () => {
    try {
      await auth.logout();
    } finally {
      set({ me: null, status: 'anon' });
    }
  },
}));

let clearCache: () => void = () => undefined;
export function registerCacheClear(fn: () => void): void {
  clearCache = fn;
}
setUnauthenticatedHandler(() => {
  if (useAuth.getState().status === 'authed') {
    useAuth.setState({ me: null, status: 'anon' });
    clearCache();
  }
});

// UI affordances only; the server enforces every one of these.
const BUYER_SIDE: Role[] = ['BUYER'];
/** The admin of a SUPPLIER organization sees the supplier view of a project, never the buyer's side. */
export const isSupplierScoped = (me?: Me | null): boolean => me?.role === 'SUPPLIER' || (me?.role === 'ADMIN' && me.org.kind === 'SUPPLIER');
/** Inspectors may open the agent workflow: SBN's super admin and evaluator everywhere, an org admin for its own organization. */
export const isInspector = (r?: Role): boolean => r === 'SUPER_ADMIN' || r === 'EVALUATOR' || r === 'ADMIN';
export const can = {
  createProject: (r?: Role) => r === 'BUYER',
  controlRun: (r?: Role) => r === 'BUYER',
  resume: (r?: Role) => r === 'BUYER' || r === 'ADMIN' || r === 'SUPER_ADMIN',
  stop: (r?: Role) => r === 'BUYER' || r === 'ADMIN' || r === 'SUPER_ADMIN',
  inspect: isInspector,
  verifyAudit: (r?: Role) => r === 'EVALUATOR' || r === 'ADMIN' || r === 'SUPER_ADMIN',
  seeProjects: (r?: Role) => !!r && r !== 'DESIGNER',
  seeApprovals: (r?: Role) => !!r && [...BUYER_SIDE, 'EVALUATOR', 'ADMIN', 'SUPER_ADMIN'].includes(r),
  resolveApprovals: (r?: Role) => r === 'BUYER',   // admins set rules and mediate; they never approve
  seeStudio: (r?: Role) => r === 'DESIGNER' || r === 'SUPER_ADMIN' || r === 'EVALUATOR',
  editDraft: (r?: Role) => r === 'DESIGNER' || r === 'SUPER_ADMIN',
  adminStudio: (r?: Role) => r === 'SUPER_ADMIN',
  seeSettings: (r?: Role) => r === 'SUPER_ADMIN' || r === 'EVALUATOR',
  editSettings: (r?: Role) => r === 'SUPER_ADMIN',
  seeOrganization: (r?: Role) => r === 'ADMIN' || r === 'SUPER_ADMIN' || r === 'EVALUATOR',
  editOrganization: (r?: Role) => r === 'ADMIN' || r === 'SUPER_ADMIN',
  manageOrganizations: (r?: Role) => r === 'SUPER_ADMIN' || r === 'EVALUATOR',
};
export const homeFor = (r?: Role): string => (r === 'DESIGNER' ? '/studio' : '/projects');
