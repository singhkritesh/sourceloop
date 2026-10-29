import { createContext, useContext } from 'react';
import type { Invariant, Issue, JsonSchemaNode } from '../api/studioTypes';

export interface FormCtxValue {
  root: JsonSchemaNode;
  config: Record<string, unknown>;
  readOnly: boolean;
  isAdmin: boolean;
  errorsAt: (path: string) => Issue[];
  /** issues at the path or anywhere beneath it */
  errorsUnder: (path: string) => Issue[];
  invariants: Map<string, Invariant>;
  modelsFor: (provider: string) => string[];
  discover?: (provider: string) => Promise<void>;
}
export const FormCtx = createContext<FormCtxValue | null>(null);
export function useForm(): FormCtxValue {
  const v = useContext(FormCtx);
  if (!v) throw new Error('FormCtx missing');
  return v;
}

/** Fields that sit under a code-enforced invariant (lock icon + explanation). The server remains the authority. */
const HINTS: Array<[RegExp, string]> = [
  [/^clauses\.(liability|termination|ip)\.(locked|negotiable)$/, 'INV_LOCKED_CLAUSES'],
  [/^workflow\.autonomy\.CONTRACTING$/, 'INV_CONTRACT_AUTONOMY_MAX_L2'],
  [/^workflow\.disabled_stages$/, 'INV_STAGES_FIXED'],
  [/^agents\.compliance_screen\.enabled$/, 'INV_COMPLIANCE_ENABLED'],
  [/^prompts\.[^.]+\.body$/, 'INV_WRAPPER_PRESENT'],
  [/^rules(\.|$)/, 'INV_RULES_ADD_ONLY'],
  [/^workflow\.negotiation\.max_rounds$/, 'INV_MANDATE_ENFORCEMENT'],
  [/^workflow\.standard_clauses$/, 'INV_LOCKED_CLAUSES'],
];
export function invariantFor(path: string, node?: JsonSchemaNode): string | null {
  const explicit = node && (typeof node.invariant === 'string' ? node.invariant : typeof node['x-invariant'] === 'string' ? (node['x-invariant'] as string) : null);
  if (explicit) return explicit;
  for (const [re, id] of HINTS) if (re.test(path)) return id;
  return null;
}
