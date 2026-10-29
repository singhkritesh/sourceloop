/** Pure reducer for the live run: SSE frames -> view state. Idempotent under replay / reconnect (dedupe by seq / id). */
export interface MsgMeta { id: string; auditSeq: number; type: string; from: string; to: string; stage: string; summary: string; aiGenerated?: boolean }
export interface CallRec {
  key: string; seq: number; agent: string; tool: string; inputSummary: string;
  confidence?: number; sourcesCount?: number; latencyMs?: number; done: boolean;
}
export interface BlockRec { seq: number; side: string; ruleId: string | null; messageType: string | null }
export interface EscRec { seq: number; approvalId: string; kind: string; targetRole: string | null; reasons: string[]; resolved?: string }
export interface StageRec { seq: number; from: string; to: string }
export interface AuditLite { seq: number; action: string; ruleId: string | null; hash: string }
export interface ErrRec { seq: number; code: string; message: string }

export interface RunState {
  stage: string;
  lastEventId: number;
  msgMeta: Record<string, MsgMeta>;
  calls: CallRec[];
  callConn: Record<number, number>;
  blocks: BlockRec[];
  escalations: EscRec[];
  stages: StageRec[];
  audit: AuditLite[];
  errors: ErrRec[];
  finished: string | null;
}

export const initialRun = (): RunState => ({
  stage: 'NEED', lastEventId: 0, msgMeta: {}, calls: [], callConn: {}, blocks: [], escalations: [],
  stages: [], audit: [], errors: [], finished: null,
});

type Rec = Record<string, unknown>;
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : v == null ? d : String(v));
const numv = (v: unknown, d = 0): number => (typeof v === 'number' && Number.isFinite(v) ? v : d);

export function reduceEvent(s: RunState, event: string, raw: unknown, connId: number): RunState {
  const d = (raw && typeof raw === 'object' ? raw : {}) as Rec;
  const seq = numv(d.seq);
  const next: RunState = { ...s, lastEventId: Math.max(s.lastEventId, seq) };
  switch (event) {
    case 'stage_changed': {
      if (s.stages.some((x) => x.seq === seq)) return next;
      const to = str(d.to, s.stage);
      return { ...next, stage: to, stages: [...s.stages, { seq, from: str(d.from), to }] };
    }
    case 'message': {
      const m = (d.message ?? {}) as Rec;
      const id = str(m.id);
      if (!id || s.msgMeta[id]) return next;
      return { ...next, msgMeta: { ...s.msgMeta, [id]: {
        id, auditSeq: seq, type: str(m.type), from: str(m.from_agent), to: str(m.to_agent), stage: str(m.stage), summary: str(m.summary),
        aiGenerated: typeof m.ai_generated === 'boolean' ? m.ai_generated : undefined,
      } } };
    }
    case 'subagent_call': {
      // calls at an audit seq may be re-sent after a reconnect (floor is inclusive): the first call of a seq seen on a
      // NEW connection replaces what an older connection delivered for that seq
      let calls = s.calls;
      const callConn = { ...s.callConn };
      if (callConn[seq] !== undefined && callConn[seq] !== connId) calls = calls.filter((c) => c.seq !== seq);
      callConn[seq] = connId;
      const n = calls.filter((c) => c.seq === seq).length;
      const rec: CallRec = { key: `${seq}:${n}`, seq, agent: str(d.agent), tool: str(d.tool), inputSummary: str(d.input_summary), done: false };
      return { ...next, calls: [...calls, rec], callConn };
    }
    case 'subagent_result': {
      const idx = [...s.calls].reverse().findIndex((c) => c.seq === seq && c.tool === str(d.tool) && !c.done);
      if (idx === -1) return next;
      const at = s.calls.length - 1 - idx;
      const calls = s.calls.map((c, i) => i === at ? { ...c, done: true, confidence: numv(d.confidence), sourcesCount: numv(d.sources_count), latencyMs: numv(d.latency_ms) } : c);
      return { ...next, calls };
    }
    case 'policy_block': {
      if (s.blocks.some((b) => b.seq === seq)) return next;
      // rule id only; thresholds never cross the side boundary and are never rendered
      return { ...next, blocks: [...s.blocks, { seq, side: str(d.side), ruleId: d.rule_id == null ? null : str(d.rule_id), messageType: d.message_type == null ? null : str(d.message_type) }] };
    }
    case 'escalation_created': {
      if (s.escalations.some((e) => e.seq === seq)) return next;
      return { ...next, escalations: [...s.escalations, {
        seq, approvalId: str(d.approval_id), kind: str(d.kind), targetRole: d.target_role == null ? null : str(d.target_role),
        reasons: Array.isArray(d.reasons) ? d.reasons.map(String) : [],
      }] };
    }
    case 'approval_resolved': {
      const aid = str(d.approval_id);
      return { ...next, escalations: s.escalations.map((e) => (e.approvalId === aid ? { ...e, resolved: str(d.status) } : e)) };
    }
    case 'audit_event': {
      if (s.audit.some((a) => a.seq === seq)) return next;
      return { ...next, audit: [...s.audit, { seq, action: str(d.action), ruleId: d.rule_id == null ? null : str(d.rule_id), hash: str(d.hash) }] };
    }
    case 'run_finished':
      return { ...next, finished: str(d.outcome, 'DONE') };
    case 'error': {
      const e = { seq: seq || s.lastEventId, code: str(d.code, 'ERROR'), message: str(d.message, 'run error') };
      return { ...next, errors: [...s.errors, e] };
    }
    default:
      return next;
  }
}
