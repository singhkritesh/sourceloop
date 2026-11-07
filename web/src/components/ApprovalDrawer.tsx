import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { approvals, projects } from '../api/endpoints';
import { SUMMARY_KINDS } from '../api/summaryTypes';
import { NegotiationSummaryView } from './NegotiationSummary';
import { BankCheckView, type BankCheckData } from './BankCheck';
import { ApiError, errorMessage } from '../api/http';
import { awardTerms, buildEdit, editOptions, isEditable } from '../lib/approvalEdit';
import { fieldErrors, isLostOnRestart, isRuntimeUnavailable, LOST_ON_RESTART_TEXT } from '../lib/errors';
import type { Approval, Project } from '../api/types';
import { Icon } from './Icon';
import { findConfidence, findSources, Tree } from './Tree';
import { AI_RECOMMENDATION_NOTICE } from '../lib/aiDisclosure';
import { approvalInfo, approvalTitle, reasonText, roleLabel, stageName } from '../lib/friendly';
import { Badge, Drawer, fmtTime } from './ui';
import { toast } from '../state/ui';

export const approvalTone = (s: string) => (s === 'PENDING' ? 'warn' : s === 'APPROVED' ? 'ok' : s === 'REJECTED' ? 'bad' : 'info');
const tone = approvalTone;

/** The decision panel: one approval with its explanation, evidence, summary and the approve / edit / reject controls.
 *  Used by the Approvals inbox and by the Projects list, so both places behave the same. */
export function ApprovalDrawer({ a, onClose, canResolve }: { a: Approval; onClose: () => void; canResolve: boolean }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<'view' | 'reject' | 'edit'>('view');
  const [comment, setComment] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [supplier, setSupplier] = useState<string | null>(null);
  const [paymentDays, setPaymentDays] = useState<number | null>(null);
  const [acceptChange, setAcceptChange] = useState<boolean | null>(null);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [lostRuntime, setLostRuntime] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [read, setRead] = useState(false);
  const needsSummary = SUMMARY_KINDS.includes(a.kind);
  const sum = useQuery({ queryKey: ['negotiation-summary', a.project_id, a.id], queryFn: () => projects.negotiationSummary(a.project_id), enabled: needsSummary, refetchOnWindowFocus: false });
  const editable = isEditable(a.kind);
  const options = useMemo(() => editOptions(a.kind, a.evidence), [a.kind, a.evidence]);
  const pending = a.status === 'PENDING';
  const bank = a.kind === 'HOLD_RELEASE' ? ((a.evidence as { bank_check?: BankCheckData } | null)?.bank_check ?? null) : null;
  const bankBlocked = a.kind === 'HOLD_RELEASE' && !(bank && bank.ok);
  const ackNeeded = needsSummary && pending && !!sum.data?.ack_required;
  // until the summary has loaded we cannot know whether a confirmation is required, so nothing can be approved (a failed load blocks too)
  const summaryMissing = needsSummary && pending && !sum.data;
  const cachedProject = qc.getQueryData<Project>(['project', a.project_id]);
  const lost = lostRuntime || (cachedProject ? isLostOnRestart(cachedProject) : false);
  const conf = findConfidence(a.evidence) ?? findConfidence(a.recommendation);
  const sources = [...findSources(a.evidence), ...findSources(a.recommendation)];
  const commentOk = comment.trim().length >= 10;
  const editObj = mode === 'edit' ? buildEdit(a.kind, { selected, supplier, paymentDays, acceptChange }, a.evidence) : null;
  const toggle = (id: string) => { setFieldErr({}); setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id])); };

  const m = useMutation({
    mutationFn: (status: 'APPROVED' | 'REJECTED' | 'EDITED') =>
      approvals.resolve(a.id, { status, comment: comment.trim(), version: a.version, ...(status !== 'REJECTED' && ackNeeded && sum.data ? { summary_hash: sum.data.hash } : {}), ...(status === 'EDITED' && editObj ? { edit: editObj } : {}) }),
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
      } else if (e instanceof ApiError && (e.code === 'SUMMARY_CHANGED' || e.code === 'SUMMARY_NOT_CONFIRMED')) {
        setRead(false);
        void sum.refetch();
        toast.warn(e.code === 'SUMMARY_CHANGED' ? 'The negotiation record changed. Read the updated summary and confirm again.' : 'Read the negotiation summary and tick the box first.');
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
    if (s !== 'REJECTED' && bankBlocked) { toast.warn('The bank details did not pass the checks. Decline and ask the supplier to correct them.'); return; }
    if (s !== 'REJECTED' && summaryMissing) { toast.warn('The negotiation summary has not loaded yet. Wait for it, then confirm it.'); return; }
    if (s !== 'REJECTED' && ackNeeded && !read) { toast.warn('Read the negotiation summary and tick the box first.'); return; }
    setConflict(false);
    setFieldErr({});
    m.mutate(s);
  }, [pending, m, commentOk, editObj, editable, lost, ackNeeded, read, summaryMissing, bankBlocked]);

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
              onSupplier={(v) => { setFieldErr({}); setSupplier(v); setPaymentDays(null); }} evidence={a.evidence} paymentDays={paymentDays} onPaymentDays={setPaymentDays} acceptChange={acceptChange}
              onAccept={(v) => { setFieldErr({}); setAcceptChange(v); }} errors={fieldErr} />
          )}
          {ackNeeded && sum.data && (
            <label className="row"><input type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} /> I have read the negotiation summary above and it is what I am deciding on.</label>
          )}
          <div className="row wrap">
            <button className="btn primary" disabled={m.isPending || lost || bankBlocked || summaryMissing || (ackNeeded && !read)} onClick={() => go('APPROVED')}><Icon name="check" />Approve <span className="kbd">a</span></button>
            {!editable || lost ? null : mode === 'edit' ? <button className="btn" disabled={m.isPending || !commentOk || !editObj || bankBlocked || summaryMissing || (ackNeeded && !read)} onClick={() => go('EDITED')}>Submit edit</button> : <button className="btn" onClick={() => setMode('edit')}>Edit <span className="kbd">e</span></button>}
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
        {bank && !sum.data?.payment && <BankCheckView data={bank} />}
        {needsSummary && (sum.isLoading ? <span className="muted">Loading the negotiation summary…</span> : sum.data ? <NegotiationSummaryView s={sum.data} /> : (
          <div className="callout bad" role="alert"><Icon name="alert" /><div>The negotiation summary could not be loaded, so this cannot be approved yet. <button className="btn sm" onClick={() => void sum.refetch()}>Try again</button></div></div>))}
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

