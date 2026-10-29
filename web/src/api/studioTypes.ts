export interface JsonSchemaNode {
  $ref?: string;
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  const?: unknown;
  properties?: Record<string, JsonSchemaNode>;
  required?: string[];
  additionalProperties?: boolean | JsonSchemaNode;
  patternProperties?: Record<string, JsonSchemaNode>;
  propertyNames?: JsonSchemaNode;
  items?: JsonSchemaNode;
  prefixItems?: JsonSchemaNode[];
  anyOf?: JsonSchemaNode[];
  oneOf?: JsonSchemaNode[];
  allOf?: JsonSchemaNode[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
  pattern?: string;
  $defs?: Record<string, JsonSchemaNode>;
  /** json_schema_extra hints */
  widget?: string;
  [ext: string]: unknown;
}

export interface Invariant {
  id: string;
  title?: string;
  description?: string;
  reason?: string;
}
export interface SchemaResponse {
  schema: JsonSchemaNode;
  invariants: Invariant[];
}
export type StudioConfigValue = Record<string, unknown>;
export interface DraftResponse {
  config: StudioConfigValue;
  base_version: number | null;
  updated_at: string | null;
}
export interface Issue {
  path: string;
  code: string;
  message: string;
  invariant_id?: string | null;
}
export interface ValidationReport {
  errors: Issue[];
  warnings: Issue[];
}
export interface VersionRow {
  version: number;
  content_hash: string;
  created_by: string | null;
  created_at: string;
  note: string | null;
  rollback_of: number | null;
  active: boolean;
}
export interface ProviderRow {
  name: string;
  kind: string;
  base_url: string;
  is_local: boolean;
  has_key: boolean;
}
export interface ProviderTest {
  ok: boolean;
  models: string[];
  error?: string | null;
}
export interface DiffEntry {
  path: string;
  kind?: 'added' | 'removed' | 'changed';
  before?: unknown;
  after?: unknown;
  a?: unknown;
  b?: unknown;
}
export type DiffResponse = { changes?: DiffEntry[]; diff?: DiffEntry[] } | DiffEntry[];
