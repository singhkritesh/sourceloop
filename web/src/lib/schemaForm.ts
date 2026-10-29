/** Pure JSON-schema -> form model mapping. Widget hints (json_schema_extra "widget") refine, never replace, type info;
 *  unknown widgets fall back to the type default. */
import type { JsonSchemaNode } from '../api/studioTypes';

export type FieldKind =
  | 'object' | 'map' | 'list-object' | 'list-string' | 'list-enum' | 'list-number'
  | 'enum' | 'boolean' | 'number' | 'integer' | 'decimal' | 'string' | 'json';

export interface FieldSpec {
  kind: FieldKind;
  /** normalised widget: the hint if recognised for this kind, else null */
  widget: string | null;
  rawWidget: string | null;
  title: string;
  description?: string;
  nullable: boolean;
  node: JsonSchemaNode;
  min?: number;
  max?: number;
  step?: number;
  enumValues?: string[];
  properties?: Array<[string, JsonSchemaNode]>;
  required: Set<string>;
  /** map / list item schema */
  valueNode?: JsonSchemaNode;
  /** map keys restricted to this set (fixed or suggested) */
  keyHints?: string[];
  keyFixed?: boolean;
  keyPattern?: string;
  maxLength?: number;
  adminOnly: boolean;
  forcedFor: string[];
  help?: string;
  defaultValue: unknown;
}

const REF = /^#\/\$defs\/(.+)$/;

export function deref(root: JsonSchemaNode, node: JsonSchemaNode | undefined, depth = 0): JsonSchemaNode {
  if (!node) return {};
  let cur: JsonSchemaNode = node;
  if (depth > 20) return cur;
  if (cur.$ref) {
    const m = REF.exec(cur.$ref);
    const target = m ? root.$defs?.[m[1] as string] : undefined;
    const { $ref: _r, ...siblings } = cur;
    cur = { ...deref(root, target, depth + 1), ...siblings };
  }
  if (cur.allOf && cur.allOf.length === 1) {
    const { allOf, ...rest } = cur;
    cur = { ...deref(root, allOf[0], depth + 1), ...rest };
  }
  return cur;
}

const NULL_NODE = (n: JsonSchemaNode) => n.type === 'null';

/** strip null from anyOf/oneOf; returns the merged non-null node plus nullable flag */
export function unwrapNullable(root: JsonSchemaNode, node: JsonSchemaNode): { node: JsonSchemaNode; nullable: boolean } {
  const n = deref(root, node);
  const alts = n.anyOf ?? n.oneOf;
  if (!alts) {
    if (Array.isArray(n.type) && n.type.includes('null')) {
      const t = n.type.filter((x) => x !== 'null');
      return { node: { ...n, type: t.length === 1 ? t[0] : t }, nullable: true };
    }
    return { node: n, nullable: false };
  }
  const nonNull = alts.map((a) => deref(root, a)).filter((a) => !NULL_NODE(a));
  const nullable = nonNull.length !== alts.length;
  const { anyOf: _a, oneOf: _o, ...outer } = n;
  if (nonNull.length === 1) return { node: { ...nonNull[0], ...outer }, nullable };
  const types = new Set(nonNull.map((a) => a.type));
  if (types.size === 2 && types.has('number') && types.has('string')) {
    const num = nonNull.find((a) => a.type === 'number') ?? {};
    return { node: { ...num, ...outer, type: 'decimal' as unknown as string }, nullable };
  }
  if (nonNull.every((a) => a.const !== undefined || a.enum)) {
    const vals = nonNull.flatMap((a) => (a.enum ?? [a.const]) as unknown[]);
    return { node: { ...outer, type: 'string', enum: vals }, nullable };
  }
  return { node: { ...outer, type: 'json-union' as unknown as string }, nullable };
}

const humanize = (k: string) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
export const titleOf = (node: JsonSchemaNode, key?: string): string => (key ? humanize(key) : node.title ?? '');

function enumOf(root: JsonSchemaNode, node: JsonSchemaNode): string[] | undefined {
  if (node.enum) return node.enum.map(String);
  if (node.const !== undefined) return [String(node.const)];
  const pn = node.propertyNames ? deref(root, node.propertyNames) : undefined;
  if (pn?.enum) return pn.enum.map(String);
  return undefined;
}

