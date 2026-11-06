export interface SummaryStep { round: number; by: 'supplier' | 'buyer'; unit_price: string; lead_time_weeks: number; text: string; why?: string }
export interface SummaryThread {
  supplier_id: string; name: string; rounds: number; steps: SummaryStep[]; conduct: { round: number | null; by: string; level: string | null; text: string }[]; leverage: { text: string }[];
  opening_quote?: string; latest_quote?: string; latest_counter?: string; supplier_moved_pct?: string | null;
}
export interface NegotiationSummary {
  side: 'BUYER' | 'SUPPLIER'; hash: string; ack_required: boolean; how_to_read: string; assumptions: string[];
  threads: SummaryThread[];
  deal: { supplier: string | null; name: string | null; unit_price?: string; payment_days: number | null; clauses: string[]; deviations: string[]; signed: boolean } | null;
  payment?: { account_ending: string | null; checks: { check: string; passed: boolean }[]; passed: boolean; external_verification: string | null } | null;
  your_limits: { target_unit_price?: string; highest_unit_price?: string } | null;
  record: { events: number; head_seq: number | null; head_hash: string | null };
}
/** Decisions where a company is committed or money moves: the person reads and confirms the negotiation summary first. */
export const SUMMARY_KINDS = ['AWARD', 'SIGNATURE', 'SUPPLIER_ACCEPT', 'HOLD_RELEASE'];
