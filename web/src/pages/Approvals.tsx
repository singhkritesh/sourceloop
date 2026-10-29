import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { approvals } from '../api/endpoints';
import { ApiError, errorMessage } from '../api/http';
import { buildEdit, editOptions, isEditable } from '../lib/approvalEdit';
import { fieldErrors, isLostOnRestart, isRuntimeUnavailable, LOST_ON_RESTART_TEXT } from '../lib/errors';
import type { Approval, Project } from '../api/types';
import { Icon } from '../components/Icon';
import { findConfidence, findSources, Tree } from '../components/Tree';
import { AI_RECOMMENDATION_NOTICE } from '../lib/aiDisclosure';
import { approvalInfo, approvalTitle, reasonText, roleLabel, stageName } from '../lib/friendly';
import { Badge, Drawer, Empty, ErrorBox, fmtTime, SkeletonList } from '../components/ui';
import { can, useAuth } from '../state/auth';
import { toast } from '../state/ui';

type Tab = 'PENDING' | 'RESOLVED' | 'ALL';
const tone = (s: string) => (s === 'PENDING' ? 'warn' : s === 'APPROVED' ? 'ok' : s === 'REJECTED' ? 'bad' : 'info');

export function Approvals() {
  const role = useAuth((s) => s.me?.role);
  const [tab, setTab] = useState<Tab>('PENDING');
  const [openId, setOpenId] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const q = useQuery({ queryKey: ['approvals', 'list'], queryFn: () => approvals.list(), refetchInterval: 10_000 });
  const rows = useMemo(() => (q.data?.items ?? []).filter((a) => (tab === 'ALL' ? true : tab === 'PENDING' ? a.status === 'PENDING' : a.status !== 'PENDING')), [q.data, tab]);
  const open = rows.find((a) => a.id === openId) ?? q.data?.items.find((a) => a.id === openId) ?? null;

  useEffect(() => {
    if (openId) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || e.metaKey || e.ctrlKey) return;
      if (e.key === 'j') setCursor((c) => Math.min(rows.length - 1, c + 1));
      else if (e.key === 'k') setCursor((c) => Math.max(0, c - 1));
      else if (e.key === 'Enter' && rows[cursor] && t.tagName !== 'BUTTON') setOpenId(rows[cursor].id);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [rows, cursor, openId]);

  return (
    <>
      <div className="page-head">
        <div className="grow"><h1>Approvals</h1><p>{role === 'EVALUATOR' ? 'Read-only view of every checkpoint.' : 'Decisions the agents need from you. They recommend; you decide, and every decision is recorded.'}</p></div>
        <span className="hint"><span className="kbd">j</span> <span className="kbd">k</span> move · <span className="kbd">Enter</span> open</span>
      </div>
      <div className="tabs" role="tablist" style={{ marginBottom: 12 }}>
        {(['PENDING', 'RESOLVED', 'ALL'] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className="tab" onClick={() => { setTab(t); setCursor(0); }}>
            {t[0] + t.slice(1).toLowerCase()}{t === 'PENDING' && q.data ? ` (${q.data.items.filter((a) => a.status === 'PENDING').length})` : ''}
          </button>
        ))}
      </div>
      {q.isLoading ? <SkeletonList rows={4} h={64} /> : q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> :
        rows.length === 0 ? <div className="card"><Empty icon="inbox" title={tab === 'PENDING' ? 'You\u2019re all caught up' : 'Nothing here'}>{tab === 'PENDING' ? 'No decisions are waiting on you. New ones appear here the moment an agent needs a person.' : 'No items match.'}</Empty></div> : (
          <div className="inbox">
            {rows.map((a, i) => (
              <button key={a.id} className={`appr ${i === cursor ? 'sel' : ''}`} onClick={() => { setCursor(i); setOpenId(a.id); }}>
                <Badge tone={tone(a.status)}>{a.status.toLowerCase()}</Badge>{a.overdue && <Badge tone="bad">OVERDUE</Badge>}
                <div style={{ minWidth: 0 }}>
                  <b className="appr-title">{approvalTitle(a.kind)}</b> <span className="muted">· {stageName(a.stage).toLowerCase()} stage · for {roleLabel(a.target_role)}</span>
                  <div className="muted trunc">{a.reasons.map(reasonText).join('; ') || 'Routine checkpoint'}</div>
                </div>
                <span className="muted num">{fmtTime(a.created_at)}</span>
              </button>
            ))}
          </div>
        )}
      {open && <ApprovalDrawer a={open} onClose={() => setOpenId(null)} canResolve={can.resolveApprovals(role)} />}
    </>
  );
}

