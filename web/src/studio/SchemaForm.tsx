import { useMemo, useState, type ReactNode } from 'react';
import type { JsonSchemaNode } from '../api/studioTypes';
import { Icon } from '../components/Icon';
import { Switch } from '../components/ui';
import { defaultFor, describe, type FieldSpec } from '../lib/schemaForm';
import { pathKey, type Path } from '../lib/paths';
import { invariantFor, useForm } from './formCtx';

type Val = unknown;
interface FieldProps {
  spec: FieldSpec;
  path: Path;
  value: Val;
  onChange: (v: Val) => void;
  parent?: Record<string, unknown>;
  entryKey?: string;
  /** hide label (caller already shows it) */
  bare?: boolean;
  depth?: number;
}

/** Entry point: renders a whole (sub)schema at a path. */
export function SchemaForm({ node, path, value, onChange, title }: { node: JsonSchemaNode; path: Path; value: Val; onChange: (v: Val) => void; title?: string }) {
  const { root } = useForm();
  const spec = useMemo(() => describe(root, node, path[path.length - 1] === undefined ? undefined : String(path[path.length - 1])), [root, node, path]);
  return <Field spec={title ? { ...spec, title } : spec} path={path} value={value} onChange={onChange} depth={0} />;
}

function Wrapper({ spec, path, children, bare, full }: { spec: FieldSpec; path: Path; children: ReactNode; bare?: boolean; full?: boolean }) {
  const ctx = useForm();
  const key = pathKey(path);
  const own = ctx.errorsAt(key);
  const invId = invariantFor(key, spec.node);
  const inv = invId ? ctx.invariants.get(invId) : undefined;
  const id = `f-${key}`;
  return (
    <div className={`field ${full ? 'full' : ''}`} data-path={key}>
      {!bare && path.length > 1 && (
        <label htmlFor={id} className="row" style={{ gap: 6 }}>
          {spec.title}
          {spec.adminOnly && <span className="badge info" title="Only ADMIN can change this">admin</span>}
          {invId && <span className="lock" title={`${invId}: ${inv?.title ?? inv?.description ?? 'protected by an invariant'}`}><Icon name="lock" /><span className="sr-only">Protected by invariant {invId}</span></span>}
        </label>
      )}
      {children}
      {(spec.help ?? spec.description) && !bare && path.length > 1 && <span className="hint">{spec.help ?? spec.description}</span>}
      {own.map((e, i) => <span key={i} className="fld-err" role="alert">{e.message}{e.invariant_id ? ` (${e.invariant_id})` : ''}</span>)}
    </div>
  );
}

const asNum = (raw: string, spec: FieldSpec): unknown => {
  if (raw.trim() === '') return undefined;
  if (spec.kind === 'decimal') return raw.trim(); // Decimal travels as a string: no float rounding
  const n = Number(raw);
  return Number.isFinite(n) ? (spec.kind === 'integer' ? Math.trunc(n) : n) : undefined;
};

