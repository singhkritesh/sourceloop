/** Per-kind EDIT shapes for POST /approvals/{id}/resolve (status EDITED). Other kinds have no edit. */
export type EditKind = 'COMPLIANCE_FLAG' | 'TECHNICAL_QUAL' | 'AWARD' | 'CLAUSE';

export const EDITABLE: readonly string[] = ['COMPLIANCE_FLAG', 'TECHNICAL_QUAL', 'AWARD', 'CLAUSE'];
export const isEditable = (kind: string): kind is EditKind => EDITABLE.includes(kind);

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** Supplier ids the server will accept, taken from the approval's own evidence only. */
export function editOptions(kind: string, evidence: unknown): string[] {
  const ev = obj(evidence);
  let ids: unknown[] = [];
  if (kind === 'COMPLIANCE_FLAG') ids = Object.keys(obj(ev.flagged));
  else if (kind === 'TECHNICAL_QUAL') ids = Array.isArray(ev.candidates) ? ev.candidates : [];
  else if (kind === 'AWARD') ids = Array.isArray(ev.ranking) ? ev.ranking.map((r) => obj(r).supplier) : [];
  return [...new Set(ids.filter((x): x is string => typeof x === 'string' && x.length > 0))];
}

export interface EditInput {
  selected?: string[];
  supplier?: string | null;
  acceptChange?: boolean | null;
}

/** Build the exact edit body for a kind, or null when the input is not submittable. */
export function buildEdit(kind: string, input: EditInput, evidence: unknown): Record<string, unknown> | null {
  if (!isEditable(kind)) return null;
  const allowed = new Set(editOptions(kind, evidence));
  switch (kind) {
    case 'COMPLIANCE_FLAG': {
      const include = [...new Set(input.selected ?? [])].filter((s) => allowed.has(s));
      return include.length ? { include } : null;
    }
    case 'TECHNICAL_QUAL': {
      const fail = [...new Set(input.selected ?? [])].filter((s) => allowed.has(s));
      return fail.length ? { fail } : null;
    }
    case 'AWARD':
      return input.supplier && allowed.has(input.supplier) ? { supplier: input.supplier } : null;
    case 'CLAUSE':
      return typeof input.acceptChange === 'boolean' ? { accept_change: input.acceptChange } : null;
  }
}