export function describe(root: JsonSchemaNode, rawNode: JsonSchemaNode, key?: string): FieldSpec {
  const { node, nullable } = unwrapNullable(root, rawNode);
  const rawWidget = typeof node.widget === 'string' ? node.widget : typeof rawNode.widget === 'string' ? rawNode.widget : null;
  const base = {
    nullable,
    node,
    rawWidget,
    title: titleOf(node, key),
    description: node.description,
    required: new Set(node.required ?? []),
    adminOnly: node.admin_only === true,
    forcedFor: Array.isArray(node.forced_for) ? (node.forced_for as string[]) : [],
    help: typeof node.help === 'string' ? node.help : undefined,
    defaultValue: node.default,
    maxLength: node.maxLength,
    min: node.minimum ?? node.exclusiveMinimum ?? (typeof node.min === 'number' ? node.min : undefined),
    max: node.maximum ?? node.exclusiveMaximum ?? (typeof node.max === 'number' ? node.max : undefined),
  };
  const t = Array.isArray(node.type) ? node.type[0] : node.type;
  const w = rawWidget;

  // enums first (select) regardless of declared type
  if (node.enum || node.const !== undefined) {
    return { ...base, kind: 'enum', widget: w === 'radio' ? 'radio' : 'select', enumValues: enumOf(root, node) };
  }
  switch (t) {
    case 'boolean':
      return { ...base, kind: 'boolean', widget: 'switch' };
    case 'integer':
    case 'number':
    case 'decimal': {
      const span = base.min !== undefined && base.max !== undefined ? base.max - base.min : Infinity;
      const slider = w === 'slider' || w === 'autonomy';
      return {
        ...base,
        kind: t === 'decimal' ? 'decimal' : t === 'integer' ? 'integer' : 'number',
        widget: slider ? 'slider' : 'number',
        step: t === 'integer' ? 1 : span <= 1 ? 0.01 : span <= 100 ? 0.1 : 1,
      };
    }
    case 'string': {
      const longText = w === 'textarea' || w === 'prompt_body' || (node.maxLength !== undefined && node.maxLength > 300);
      return {
        ...base,
        kind: 'string',
        widget: longText ? 'textarea'
          : w === 'url' || w === 'secret_ref' || w === 'model_picker' || w === 'provider_picker' || w === 'prompt_ref' ? w : 'text',
      };
    }
    case 'array': {
      const item = unwrapNullable(root, node.items ?? {}).node;
      const it = Array.isArray(item.type) ? item.type[0] : item.type;
      if (item.enum) return { ...base, kind: 'list-enum', widget: 'checkbox_group', valueNode: item, enumValues: item.enum.map(String) };
      if (it === 'object') return { ...base, kind: 'list-object', widget: 'list', valueNode: node.items };
      if (it === 'integer' || it === 'number') return { ...base, kind: 'list-number', widget: 'tags', valueNode: item };
      if (it === 'string') return { ...base, kind: 'list-string', widget: 'tags', valueNode: item };
      return { ...base, kind: 'json', widget: null };
    }
    case 'object': {
      if (node.properties) {
        return { ...base, kind: 'object', widget: null, properties: Object.entries(node.properties) };
      }
      const pp = node.patternProperties ? Object.entries(node.patternProperties)[0] : undefined;
      const ap = typeof node.additionalProperties === 'object' ? node.additionalProperties : undefined;
      const valueNode = ap ?? pp?.[1];
      if (valueNode) {
        const hints = Array.isArray(node.keys) ? (node.keys as string[]) : keyHintsFromRoot(root, w) ?? enumOf(root, node);
        return {
          ...base, kind: 'map', widget: w, valueNode, keyHints: hints,
          keyFixed: w === 'role_models' || w === 'agents' || (Array.isArray(node.keys) && w !== 'agents' && w !== 'prompts'),
          keyPattern: pp?.[0],
        };
      }
      return { ...base, kind: 'json', widget: null };
    }
    default:
      return { ...base, kind: 'json', widget: null };
  }
}

function keyHintsFromRoot(root: JsonSchemaNode, w: string | null): string[] | undefined {
  if (w === 'prompts' && Array.isArray(root['x-prompt-agents'])) return root['x-prompt-agents'] as string[];
  if (w === 'agents' && Array.isArray(root['x-agent-keys'])) return root['x-agent-keys'] as string[];
  return undefined;
}

/** value for a freshly added list item / map entry / un-nulled optional */
export function defaultFor(root: JsonSchemaNode, rawNode: JsonSchemaNode): unknown {
  const spec = describe(root, rawNode);
  if (spec.defaultValue !== undefined && spec.defaultValue !== null) return structuredCloneSafe(spec.defaultValue);
  switch (spec.kind) {
    case 'boolean': return false;
    case 'number': case 'integer': return spec.min ?? 0;
    case 'decimal': return String(spec.min ?? 0);
    case 'enum': return spec.enumValues?.[0] ?? '';
    case 'string': return '';
    case 'list-object': case 'list-string': case 'list-enum': case 'list-number': return [];
    case 'map': return {};
    case 'object': {
      const out: Record<string, unknown> = {};
      for (const [k, n] of spec.properties ?? []) {
        const s = describe(root, n, k);
        if (spec.required.has(k) || (s.defaultValue !== undefined && s.defaultValue !== null)) out[k] = defaultFor(root, n);
      }
      return out;
    }
    default: return null;
  }
}

function structuredCloneSafe<T>(v: T): T {
  return v === undefined ? v : (JSON.parse(JSON.stringify(v)) as T);
}

/** Top-level sections of the form for nav (skips schema_version / non-config scalars). */
export interface SectionSpec { key: string; title: string; spec: FieldSpec }
export function sectionsOf(root: JsonSchemaNode): SectionSpec[] {
  const props = root.properties ?? {};
  return Object.entries(props)
    .filter(([k]) => k !== 'schema_version')
    .map(([k, n]) => ({ key: k, title: humanize(k), spec: describe(root, n, k) }));
}