function ApprovalDrawer({ a, onClose, canResolve }: { a: Approval; onClose: () => void; canResolve: boolean }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<'view' | 'reject' | 'edit'>('view');
  const [comment, setComment] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [supplier, setSupplier] = useState<string | null>(null);
  const [acceptChange, setAcceptChange] = useState<boolean | null>(null);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [lostRuntime, setLostRuntime] = useState(false);
  const [conflict, setConflict] = useState(false);
  const editable = isEditable(a.kind);
  const options = useMemo(() => editOptions(a.kind, a.evidence), [a.kind, a.evidence]);
  const pending = a.status === 'PENDING';
  const cachedProject = qc.getQueryData<Project>(['project', a.project_id]);
  const lost = lostRuntime || (cachedProject ? isLostOnRestart(cachedProject) : false);
  const conf = findConfidence(a.evidence) ?? findConfidence(a.recommendation);
  const sources = [...findSources(a.evidence), ...findSources(a.recommendation)];
  const commentOk = comment.trim().length >= 10;
  const editObj = mode === 'edit' ? buildEdit(a.kind, { selected, supplier, acceptChange }, a.evidence) : null;
  const toggle = (id: string) => { setFieldErr({}); setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id])); };

  const m = useMutation({
    mutationFn: (status: 'APPROVED' | 'REJECTED' | 'EDITED') =>
      approvals.resolve(a.id, { status, comment: comment.trim(), version: a.version, ...(status === 'EDITED' && editObj ? { edit: editObj } : {}) }),
    onSuccess: (r) => {
      toast.ok(`Marked ${r.status.toLowerCase()}`);
      void qc.invalidateQueries({ queryKey: ['approvals'] });
      void qc.invalidateQueries({ queryKey: ['project', a.project_id] });
      onClose();
    },
    onError: (e) => {
      if (isRuntimeUnavailable(e)) {
        setLostRuntime(true);
      } else if (e instanceof ApiError && e.status === 422 && e.code === 'INVALID_EDIT') {
        const fe = fieldErrors(e);
        setFieldErr(Object.keys(fe).length ? fe : { _: errorMessage(e) });
        void qc.invalidateQueries({ queryKey: ['approvals'] }); // approval stays pending at the same version
      } else if (e instanceof ApiError && e.status === 409) {
        setConflict(true);
        toast.warn('This item changed while you were reviewing it. It was reloaded; review and decide again.');
        void qc.invalidateQueries({ queryKey: ['approvals'] });
      } else toast.error(errorMessage(e));
    },
  });

  const go = useCallback((s: 'APPROVED' | 'REJECTED' | 'EDITED') => {
    if (!pending || m.isPending || lost) return;
    if (s !== 'APPROVED' && !commentOk) { toast.warn('A comment of at least 10 characters is required.'); return; }
    if (s === 'EDITED' && (!editable || !editObj)) return;
    setConflict(false);
    setFieldErr({});
    m.mutate(s);
  }, [pending, m, commentOk, editObj, editable, lost]);

  useEffect(() => {
    if (!canResolve || !pending || lost) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (/INPUT|TEXTAREA|SELECT/.test(t.tagName) || e.metaKey || e.ctrlKey) return;
      if (e.key === 'a') go('APPROVED');
      else if (e.key === 'r') setMode('reject');
      else if (e.key === 'e' && editable) setMode('edit');
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [canResolve, pending, go, lost, editable]);

  return (
    <Drawer onClose={onClose} title={<><span data-kind={a.kind}>{approvalTitle(a.kind)}</span> <Badge tone={tone(a.status)}>{a.status.toLowerCase()}</Badge></>}
      subtitle={<>{stageName(a.stage)} stage · for {roleLabel(a.target_role)} · {fmtTime(a.created_at)} · <Link to={`/projects/${a.project_id}`}>open project</Link></>}
      footer={pending && canResolve ? (
        <div className="stack">
          {mode !== 'view' && (
            <div className="field"><label htmlFor="cm">Comment (required, min 10 characters)</label>
              <textarea id="cm" className="textarea" style={{ minHeight: 64, fontFamily: 'inherit' }} value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} aria-invalid={!commentOk && comment.length > 0} autoFocus />
              <span className="hint num">{comment.trim().length}/10 minimum</span></div>
          )}
          {mode === 'edit' && editable && (
            <EditControls kind={a.kind} options={options} selected={selected} onToggle={toggle} supplier={supplier}
              onSupplier={(v) => { setFieldErr({}); setSupplier(v); }} acceptChange={acceptChange}
              onAccept={(v) => { setFieldErr({}); setAcceptChange(v); }} errors={fieldErr} />
          )}
          <div className="row wrap">
            <button className="btn primary" disabled={m.isPending || lost} onClick={() => go('APPROVED')}><Icon name="check" />Approve <span className="kbd">a</span></button>
            {!editable || lost ? null : mode === 'edit' ? <button className="btn" disabled={m.isPending || !commentOk || !editObj} onClick={() => go('EDITED')}>Submit edit</button> : <button className="btn" onClick={() => setMode('edit')}>Edit <span className="kbd">e</span></button>}
            {mode === 'reject' ? <button className="btn danger" disabled={m.isPending || !commentOk} onClick={() => go('REJECTED')}>Confirm reject</button> : <button className="btn danger" disabled={lost} onClick={() => setMode('reject')}>Reject <span className="kbd">r</span></button>}
            {mode !== 'view' && <button className="btn ghost" onClick={() => setMode('view')}>Cancel</button>}
          </div>
        </div>
      ) : !pending ? <span className="muted">Resolved {fmtTime(a.resolved_at)}.{a.comment ? ` “${a.comment}”` : ''}</span> : <span className="muted">Read-only for your role.</span>}>
      <div className="stack">
        {lost && <div className="callout bad" role="alert"><Icon name="alert" /><div><b>Project lost on restart.</b> {LOST_ON_RESTART_TEXT} Approving, rejecting and editing are disabled.</div></div>}
        {conflict && <div className="callout warn" role="alert"><Icon name="alert" /><div><b>Version conflict.</b> Someone else resolved or changed this item; the latest version has been reloaded.</div></div>}
        {!!a.recommendation && <div className="callout info" role="note"><Icon name="info" /><div>{AI_RECOMMENDATION_NOTICE}</div></div>}
        <ApprovalExplainer kind={a.kind} editable={editable} />
        <section><h4>Recommendation</h4><div style={{ marginTop: 6 }}>{a.recommendation ? <Tree value={a.recommendation} /> : <span className="muted">The agent made no recommendation.</span>}</div></section>
        <section><h4>Why a person is needed</h4><div className="row wrap" style={{ marginTop: 6 }}>{a.reasons.length ? a.reasons.map((r) => <span key={r} title={r}><Badge tone="warn">{reasonText(r)}</Badge></span>) : <span className="muted">none recorded</span>}</div></section>
        <section><h4>Agent confidence</h4>
          <div style={{ marginTop: 6 }}>{conf === null ? <span className="muted">Not reported for this item. Deterministic rules triggered it.</span> :
            <span className="chip"><span className="meter"><i style={{ width: `${Math.round(conf * 100)}%` }} /></span><b className="num">{Math.round(conf * 100)}%</b></span>}</div></section>
        <section><h4>Evidence</h4><div style={{ marginTop: 6 }}><Tree value={a.evidence} /></div></section>
        {sources.length > 0 && <section><h4>Sources</h4><ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{sources.map((s, i) => <li key={i}>{s}</li>)}</ul></section>}
      </div>
    </Drawer>
  );
}