function EditControls({ kind, options, selected, onToggle, supplier, onSupplier, acceptChange, onAccept, errors, evidence, paymentDays, onPaymentDays }: {
  evidence: unknown; paymentDays: number | null; onPaymentDays: (d: number | null) => void;
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
      {kind === 'AWARD' && awardTerms(evidence, supplier).length > 1 && (
        <div className="stack" style={{ gap: 4 }}>
          <b>Payment term</b>
          {awardTerms(evidence, supplier).map((t, i) => (
            <label key={t.days} className="row"><input type="radio" name="award-term" checked={(paymentDays ?? awardTerms(evidence, supplier)[0]?.days) === t.days} onChange={() => onPaymentDays(i === 0 ? null : t.days)} />
              <span>Net {t.days}</span> <span className="num">{t.price} per unit</span></label>
          ))}
          <span className="hint">A longer term raises the price by the financing cost set in Studio (an assumed rate). Only terms inside both sides' limits are listed.</span>
        </div>
      )}
      {kind === 'CLAUSE' && (
        <label className="row"><input type="checkbox" role="switch" checked={acceptChange === true}
          onChange={(e) => onAccept(e.target.checked)} /> Accept the proposed change {acceptChange === null && <span className="hint">(choose to enable submit)</span>}</label>
      )}
      {(['include', 'fail', 'supplier', 'accept_change'] as const).map((k) => err(k) ? <span key={k} className="err-text" role="alert">{k}: {err(k)}</span> : null)}
      {general && <span className="err-text" role="alert">{general}</span>}
    </fieldset>
  );
}