export function Field(props: FieldProps) {
  const { spec, path, value, onChange } = props;
  const ctx = useForm();
  const key = pathKey(path);
  const id = `f-${key}`;
  const ro = ctx.readOnly || (spec.adminOnly && !ctx.isAdmin);
  const invalid = ctx.errorsAt(key).some((e) => e.path === key);

  // nullable object / scalar: explicit unset state
  if (spec.nullable && (value === null || value === undefined) && spec.kind !== 'boolean') {
    return (
      <Wrapper spec={spec} path={path} full={spec.kind === 'object'}>
        <div className="row"><span className="muted">Not set (uses the default)</span>
          <button type="button" className="btn sm" disabled={ro} onClick={() => onChange(defaultFor(ctx.root, { ...spec.node }) ?? {})}>Set…</button></div>
      </Wrapper>
    );
  }
  const clear = spec.nullable && !ro ? <button type="button" className="btn sm ghost" onClick={() => onChange(null)}>Clear</button> : null;

  switch (spec.kind) {
    case 'boolean': {
      const forced = !!props.entryKey && spec.forcedFor.includes(props.entryKey);
      return (
        <Wrapper spec={spec} path={path}>
          <div className="row"><Switch label={spec.title} checked={forced ? true : value === true} disabled={ro || forced} onChange={onChange} />
            <span className="hint">{forced ? 'Forced on by an invariant' : value === true ? 'On' : 'Off'}</span></div>
        </Wrapper>
      );
    }
    case 'enum': {
      const vals = spec.enumValues ?? [];
      return (
        <Wrapper spec={spec} path={path}>
          <select id={id} className="select" disabled={ro} aria-invalid={invalid} value={value == null ? '' : String(value)} onChange={(e) => onChange(e.target.value)}>
            {value == null && <option value="" disabled>Select…</option>}
            {vals.map((v) => <option key={v} value={v}>{v}</option>)}
            {value != null && !vals.includes(String(value)) && <option value={String(value)}>{String(value)} (unknown)</option>}
          </select>
        </Wrapper>
      );
    }
    case 'number':
    case 'integer':
    case 'decimal': {
      const num = value === undefined || value === null ? '' : String(value);
      return (
        <Wrapper spec={spec} path={path}>
          <div className="row">
            {spec.widget === 'slider' && spec.min !== undefined && spec.max !== undefined && (
              <input type="range" aria-label={`${spec.title} slider`} min={spec.min} max={spec.max} step={spec.step ?? 1} disabled={ro}
                value={Number(num) || spec.min} onChange={(e) => onChange(asNum(e.target.value, spec))} />
            )}
            <input id={id} className="input num" type="number" inputMode="decimal" disabled={ro} aria-invalid={invalid}
              min={spec.min} max={spec.max} step={spec.kind === 'integer' ? 1 : 'any'} value={num}
              style={spec.widget === 'slider' ? { width: 90 } : undefined}
              onChange={(e) => onChange(asNum(e.target.value, spec))} />
            {clear}
          </div>
          {(spec.min !== undefined || spec.max !== undefined) && <span className="hint">{spec.min ?? '…'} to {spec.max ?? '…'}</span>}
        </Wrapper>
      );
    }
    case 'string':
      return <StringField {...props} ro={ro} invalid={invalid} id={id} clear={clear} />;
    case 'list-string':
    case 'list-number':
      return (
        <Wrapper spec={spec} path={path} full>
          <Tags id={id} value={Array.isArray(value) ? (value as unknown[]).map(String) : []} disabled={ro}
            onChange={(v) => onChange(spec.kind === 'list-number' ? v.map(Number).filter(Number.isFinite) : v)} placeholder={spec.rawWidget === 'host_list' ? 'add host, press Enter' : 'add, press Enter'} />
        </Wrapper>
      );
    case 'list-enum': {
      const cur = new Set(Array.isArray(value) ? (value as unknown[]).map(String) : []);
      return (
        <Wrapper spec={spec} path={path} full>
          <div className="row wrap" role="group" aria-label={spec.title}>
            {(spec.enumValues ?? []).map((v) => (
              <label key={v} className="row" style={{ gap: 4 }}>
                <input type="checkbox" disabled={ro} checked={cur.has(v)} onChange={(e) => { const n = new Set(cur); if (e.target.checked) n.add(v); else n.delete(v); onChange([...n]); }} /> {v}
              </label>
            ))}
          </div>
        </Wrapper>
      );
    }
    case 'object':
      return <ObjectField {...props} clear={clear} />;
    case 'map':
      return <MapField {...props} ro={ro} />;
    case 'list-object':
      return <ListObjectField {...props} ro={ro} />;
    default:
      return <JsonField {...props} ro={ro} />;
  }
}

