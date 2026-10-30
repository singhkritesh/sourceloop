export type Role = 'BUYER' | 'SUPPLIER' | 'COMPLIANCE' | 'QUALITY' | 'LEGAL' | 'FINANCE' | 'EVALUATOR' | 'DESIGNER' | 'ADMIN' | 'SUPER_ADMIN';

export interface Me {
  id: string;
  email: string;
  role: Role;
  org: { id: string; name: string; kind: 'BUYER' | 'SUPPLIER' };
}

export const STAGES = ['NEED', 'DISCOVERY', 'QUALIFICATION', 'SOURCING', 'CONTRACTING', 'ONBOARDING'] as const;
export type Stage = (typeof STAGES)[number] | 'DONE';

export type ProjectStatus = 'DRAFT' | 'RUNNING' | 'ESCALATED' | 'STOPPED' | 'DONE' | 'FAILED_RECOVERABLE' | 'STALLED' | 'BUDGET_EXCEEDED' | 'FAILED' | (string & {});

export interface Project {
  id: string;
  title: string;
  stage: Stage;
  status: ProjectStatus;
  outcome: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  // buyer-side only (absent for SUPPLIER)
  need_text?: string;
  brief?: Record<string, unknown>;
  reason?: string | null;
  tokens_used?: number;
  llm_mode?: string;
  buyer_org_id?: string;
}

export const SCENARIOS = ['happy', 'no_deal', 'below_floor', 'locked_clause', 'poisoned_news', 'inflated_claims'] as const;
export type Scenario = (typeof SCENARIOS)[number];

export interface ProjectMessage {
  id: string;
  seq: number;
  stage: Stage;
  type: string;
  from_agent: string;
  to_agent: string;
  /** server-derived AI disclosure flag (absent on older payloads) */
  ai_generated?: boolean;
  in_reply_to: string | null;
  payload: Record<string, unknown>;
  ts: string | null;
}

export interface AuditEventRec {
  seq: number;
  ts: string;
  actor_type: string;
  actor_id?: string;
  action: string;
  rule_id: string | null;
  evidence: unknown[];
  outcome: string | null;
  approver_id?: string | null;
  config_version: number | null;
  prev_hash: string;
  hash: string;
}

export interface AuditVerify {
  valid: boolean;
  first_bad_seq: number | null;
  events: number;
  head_hash: string | null;
}

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EDITED';
export interface Approval {
  id: string;
  project_id: string;
  kind: string;
  stage: string;
  target_role: Role;
  status: ApprovalStatus;
  reasons: string[];
  evidence: unknown;
  recommendation: unknown;
  comment: string | null;
  version: number;
  created_at: string;
  resolved_at: string | null;
  due_at?: string;
  overdue?: boolean;
}

export interface OverrideRate {
  resolver_id: string;
  email: string | null;
  role: string;
  approved: number;
  edited: number;
  rejected: number;
  decisions: number;
  override_rate: number;
  flag: string | null;
}

/* ---------- SSE event payloads (api/sse.py) ---------- */
export type RunEventName =
  | 'stage_changed' | 'message' | 'subagent_call' | 'subagent_result' | 'policy_block'
  | 'escalation_created' | 'approval_resolved' | 'audit_event' | 'run_finished' | 'error';

export interface SettingsView {
  llm_mode?: string;
  token_budget_per_project?: number;
  app_env?: string;
  cors_origins?: string[];
  active_config_version?: number | null;
  setup_complete?: boolean;
  jwt_secret?: string;
  org_signing_secret?: string;
  config_encryption_key?: string | null;
  database_url?: string;
  [k: string]: unknown;
}

// ---- perspectives (GET /projects/{id}/overview): what each login sees of a project
export interface OfferRow { supplier_id: string; round: number; side: 'supplier' | 'buyer' | 'you'; unit_price: string; lead_time_weeks: number; capacity_units?: number | null }
export interface Candidate {
  supplier_id: string; name: string; country: string; qualification: string | null; reasons: string[]; rounds: number;
  latest_offer: OfferRow | null; awarded: boolean;
}
export interface AgentPerf {
  agent: string; decisions: number; model_chosen: number; rule_used: number; fallbacks: Record<string, number>; agreement_with_rule_pct: number | null;
  avg_confidence: number | null; avg_p_chosen: number | null; avg_ms: number | null; tokens: number; judge: Record<string, number>;
}
export interface AgentDecisionRow {
  seq: number; agent: string; round: number | null; rule: string; chosen: string; source: 'model' | 'rule'; agrees_with_rule: boolean;
  p_chosen: number | null; confidence: number | null; fallback: string | null; ms: number | null; model: string | null;
}
export interface AgentPerformance { live_model_decisions: boolean; agents: AgentPerf[]; decisions: AgentDecisionRow[] }

export interface TurnReview { supplier_id: string | null; round: number | null; actor: 'buyer' | 'supplier' | 'you'; action: string; level: 'APPROPRIATE' | 'CONCERN' | 'INAPPROPRIATE'; notes: string[] }
export interface Decision { kind: string; role: string; status: ApprovalStatus; stage: string; resolved_at: string | null; comment: string }
export interface Milestone { stage: string; at: string }
export interface ContractView { status: string; signed: boolean; clauses: unknown[]; deviations?: unknown[] }
export interface BuyerOverview {
  perspective: 'buyer'; negotiation_review?: TurnReview[]; candidates: Candidate[]; offers: OfferRow[]; decisions: Decision[]; milestones: Milestone[];
  award: { supplier_id: string; name: string; unit_price: string | null; lead_time_weeks: number | null } | null; contract: ContractView | null;
}
export interface SupplierOverview { perspective: 'supplier'; negotiation_review?: TurnReview[]; offers: OfferRow[]; outcome: 'DEAL' | 'CLOSED' | null; contract?: ContractView }
export type Overview = BuyerOverview | SupplierOverview;

export interface ActivityItem {
  ord: number; agent: string; supplier_id: string | null; tool: string; ok: boolean | null; error: string | null; sources: number;
  confidence: number; latency_ms: number; model_id: string | null; tokens: number; audit_seq: number;
}
export interface ActivitySummary { agent: string; tool: string; calls: number; failed: number; avg_confidence: number; avg_latency_ms: number; tokens: number }

// ---- organization administration
export interface OrgInfo { id: string; name: string; kind: 'BUYER' | 'SUPPLIER'; users: number; override_version: number | null; roles: Role[] }
export interface OrgUser { id: string; email: string; role: Role; disabled: boolean; locked: boolean; created_at: string }
export interface OrgSummary { id: string; name: string; kind: 'BUYER' | 'SUPPLIER'; users: number; projects: number; override_version: number | null; platform: boolean }
export interface AgentEditable { timeout_s: number; max_tool_calls: number | null; lead: boolean; model_override: { provider: string; model: string } | null }
export interface OverrideSpec { schema_version?: number; agents?: Record<string, { timeout_s?: number; max_tool_calls?: number; model_override?: { provider: string; model: string } }>; prompts?: Record<string, { body: string }> }
export interface OverridesView {
  kind: 'BUYER' | 'SUPPLIER'; platform_version: number; active_version: number | null; active: OverrideSpec;
  versions: { version: number; content_hash: string; note: string; created_at: string; created_by: string }[];
  editable: { agents: Record<string, AgentEditable>; prompts: Record<string, { body: string }>; models: string[]; providers: string[] };
}
