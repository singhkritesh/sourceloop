import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { admin, org } from '../api/endpoints';
import { ApiError, errorMessage } from '../api/http';
import type { OrgUser, OverrideSpec, OverridesView, Role } from '../api/types';
import { Badge, Dialog, Empty, ErrorBox, fmtTime, SkeletonList } from '../components/ui';
import { roleTitle, toolText } from '../lib/friendly';
import { can, useAuth } from '../state/auth';
import { toast } from '../state/ui';

type Tab = 'team' | 'agents' | 'orgs';

function Secret({ title, email, password, onClose }: { title: string; email: string; password: string; onClose: () => void }) {
  return (
    <Dialog title={title} onClose={onClose} footer={<button className="btn primary" onClick={onClose}>I have saved it</button>}>
      <div className="stack">
        <p>Give this one-time password to <b>{email}</b>. It is shown <b>only now</b> and cannot be looked up again; they should change it after signing in. If it is lost, reset it.</p>
        <div className="secret"><code aria-label="One-time password">{password}</code>
          <button className="btn sm" onClick={() => { void navigator.clipboard?.writeText(password).then(() => toast.ok('Copied'), () => toast.warn('Copy failed: select it by hand')); }}>Copy</button></div>
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ team
function Team({ orgId, editable }: { orgId?: string; editable: boolean }) {
  const qc = useQueryClient();
  const meId = useAuth((s) => s.me?.id);
  const info = useQuery({ queryKey: ['org', orgId], queryFn: () => org.get(orgId) });
  const users = useQuery({ queryKey: ['org-users', orgId], queryFn: () => org.users(orgId) });
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('BUYER');
  const [secret, setSecret] = useState<{ title: string; email: string; password: string } | null>(null);
  useEffect(() => { const first = info.data?.roles[0]; if (first && !info.data?.roles.includes(role)) setRole(first); }, [info.data, role]);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['org-users', orgId] }); void qc.invalidateQueries({ queryKey: ['org', orgId] }); void qc.invalidateQueries({ queryKey: ['orgs'] }); };
  const add = useMutation({
    mutationFn: () => org.createUser({ email: email.trim(), role }, orgId),
    onSuccess: (r) => { setSecret({ title: 'User created', email: r.user.email, password: r.initial_password }); setEmail(''); refresh(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const patch = useMutation({
    mutationFn: (v: { id: string; role?: Role; disabled?: boolean }) => org.patchUser(v.id, { role: v.role, disabled: v.disabled }, orgId),
    onSuccess: () => { toast.ok('Saved'); refresh(); }, onError: (e) => toast.error(errorMessage(e)),
  });
  const reset = useMutation({
    mutationFn: (u: OrgUser) => org.resetPassword(u.id, orgId).then((r) => ({ u, r })),
    onSuccess: ({ u, r }) => setSecret({ title: 'Password reset', email: u.email, password: r.initial_password }), onError: (e) => toast.error(errorMessage(e)),
  });
  if (users.isLoading || info.isLoading) return <SkeletonList rows={4} />;
  if (users.isError) return <ErrorBox error={users.error} onRetry={() => void users.refetch()} />;
  const roles = info.data?.roles ?? [];
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>Team</h2><span className="hint">{users.data?.items.length ?? 0} people in {info.data?.name}</span></div>
        <table className="table">
          <thead><tr><th>Person</th><th>Role</th><th>Status</th><th>Added</th>{editable && <th />}</tr></thead>
          <tbody>
            {users.data?.items.map((u) => {
              const me = u.id === meId;
              return (
                <tr key={u.id}>
                  <td><b>{u.email}</b> {me && <Badge tone="info">you</Badge>}</td>
                  <td>{editable && !me && u.role !== 'SUPER_ADMIN' ? (
                    <select className="select" style={{ width: 170 }} aria-label={`Role of ${u.email}`} value={u.role} disabled={patch.isPending}
                      onChange={(e) => patch.mutate({ id: u.id, role: e.target.value as Role })}>
                      {roles.map((r) => <option key={r} value={r}>{roleTitle(r)}</option>)}
                    </select>) : roleTitle(u.role)}</td>
                  <td>{u.disabled ? <Badge tone="bad">disabled</Badge> : u.locked ? <Badge tone="warn">temporarily locked</Badge> : <Badge tone="ok">active</Badge>}</td>
                  <td className="muted">{fmtTime(u.created_at)}</td>
                  {editable && <td className="row" style={{ justifyContent: 'flex-end' }}>
                    {!me && <button className="btn sm" disabled={patch.isPending} onClick={() => patch.mutate({ id: u.id, disabled: !u.disabled })}>{u.disabled ? 'Enable' : 'Disable'}</button>}
                    <button className="btn sm" disabled={reset.isPending} onClick={() => reset.mutate(u)}>Reset password</button></td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
      {editable && (
        <form className="card pad stack" onSubmit={(e) => { e.preventDefault(); if (email.trim()) add.mutate(); }}>
          <h2>Add a person</h2>
          <div className="row wrap">
            <div className="field grow"><label htmlFor="nu-e">Work email</label><input id="nu-e" className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" required /></div>
            <div className="field"><label htmlFor="nu-r">Role</label>
              <select id="nu-r" className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>{roles.map((r) => <option key={r} value={r}>{roleTitle(r)}</option>)}</select></div>
            <div className="field"><label>&nbsp;</label><button className="btn primary" disabled={add.isPending || !email.trim()}>{add.isPending ? 'Adding…' : 'Add person'}</button></div>
          </div>
          <p className="hint">They get a one-time password to sign in with. {info.data?.kind === 'BUYER' ? 'Buyer roles: Buyer starts projects and makes every decision; the Organization admin manages this page and sets rules, but never approves.' : 'Supplier roles: Supplier sees the requests it was invited to; the Organization admin manages this page.'}</p>
        </form>
      )}
      {secret && <Secret {...secret} onClose={() => setSecret(null)} />}
    </div>
  );
}

// ------------------------------------------------------------------ own-side agent settings
interface Draft { agents: Record<string, { timeout?: string; calls?: string; model?: string }>; prompts: Record<string, string> }

function toDraft(v: OverridesView): Draft {
  const d: Draft = { agents: {}, prompts: {} };
  for (const [k, a] of Object.entries(v.active.agents ?? {})) {
    d.agents[k] = { timeout: a.timeout_s?.toString(), calls: a.max_tool_calls?.toString(), model: a.model_override ? `${a.model_override.provider}:${a.model_override.model}` : undefined };
  }
  for (const [k, p] of Object.entries(v.active.prompts ?? {})) d.prompts[k] = p.body;
  return d;
}

function toPayload(v: OverridesView, d: Draft): OverrideSpec {
  const agents: NonNullable<OverrideSpec['agents']> = {};
  for (const [k, a] of Object.entries(d.agents)) {
    const base = v.editable.agents[k];
    const o: NonNullable<OverrideSpec['agents']>[string] = {};
    if (a.timeout && Number(a.timeout) !== base?.timeout_s) o.timeout_s = Number(a.timeout);
    if (a.calls && Number(a.calls) !== base?.max_tool_calls) o.max_tool_calls = Number(a.calls);
    if (a.model) { const i = a.model.indexOf(':'); o.model_override = { provider: a.model.slice(0, i), model: a.model.slice(i + 1) }; }
    if (Object.keys(o).length) agents[k] = o;
  }
  const prompts: NonNullable<OverrideSpec['prompts']> = {};
  for (const [k, body] of Object.entries(d.prompts)) if (body.trim() && body !== v.editable.prompts[k]?.body) prompts[k] = { body };
  return { schema_version: 1, ...(Object.keys(agents).length ? { agents } : {}), ...(Object.keys(prompts).length ? { prompts } : {}) };
}

function AgentSettings({ orgId, editable }: { orgId?: string; editable: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['org-overrides', orgId], queryFn: () => org.overrides(orgId) });
  const [draft, setDraft] = useState<Draft>({ agents: {}, prompts: {} });
  const [note, setNote] = useState('');
  const [issues, setIssues] = useState<{ path: string; code: string; message: string }[]>([]);
  useEffect(() => { if (q.data) setDraft(toDraft(q.data)); }, [q.data]);
  const save = useMutation({
    mutationFn: () => org.saveOverrides(toPayload(q.data as OverridesView, draft), note, orgId),
    onSuccess: (r) => { toast.ok(`Saved as version ${r.version}. New projects use it; running ones keep the version they started with.`); setNote(''); setIssues([]); void qc.invalidateQueries({ queryKey: ['org-overrides', orgId] }); void qc.invalidateQueries({ queryKey: ['orgs'] }); },
    onError: (e) => {
      const d = e instanceof ApiError ? ((e.body as { error?: { details?: { issues?: typeof issues } } } | undefined)?.error?.details?.issues ?? []) : [];
      setIssues(d); toast.error(d.length ? 'Some settings are not allowed' : errorMessage(e));
    },
  });
  const rollback = useMutation({
    mutationFn: (v: number) => org.rollbackOverrides(v, orgId),
    onSuccess: (r) => { toast.ok(`Rolled back as version ${r.version}`); void qc.invalidateQueries({ queryKey: ['org-overrides', orgId] }); }, onError: (e) => toast.error(errorMessage(e)),
  });
  const payload = useMemo(() => (q.data ? toPayload(q.data, draft) : {}), [q.data, draft]);
  if (q.isLoading) return <SkeletonList rows={5} />;
  if (q.isError || !q.data) return <ErrorBox error={q.error} onRetry={() => void q.refetch()} />;
  const v = q.data;
  const setAgent = (k: string, patch: Draft['agents'][string]) => setDraft((d) => ({ ...d, agents: { ...d.agents, [k]: { ...d.agents[k], ...patch } } }));
  const changed = JSON.stringify(payload) !== JSON.stringify(toPayload(v, toDraft(v)));
  return (
    <div className="stack">
      <div className="callout info"><span aria-hidden="true">i</span><div>These settings apply to <b>your side only</b> and sit on top of the platform configuration that SBN publishes (version {v.platform_version}). You can tune timeouts, tool-call limits, the model{v.kind === 'BUYER' ? ' and prompt wording' : ''} of your agents. Safety rules, checkpoints, contract clauses and the audit trail are not configurable here. Each save is a new version; projects keep the version they started with.</div></div>
      <section className="card">
        <div className="card-head"><h2>Your agents</h2>{v.active_version ? <Badge tone="accent">your version {v.active_version}</Badge> : <Badge>platform defaults</Badge>}</div>
        <table className="table">
          <thead><tr><th>Agent</th><th>Timeout (s)</th><th>Max tool calls</th><th>Model</th></tr></thead>
          <tbody>
            {Object.entries(v.editable.agents).map(([k, a]) => {
              const d = draft.agents[k] ?? {};
              return (
                <tr key={k}>
                  <td><b>{toolText(k)}</b>{a.lead && <div className="hint">lead agent</div>}</td>
                  <td><input className="input" style={{ width: 90 }} type="number" min={1} max={120} aria-label={`${toolText(k)} timeout`} disabled={!editable} placeholder={String(a.timeout_s)} value={d.timeout ?? ''} onChange={(e) => setAgent(k, { timeout: e.target.value })} /></td>
                  <td>{a.lead ? <input className="input" style={{ width: 90 }} type="number" min={1} max={50} aria-label={`${toolText(k)} max tool calls`} disabled={!editable} placeholder={String(a.max_tool_calls)} value={d.calls ?? ''} onChange={(e) => setAgent(k, { calls: e.target.value })} /> : <span className="muted">-</span>}</td>
                  <td><select className="select" style={{ width: 230 }} aria-label={`${toolText(k)} model`} disabled={!editable} value={d.model ?? ''} onChange={(e) => setAgent(k, { model: e.target.value || undefined })}>
                    <option value="">platform default</option>{v.editable.models.map((m) => <option key={m} value={m}>{m}</option>)}</select></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="hint" style={{ padding: '8px 14px' }}>Leave a field empty to use the platform value (shown greyed).</p>
      </section>
      {Object.keys(v.editable.prompts).length > 0 && (
        <section className="card pad stack">
          <h2>Prompt wording</h2>
          <p className="hint">Only the instruction text. The safety wrapper around untrusted text and the required output format are added by the system and cannot be changed.</p>
          {Object.entries(v.editable.prompts).map(([k, p]) => (
            <div className="field" key={k}><label htmlFor={`pr-${k}`}>{toolText(k)}</label>
              <textarea id={`pr-${k}`} className="textarea" style={{ fontFamily: 'inherit', minHeight: 110 }} disabled={!editable} maxLength={4000} value={draft.prompts[k] ?? p.body}
                onChange={(e) => setDraft((d) => ({ ...d, prompts: { ...d.prompts, [k]: e.target.value } }))} />
              {editable && (draft.prompts[k] ?? p.body) !== p.body && <button type="button" className="btn sm" style={{ alignSelf: 'flex-start' }} onClick={() => setDraft((d) => { const n = { ...d.prompts }; delete n[k]; return { ...d, prompts: n }; })}>Use the platform wording</button>}
            </div>
          ))}
        </section>
      )}
      {issues.length > 0 && <div className="callout bad" role="alert"><div><b>These settings were not saved:</b><ul className="reason-list">{issues.map((i, n) => <li key={n}>{i.message} <span className="code">{i.path} · {i.code}</span></li>)}</ul></div></div>}
      {editable && (
        <div className="row wrap">
          <input className="input grow" aria-label="What changed" placeholder="What changed? (optional)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn primary" disabled={save.isPending || !changed} onClick={() => save.mutate()}>{save.isPending ? 'Saving…' : 'Save as a new version'}</button>
        </div>
      )}
      <section className="card">
        <div className="card-head"><h2>Version history</h2></div>
        {v.versions.length === 0 ? <Empty title="No versions yet">Your first save creates version 1.</Empty> : (
          <table className="table"><thead><tr><th>Version</th><th>Saved</th><th>Note</th><th /></tr></thead>
            <tbody>{v.versions.map((x) => (
              <tr key={x.version}><td><b>v{x.version}</b> {x.version === v.active_version && <Badge tone="accent">active</Badge>}</td><td className="muted">{fmtTime(x.created_at)}</td><td>{x.note || <span className="muted">-</span>}</td>
                <td style={{ textAlign: 'right' }}>{editable && x.version !== v.active_version && <button className="btn sm" disabled={rollback.isPending} onClick={() => rollback.mutate(x.version)}>Make active again</button>}</td></tr>))}</tbody></table>
        )}
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ SBN: organizations and staff
function Organizations({ onManage, editable }: { onManage: (id: string) => void; editable: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['orgs'], queryFn: admin.orgs });
  const [find, setFind] = useState('');
  const [f, setF] = useState({ name: '', kind: 'BUYER' as 'BUYER' | 'SUPPLIER', email: '' });
  const [s, setS] = useState({ email: '', role: 'EVALUATOR' as Role });
  const [secret, setSecret] = useState<{ title: string; email: string; password: string } | null>(null);
  const create = useMutation({
    mutationFn: () => admin.createOrg({ name: f.name.trim(), kind: f.kind, admin_email: f.email.trim() }),
    onSuccess: (r) => { setSecret({ title: `${r.org.name} created`, email: r.admin.email, password: r.initial_password }); setF({ name: '', kind: 'BUYER', email: '' }); void qc.invalidateQueries({ queryKey: ['orgs'] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const staff = useMutation({
    mutationFn: () => admin.createStaff({ email: s.email.trim(), role: s.role }),
    onSuccess: (r) => { setSecret({ title: 'SBN staff created', email: r.user.email, password: r.initial_password }); setS({ email: '', role: 'EVALUATOR' }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isLoading) return <SkeletonList rows={4} />;
  if (q.isError) return <ErrorBox error={q.error} onRetry={() => void q.refetch()} />;
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head"><h2>Organizations</h2><span className="spacer" />
          <input className="input" style={{ width: 240 }} aria-label="Find an organization" placeholder="Find an organization" value={find} onChange={(e) => setFind(e.target.value)} /></div>
        <table className="table"><thead><tr><th>Organization</th><th>Type</th><th>People</th><th>Projects</th><th>Own settings</th><th /></tr></thead>
          <tbody>{q.data?.items.filter((o) => !find.trim() || o.name.toLowerCase().includes(find.trim().toLowerCase())).slice(0, 100).map((o) => (
            <tr key={o.id}><td><b>{o.name}</b> {o.platform && <Badge tone="info">SBN</Badge>}</td><td>{o.kind === 'BUYER' ? 'Buyer' : 'Supplier'}</td><td className="num">{o.users}</td><td className="num">{o.projects}</td>
              <td>{o.override_version ? `v${o.override_version}` : <span className="muted">platform defaults</span>}</td>
              <td style={{ textAlign: 'right' }}><button className="btn sm" onClick={() => onManage(o.id)}>Open</button></td></tr>))}</tbody></table>
      </section>
      {editable && (
        <>
          <form className="card pad stack" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <h2>Create an organization</h2>
            <div className="row wrap">
              <div className="field grow"><label htmlFor="co-n">Name</label><input id="co-n" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required minLength={2} /></div>
              <div className="field"><label htmlFor="co-k">Type</label><select id="co-k" className="select" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as 'BUYER' | 'SUPPLIER' })}><option value="BUYER">Buyer</option><option value="SUPPLIER">Supplier</option></select></div>
              <div className="field grow"><label htmlFor="co-e">First admin&rsquo;s email</label><input id="co-e" className="input" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required /></div>
              <div className="field"><label>&nbsp;</label><button className="btn primary" disabled={create.isPending}>Create</button></div>
            </div>
          </form>
          <form className="card pad stack" onSubmit={(e) => { e.preventDefault(); staff.mutate(); }}>
            <h2>Add SBN staff</h2>
            <div className="row wrap">
              <div className="field grow"><label htmlFor="st-e">Email</label><input id="st-e" className="input" type="email" value={s.email} onChange={(e) => setS({ ...s, email: e.target.value })} required /></div>
              <div className="field"><label htmlFor="st-r">Role</label><select id="st-r" className="select" value={s.role} onChange={(e) => setS({ ...s, role: e.target.value as Role })}>
                {(['EVALUATOR', 'DESIGNER', 'SUPER_ADMIN'] as Role[]).map((r) => <option key={r} value={r}>{roleTitle(r)}</option>)}</select></div>
              <div className="field"><label>&nbsp;</label><button className="btn primary" disabled={staff.isPending}>Add</button></div>
            </div>
          </form>
        </>
      )}
      {secret && <Secret {...secret} onClose={() => setSecret(null)} />}
    </div>
  );
}

export function Organization() {
  const me = useAuth((s) => s.me);
  const role = me?.role;
  const platform = role === 'SUPER_ADMIN' || role === 'EVALUATOR';
  const editable = can.editOrganization(role);
  const [orgId, setOrgId] = useState<string | undefined>(undefined);
  const [tab, setTab] = useState<Tab>(platform ? 'orgs' : 'team');
  const info = useQuery({ queryKey: ['org', orgId], queryFn: () => org.get(orgId) });
  // SBN starts on the list of organizations; Team and Agent settings only make sense once an organization is open
  const tabs: [Tab, string][] = platform && !orgId ? [['orgs', 'Organizations']] : [...(platform ? ([['orgs', 'Organizations']] as [Tab, string][]) : []), ['team', 'Team'], ['agents', 'Agent settings']];
  const showing = platform && !orgId && tab !== 'orgs' ? 'SBN' : info.data?.name;
  return (
    <>
      <div className="page-head"><div className="grow"><h1>{platform ? 'Organizations' : 'Your organization'}</h1>
        <p>{platform ? 'SBN view across every organization. Open one to manage its people and its own-side agent settings.' : `${info.data?.name ?? ''} — manage your people and tune your own agents.`}</p></div>
        {platform && orgId && <button className="btn" onClick={() => { setOrgId(undefined); setTab('orgs'); }}>All organizations</button>}</div>
      {platform && orgId && <p className="hint" style={{ marginBottom: 8 }}>Managing: <b>{showing}</b> ({info.data?.kind?.toLowerCase()})</p>}
      <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
        {tabs.map(([t, l]) => <button key={t} role="tab" aria-selected={tab === t} className="tab" onClick={() => setTab(t)}>{l}</button>)}
      </div>
      {tab === 'orgs' && platform && <Organizations editable={editable} onManage={(id) => { setOrgId(id); setTab('team'); }} />}
      {tab === 'team' && <Team orgId={orgId} editable={editable} />}
      {tab === 'agents' && <AgentSettings orgId={orgId} editable={editable} />}
    </>
  );
}
