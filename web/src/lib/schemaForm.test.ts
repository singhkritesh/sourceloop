import { describe, expect, it } from 'vitest';
import fixture from '../api/mock/fixture.json';
import type { JsonSchemaNode } from '../api/studioTypes';
import { defaultFor, deref, describe as spec, sectionsOf } from './schemaForm';
import { deleteIn, getIn, normalizeErrorPath, setIn } from './paths';

const root = fixture.schema as unknown as JsonSchemaNode;
const prop = (o: JsonSchemaNode, k: string) => deref(root, o).properties?.[k] as JsonSchemaNode;

describe('schema -> form mapping (real StudioConfig schema)', () => {
  it('lists the config sections, skipping schema_version', () => {
    expect(sectionsOf(root).map((s) => s.key)).toEqual(['providers', 'llm', 'agents', 'prompts', 'workflow', 'rules', 'clauses', 'data_sources', 'scenarios']);
  });
  it('maps scalars with widget hints', () => {
    const neg = prop(prop(root.properties?.workflow as JsonSchemaNode, 'negotiation'), 'max_rounds');
    const s = spec(root, neg, 'max_rounds');
    expect(s).toMatchObject({ kind: 'integer', widget: 'number', min: 1, max: 8, title: 'Max rounds' });
  });
  it('maps Decimal (anyOf number|string) to decimal', () => {
    const n = prop(prop(root.properties?.workflow as JsonSchemaNode, 'negotiation'), 'min_step_pct');
    expect(spec(root, n, 'min_step_pct').kind).toBe('decimal');
  });
  it('maps enum literals to select and bool to switch', () => {
    const llm = deref(root, root.properties?.llm);
    expect(spec(root, llm.properties?.mode as JsonSchemaNode, 'mode')).toMatchObject({ kind: 'enum', enumValues: ['live', 'replay', 'record'] });
    expect(spec(root, llm.properties?.replay_fallback_live as JsonSchemaNode)).toMatchObject({ kind: 'boolean', widget: 'switch' });
  });
  it('treats nullable $ref object (model_override) as nullable object', () => {
    const agent = deref(root, (deref(root, root.properties?.agents).additionalProperties as JsonSchemaNode));
    const s = spec(root, agent.properties?.model_override as JsonSchemaNode, 'model_override');
    expect(s).toMatchObject({ kind: 'object', nullable: true, rawWidget: 'model_picker' });
    expect(s.properties?.map(([k]) => k)).toContain('model');
  });
  it('maps dict fields (additionalProperties and patternProperties) to maps with key hints', () => {
    const agents = spec(root, root.properties?.agents as JsonSchemaNode, 'agents');
    expect(agents.kind).toBe('map');
    expect(agents.keyHints).toContain('buyer_lead');
    const providers = spec(root, root.properties?.providers as JsonSchemaNode, 'providers');
    expect(providers.kind).toBe('map');
    expect(providers.keyPattern).toContain('a-z');
    const roles = spec(root, deref(root, root.properties?.llm).properties?.roles as JsonSchemaNode, 'roles');
    expect(roles).toMatchObject({ kind: 'map', keyFixed: true, keyHints: ['fast', 'chat'] });
  });
  it('maps arrays: enum -> checkbox group, objects -> list, strings -> tags', () => {
    const wf = deref(root, root.properties?.workflow);
    expect(spec(root, wf.properties?.disabled_stages as JsonSchemaNode).kind).toBe('list-enum');
    expect(spec(root, root.properties?.rules as JsonSchemaNode).kind).toBe('list-object');
    expect(spec(root, wf.properties?.standard_clauses as JsonSchemaNode).kind).toBe('list-string');
  });
  it('maps prompt body to textarea', () => {
    const prompts = deref(root, root.properties?.prompts);
    const def = deref(root, prompts.additionalProperties as JsonSchemaNode);
    expect(spec(root, def.properties?.body as JsonSchemaNode, 'body')).toMatchObject({ kind: 'string', widget: 'textarea' });
  });
  it('falls back sensibly for unknown widgets / types', () => {
    expect(spec(root, { type: 'string', widget: 'fancy_unknown' })).toMatchObject({ kind: 'string', widget: 'text' });
    expect(spec(root, { type: 'integer', widget: 'fancy_unknown' })).toMatchObject({ kind: 'integer', widget: 'number' });
    expect(spec(root, { widget: 'mystery' }).kind).toBe('json');
    expect(spec(root, { type: 'integer', minimum: 1, maximum: 4, widget: 'slider' }).widget).toBe('slider');
  });
  it('builds defaults for new entries', () => {
    const agents = spec(root, root.properties?.agents as JsonSchemaNode);
    expect(defaultFor(root, agents.valueNode as JsonSchemaNode)).toMatchObject({ enabled: true, timeout_s: 20 });
  });
});

describe('paths', () => {
  it('get/set/delete are immutable', () => {
    const o = { a: { b: [1, 2] } };
    const o2 = setIn(o, ['a', 'b', 1], 9);
    expect(o.a.b[1]).toBe(2);
    expect(getIn(o2, ['a', 'b', 1])).toBe(9);
    expect(deleteIn(o2, ['a', 'b', 0])).toEqual({ a: { b: [9] } });
    expect(setIn({}, ['x', 0, 'y'], 1)).toEqual({ x: [{ y: 1 }] });
  });
  it('normalises error paths', () => {
    expect(normalizeErrorPath('llm.roles.fast.model')).toBe('llm.roles.fast.model');
    expect(normalizeErrorPath('/llm/roles/fast')).toBe('llm.roles.fast');
    expect(normalizeErrorPath('body.config.rules[0].value')).toBe('rules.0.value');
    expect(normalizeErrorPath("clauses['liability'].locked")).toBe('clauses.liability.locked');
  });
});