function StringField({ spec, path, value, onChange, parent, ro, invalid, id, clear }: FieldProps & { ro: boolean; invalid: boolean; id: string; clear: ReactNode }) {
  const ctx = useForm();
  const v = value == null ? '' : String(value);
  const w = spec.widget;
  if (w === 'textarea') {
    return (
      <Wrapper spec={spec} path={path} full>
        <textarea id={id} className="textarea" disabled={ro} aria-invalid={invalid} rows={Math.min(14, Math.max(4, v.split('\n').length + 1))} maxLength={spec.maxLength} value={v} onChange={(e) => onChange(e.target.value)} />
        {spec.maxLength && <span className="hint num">{v.length}/{spec.maxLength}</span>}
      </Wrapper>
    );
  }
  if (w === 'provider_picker') {
    const names = Object.keys((ctx.config.providers as Record<string, unknown> | undefined) ?? {});
    return (
      <Wrapper spec={spec} path={path}>
        <select id={id} className="select" disabled={ro} aria-invalid={invalid} value={v} onChange={(e) => onChange(e.target.value)}>
          <option value="" disabled>Select provider…</option>
          {names.map((n) => <option key={n} value={n}>{n}</option>)}
          {v && !names.includes(v) && <option value={v}>{v} (not configured)</option>}
        </select>
      </Wrapper>
    );
  }
  if (w === 'prompt_ref') {
    const names = Object.keys((ctx.config.prompts as Record<string, unknown> | undefined) ?? {});
    return (
      <Wrapper spec={spec} path={path}>
        <div className="row"><select id={id} className="select" disabled={ro} value={v} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">(default prompt)</option>{names.map((n) => <option key={n} value={n}>{n}</option>)}</select>{clear}</div>
      </Wrapper>
    );
  }
  if (w === 'model_picker') {
    const provider = String(parent?.provider ?? '');
    const models = ctx.modelsFor(provider);
    return (
      <Wrapper spec={spec} path={path}>
        <div className="row">
          <input id={id} className="input" list={`${id}-dl`} disabled={ro} aria-invalid={invalid} value={v} maxLength={spec.maxLength} placeholder={models.length ? 'pick or type a model id' : 'type a model id (or discover)'} onChange={(e) => onChange(e.target.value)} />
          <datalist id={`${id}-dl`}>{models.map((m) => <option key={m} value={m} />)}</datalist>
          {ctx.discover && provider && <button type="button" className="btn sm" disabled={ro} onClick={() => void ctx.discover?.(provider)}>Discover</button>}
        </div>
        {models.length > 0 && <span className="hint">{models.length} models discovered on {provider}</span>}
      </Wrapper>
    );
  }
  return (
    <Wrapper spec={spec} path={path}>
      <div className="row"><input id={id} className="input" type={w === 'url' ? 'url' : 'text'} disabled={ro} aria-invalid={invalid} maxLength={spec.maxLength} value={v}
        autoComplete="off" spellCheck={false} onChange={(e) => onChange(e.target.value)} />{clear}</div>
    </Wrapper>
  );
}

function ObjectField({ spec, path, value, onChange, clear, depth = 1, entryKey }: FieldProps & { clear: ReactNode }) {
  const ctx = useForm();
  const obj = (value && typeof value === 'object' && !Array.isArray(value) ? value : {}) as Record<string, unknown>;
  const set = (k: string, v: unknown) => {
    const next = { ...obj };
    if (v === undefined) delete next[k]; else next[k] = v;
    onChange(next);
  };
  const kids = (spec.properties ?? []).filter(([k]) => k !== 'schema_version');
  const body = (
    <div className="fgrid">
      {kids.map(([k, n]) => {
        const s = describe(ctx.root, n, k);
        const big = s.kind === 'object' || s.kind === 'map' || s.kind === 'list-object' || s.kind === 'json' || s.widget === 'textarea';
        return (
          <div key={k} className={big ? 'full' : undefined}>
            <Field spec={s} path={[...path, k]} value={obj[k]} onChange={(v) => set(k, v)} parent={obj} entryKey={entryKey} depth={depth + 1} />
          </div>
        );
      })}
    </div>
  );
  if (depth === 0) return body;
  return (
    <fieldset className={`fs ${depth > 1 ? 'nested' : ''}`} data-path={pathKey(path)}>
      <legend>{spec.title}{invariantFor(pathKey(path), spec.node) && <span className="lock"><Icon name="lock" /></span>}</legend>
      {spec.description && <p className="hint" style={{ marginBottom: 8 }}>{spec.description}</p>}
      {ctx.errorsAt(pathKey(path)).map((e, i) => <p key={i} className="fld-err" role="alert">{e.message}{e.invariant_id ? ` (${e.invariant_id})` : ''}</p>)}
      {body}
      {clear && <div style={{ marginTop: 8 }}>{clear}</div>}
    </fieldset>
  );
}