function ApprovalExplainer({ kind, editable }: { kind: string; editable: boolean }) {
  const i = approvalInfo(kind);
  return (
    <section className="explain" aria-label="What you are deciding">
      <h3>{i.title}</h3>
      <p>{i.ask}</p>
      <dl>
        <dt>Why you</dt><dd>{i.why}</dd>
        <dt>If you approve</dt><dd>{i.approve}</dd>
        <dt>If you reject</dt><dd>{i.reject}</dd>
        {editable && i.edit && <><dt>If you edit</dt><dd>{i.edit}</dd></>}
      </dl>
    </section>
  );
}

function EditControls({ kind, options, selected, onToggle, supplier, onSupplier, acceptChange, onAccept, errors }: {
  kind: string; options: string[]; selected: string[]; onToggle: (id: string) => void; supplier: string | null;
  onSupplier: (id: string) => void; acceptChange: boolean | null; onAccept: (v: boolean) => void; errors: Record<string, string>;
}) {
  const err = (k: string) => errors[k] ?? errors[`edit.${k}`];
  const general = errors._;
  const none = options.length === 0 && kind !== 'CLAUSE';
  return (
    <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend style={{ fontWeight: 600 }}>
        {kind === 'COMPLIANCE_FLAG' ? 'Clear these flagged suppliers (include in sourcing)' : kind === 'TECHNICAL_QUAL' ? 'Mark these suppliers as failing technical qualification' : kind === 'AWARD' ? 'Award to supplier' : 'Clause change'}
      </legend>
      {none && <span className="muted">The approval evidence lists no suppliers to choose from.</span>}
      {(kind === 'COMPLIANCE_FLAG' || kind === 'TECHNICAL_QUAL') && options.map((o) => (
        <label key={o} className="row"><input type="checkbox" checked={selected.includes(o)} onChange={() => onToggle(o)} /> <span className="mono">{o}</span></label>
      ))}
      {kind === 'AWARD' && options.map((o) => (
        <label key={o} className="row"><input type="radio" name="award-supplier" checked={supplier === o} onChange={() => onSupplier(o)} /> <span className="mono">{o}</span></label>
      ))}
      {kind === 'CLAUSE' && (
        <label className="row"><input type="checkbox" role="switch" checked={acceptChange === true}
          onChange={(e) => onAccept(e.target.checked)} /> Accept the proposed change {acceptChange === null && <span className="hint">(choose to enable submit)</span>}</label>
      )}
      {(['include', 'fail', 'supplier', 'accept_change'] as const).map((k) => err(k) ? <span key={k} className="err-text" role="alert">{k}: {err(k)}</span> : null)}
      {general && <span className="err-text" role="alert">{general}</span>}
    </fieldset>
  );
}
