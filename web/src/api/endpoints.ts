import { http } from './http';
import type {
  ActivityItem, ActivitySummary, Approval, AuditEventRec, AuditVerify, Me, OrgInfo, OrgSummary, OrgUser, OverrideRate, OverridesView, OverrideSpec, Overview,
  Project, ProjectMessage, Role, Scenario, SettingsView,
} from './types';

export interface DemoAccount { email: string; role: string; org: string; org_kind: string }

export const auth = {
  demoRoles: () => http.get<{ enabled: boolean; accounts: DemoAccount[] }>('/auth/demo-roles', { noAuthRetry: true }),
  demoLogin: (email: string) => http.post<Me>('/auth/demo-login', { email }, { noAuthRetry: true }),
  login: (email: string, password: string) => http.post<Me>('/auth/login', { email, password }, { noAuthRetry: true }),
  me: () => http.get<Me>('/auth/me'),
  logout: () => http.post<void>('/auth/logout', undefined, { noAuthRetry: true }),
};

export interface NewProjectBody {
  title: string;
  need_text: string;
  scenario: Scenario;
  need_index: number;
  seed: number;
  // optional structured need (replaces the synthetic need's fields when present)
  category?: string;
  industry?: string;
  volume_units?: number;
  required_certs?: string[];
  excluded_countries?: string[];
  max_lead_time_weeks?: number;
  target_price?: string;
}

export const projects = {
  list: () => http.get<{ items: Project[] }>('/projects', { query: { limit: 100 } }),
  get: (id: string) => http.get<Project>(`/projects/${id}`),
  create: (b: NewProjectBody) => http.post<Project>('/projects', b),
  start: (id: string) => http.post<{ id: string; status: string }>(`/projects/${id}/start`),
  resume: (id: string) => http.post<{ id: string; status: string }>(`/projects/${id}/resume`),
  stop: (id: string) => http.post<{ id: string; status: string }>(`/projects/${id}/stop`),
  messages: (id: string, afterSeq = 0) =>
    http.get<{ items: ProjectMessage[] }>(`/projects/${id}/messages`, { query: { after_seq: afterSeq, limit: 500 } }),
  audit: (id: string, afterSeq = 0) =>
    http.get<{ items: AuditEventRec[] }>(`/projects/${id}/audit`, { query: { after_seq: afterSeq, limit: 500 } }),
  verify: (id: string) => http.get<AuditVerify>(`/projects/${id}/audit/verify`),
  escalations: (id: string) => http.get<{ items: Approval[] }>(`/projects/${id}/escalations`),
  overview: (id: string) => http.get<Overview>(`/projects/${id}/overview`),
  activity: (id: string) => http.get<{ items: ActivityItem[]; summary: ActivitySummary[] }>(`/projects/${id}/activity`),
  streamUrl: (id: string) => `/api/v1/projects/${id}/stream`,
};

export interface ResolveBody {
  status: 'APPROVED' | 'REJECTED' | 'EDITED';
  comment: string;
  version: number;
  edit?: Record<string, unknown>;
}
export const approvals = {
  list: (status?: string) => http.get<{ items: Approval[] }>('/approvals', { query: { status, limit: 200 } }),
  resolve: (id: string, b: ResolveBody) => http.post<Approval>(`/approvals/${id}/resolve`, b),
};

export const settings = {
  get: () => http.get<SettingsView>('/settings'),
  put: (b: { llm_mode?: string; token_budget_per_project?: number }) => http.put<SettingsView>('/settings', b),
};

export const reviewers = {
  overrideRates: () => http.get<{ items: OverrideRate[]; min_decisions: number; rubber_stamp_threshold: number }>('/reviewers/override-rates'),
};

export const health = { get: () => http.get<{ status: string; db?: boolean; llm_mode?: string }>('/health', { noAuthRetry: true }) };

const orgQ = (orgId?: string) => (orgId ? { org_id: orgId } : undefined);
export const org = {
  get: (orgId?: string) => http.get<OrgInfo>('/org', { query: orgQ(orgId) }),
  users: (orgId?: string) => http.get<{ items: OrgUser[] }>('/org/users', { query: orgQ(orgId) }),
  createUser: (b: { email: string; role: Role }, orgId?: string) =>
    http.post<{ user: OrgUser; initial_password: string }>(`/org/users${orgId ? `?org_id=${orgId}` : ''}`, b),
  patchUser: (id: string, b: { role?: Role; disabled?: boolean }, orgId?: string) => http.patch<OrgUser>(`/org/users/${id}${orgId ? `?org_id=${orgId}` : ''}`, b),
  resetPassword: (id: string, orgId?: string) => http.post<{ initial_password: string }>(`/org/users/${id}/reset-password${orgId ? `?org_id=${orgId}` : ''}`),
  overrides: (orgId?: string) => http.get<OverridesView>('/org/overrides', { query: orgQ(orgId) }),
  saveOverrides: (payload: OverrideSpec, note: string, orgId?: string) =>
    http.put<{ version: number; content_hash: string }>(`/org/overrides${orgId ? `?org_id=${orgId}` : ''}`, { payload, note }),
  rollbackOverrides: (to_version: number, orgId?: string) =>
    http.post<{ version: number }>(`/org/overrides/rollback${orgId ? `?org_id=${orgId}` : ''}`, { to_version }),
};
export const admin = {
  orgs: () => http.get<{ items: OrgSummary[] }>('/admin/orgs'),
  createOrg: (b: { name: string; kind: 'BUYER' | 'SUPPLIER'; admin_email: string }) =>
    http.post<{ org: { id: string; name: string; kind: string }; admin: OrgUser; initial_password: string }>('/admin/orgs', b),
  createStaff: (b: { email: string; role: Role }) => http.post<{ user: OrgUser; initial_password: string }>('/admin/staff', b),
};