function MapField({ spec, path, value, onChange, ro, depth = 1 }: FieldProps & { ro: boolean }) {
  const ctx = useForm();
  const obj = (value && typeof value === 'object' && !Array.isArray(value) ? value : {}) as Record<string, unknown>;
  const [newKey, setNewKey] = useState('');
  const vs = useMemo(() => (spec.valueNode ? describe(ctx.root, spec.valueNode) : null), [ctx.root, spec.valueNode]);
  if (!vs || !spec.valueNode) return <JsonField spec={spec} path={path} value={value} onChange={onChange} ro={ro} />;
  const keys = Object.keys(obj);
  const hints = spec.keyHints ?? [];
  const missing = hints.filter((h) => !(h in obj));
  const complex = vs.kind === 'object' || vs.kind === 'map' || vs.kind === 'list-object' || vs.kind === 'json';
  const keyOk = newKey !== '' && !(newKey in obj) && (!spec.keyPattern || new RegExp(spec.keyPattern).test(newKey));
  const add = (k: string) => { onChange({ ...obj, [k]: defaultFor(ctx.root, spec.valueNode as JsonSchemaNode) }); setNewKey(''); };
  const remove = (k: string) => { const n = { ...obj }; delete n[k]; onChange(n); };
  const summary = (v: unknown): ReactNode => {
    if (!v || typeof v !== 'object') return null;
    const o = v as Record<string, unknown>;
    return <>
      {'enabled' in o && <span className={`badge ${o.enabled ? 'ok' : ''}`}>{o.enabled ? 'enabled' : 'disabled'}</span>}
      {o.model_override != null && <span className="badge info">model override</span>}
      {typeof o.name === 'string' && <span className="muted">{o.name}</span>}
      {typeof o.title === 'string' && <span className="muted">{o.title}</span>}
      {'kind' in o && typeof o.kind === 'string' && <span className="muted">{o.kind}</span>}
      {'locked' in o && o.locked === true && <span className="badge warn">locked</span>}
      {typeof o.model === 'string' && <span className="muted mono">{o.model}</span>}
    </>;
  };
  const shown = spec.keyFixed ? [...new Set([...hints.filter((h) => h in obj), ...keys])] : keys;
  return (
    <Wrapper spec={spec} path={path} full>
      {shown.length === 0 && <p className="hint">No entries yet.</p>}
      {shown.map((k) => {
        const p = [...path, k];
        if (!complex) {
          return (
            <div key={k} className="row" style={{ marginBottom: 6 }}>
              <code style={{ minWidth: 110 }}>{k}</code>
              <div className="grow"><Field spec={{ ...vs, title: k }} path={p} value={obj[k]} bare onChange={(v) => onChange({ ...obj, [k]: v })} entryKey={k} depth={depth + 1} /></div>
              {!ro && <button type="button" className="btn ghost icon sm" aria-label={`Remove ${k}`} onClick={() => remove(k)}><Icon name="trash" /></button>}
            </div>
          );
        }
        return (
          <details key={k} className="entry" open={keys.length <= 2 || ctx.errorsUnder(pathKey(p)).length > 0} data-path={pathKey(p)}>
            <summary><code>{k}</code>{summary(obj[k])}{ctx.errorsUnder(pathKey(p)).length > 0 && <span className="badge bad">{ctx.errorsUnder(pathKey(p)).length} issue(s)</span>}<span className="spacer" />
              {!ro && (!spec.keyFixed) && <button type="button" className="btn ghost icon sm" aria-label={`Remove ${k}`} onClick={(e) => { e.preventDefault(); remove(k); }}><Icon name="trash" /></button>}</summary>
            <div className="entry-body">
              {ctx.errorsAt(pathKey(p)).map((e, i) => <p key={i} className="fld-err" role="alert">{e.message}{e.invariant_id ? ` (${e.invariant_id})` : ''}</p>)}
              <Field spec={{ ...vs, title: k }} path={p} value={obj[k]} onChange={(v) => onChange({ ...obj, [k]: v })} entryKey={k} depth={0} /></div>
          </details>
        );
      })}
      {!ro && (
        <div className="row wrap" style={{ marginTop: 6 }}>
          {missing.length > 0 && (
            <select className="select" style={{ width: 200 }} aria-label={`Add ${spec.title} entry`} value="" onChange={(e) => e.target.value && add(e.target.value)}>
              <option value="">Add {spec.keyFixed ? 'entry' : 'known'}…</option>{missing.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
          {!spec.keyFixed && (
            <>
              <input className="input" style={{ width: 180 }} aria-label="New key" placeholder="new key" value={newKey} onChange={(e) => setNewKey(e.target.value)} aria-invalid={newKey !== '' && !keyOk} />
              <button type="button" className="btn sm" disabled={!keyOk} onClick={() => add(newKey)}><Icon name="plus" />Add</button>
            </>
          )}
        </div>
      )}
    </Wrapper>
  );
}

function ListObjectField({ spec, path, value, onChange, ro }: FieldProps & { ro: boolean }) {
  const ctx = useForm();
  const list = Array.isArray(value) ? (value as unknown[]) : [];
  const vs = useMemo(() => (spec.valueNode ? describe(ctx.root, spec.valueNode) : null), [ctx.root, spec.valueNode]);
  if (!vs || !spec.valueNode) return <JsonField spec={spec} path={path} value={value} onChange={onChange} ro={ro} />;
  const move = (i: number, d: number) => { const n = [...list]; const j = i + d; if (j < 0 || j >= n.length) return; [n[i], n[j]] = [n[j], n[i]] as [unknown, unknown]; onChange(n); };
  return (
    <Wrapper spec={spec} path={path} full>
      {list.length === 0 && <p className="hint">Empty.</p>}
      {list.map((it, i) => {
        const o = (it ?? {}) as Record<string, unknown>;
        return (
          <details key={i} className="entry" open data-path={pathKey([...path, i])}>
            <summary><span>#{i + 1}</span>{typeof o.id === 'string' && <code>{o.id}</code>}{typeof o.name === 'string' && <span className="muted">{o.name}</span>}<span className="spacer" />
              {!ro && <>
                <button type="button" className="btn ghost icon sm" aria-label="Move up" onClick={(e) => { e.preventDefault(); move(i, -1); }}>↑</button>
                <button type="button" className="btn ghost icon sm" aria-label="Move down" onClick={(e) => { e.preventDefault(); move(i, 1); }}>↓</button>
                <button type="button" className="btn ghost icon sm" aria-label={`Remove item ${i + 1}`} onClick={(e) => { e.preventDefault(); onChange(list.filter((_, j) => j !== i)); }}><Icon name="trash" /></button></>}
            </summary>
            <div className="entry-body"><Field spec={{ ...vs, title: `Item ${i + 1}` }} path={[...path, i]} value={it} onChange={(v) => onChange(list.map((x, j) => (j === i ? v : x)))} depth={0} /></div>
          </details>
        );
      })}
      {!ro && <button type="button" className="btn sm" style={{ marginTop: 6 }} onClick={() => onChange([...list, defaultFor(ctx.root, spec.valueNode as JsonSchemaNode)])}><Icon name="plus" />Add item</button>}
    </Wrapper>
  );
}

function JsonField({ spec, path, value, onChange, ro }: Pick<FieldProps, 'spec' | 'path' | 'value' | 'onChange'> & { ro: boolean }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [bad, setBad] = useState(false);
  return (
    <Wrapper spec={spec} path={path} full>
      <textarea id={`f-${pathKey(path)}`} className="textarea" disabled={ro} aria-invalid={bad} value={text}
        onChange={(e) => { setText(e.target.value); try { onChange(JSON.parse(e.target.value)); setBad(false); } catch { setBad(true); } }} />
      {bad && <span className="err-text">Invalid JSON (not applied yet)</span>}
      <span className="hint">No dedicated widget for this field type; edit as JSON.</span>
    </Wrapper>
  );
}

export function Tags({ id, value, onChange, disabled, placeholder }: { id?: string; value: string[]; onChange: (v: string[]) => void; disabled?: boolean; placeholder?: string }) {
  const [draft, setDraft] = useState('');
  const commit = () => { const t = draft.trim(); if (t && !value.includes(t)) onChange([...value, t]); setDraft(''); };
  return (
    <div className="tags">
      {value.map((t) => (
        <span className="tag" key={t}>{t}{!disabled && <button type="button" aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>×</button>}</span>
      ))}
      {!disabled && <input id={id} value={draft} placeholder={placeholder} aria-label="Add value" onChange={(e) => setDraft(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commit(); } else if (e.key === 'Backspace' && !draft && value.length) onChange(value.slice(0, -1)); }} />}
    </div>
  );
}
