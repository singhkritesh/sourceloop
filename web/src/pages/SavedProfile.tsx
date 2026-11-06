import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { supplierProfile } from '../api/endpoints';
import { errorMessage } from '../api/http';
import { PROFILE_FIELDS, parseProfile } from '../lib/profileFields';
import { Badge, ErrorBox, fmtTime, SkeletonList } from '../components/ui';
import { toast } from '../state/ui';

/** The figures this supplier's agent gives when a buyer's agent asks for information. Declared by the supplier and never verified here. */
export function SavedProfilePage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['supplier-profile'], queryFn: supplierProfile.get });
  const [text, setText] = useState<Record<string, string>>({});
  useEffect(() => { if (q.data) setText(Object.fromEntries(Object.entries(q.data.values).map(([k, v]) => [k, String(v)]))); }, [q.data]);
  const { values, errors } = parseProfile(text);
  const done = () => void qc.invalidateQueries({ queryKey: ['supplier-profile'] });
  const save = useMutation({ mutationFn: () => supplierProfile.save(values), onSuccess: () => { toast.ok('Figures saved and confirmed today'); done(); }, onError: (e) => toast.error(errorMessage(e)) });
  const clear = useMutation({ mutationFn: () => supplierProfile.remove(), onSuccess: () => { toast.ok('Saved figures removed'); setText({}); done(); }, onError: (e) => toast.error(errorMessage(e)) });
  if (q.isLoading) return <SkeletonList rows={4} />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => void q.refetch()} />;
  const p = q.data!;
  return (
    <div className="stack">
      <h1>Our figures</h1>
      <p className="muted">When a buyer's agent asks for information your records lack, your agent answers with these figures, so nobody has to fill in a questionnaire. They are
        <b> declared by you and not verified</b> by SourceLoop. They only fill blanks, they never replace what is on record, and the same checks still decide. They hold no price or limit.</p>
      <div className="row wrap">
        {p.confirmed_at ? <Badge tone={p.stale ? 'warn' : 'ok'}>{p.stale ? 'Out of date: not used until you confirm again' : 'Confirmed'} {fmtTime(p.confirmed_at)}</Badge> : <Badge>Nothing saved yet</Badge>}
      </div>
      <form className="card pad stack" onSubmit={(e) => { e.preventDefault(); if (!Object.keys(errors).length && Object.keys(values).length) save.mutate(); }}>
        {PROFILE_FIELDS.map((f) => (
          <div className="field" key={f.key}>
            <label htmlFor={`pf-${f.key}`}>{f.label}</label>
            <input id={`pf-${f.key}`} className="input" inputMode="decimal" value={text[f.key] ?? ''} onChange={(e) => setText({ ...text, [f.key]: e.target.value })} aria-invalid={!!errors[f.key]} />
            <span className="hint">{f.hint} Leave blank to give no answer.</span>
            {errors[f.key] && <span className="err-text" role="alert">{errors[f.key]}</span>}
          </div>
        ))}
        <div className="row">
          <button className="btn primary" type="submit" disabled={save.isPending || !Object.keys(values).length || !!Object.keys(errors).length}>{p.confirmed_at ? 'Save and confirm again' : 'Save'}</button>
          {p.confirmed_at && <button className="btn" type="button" disabled={clear.isPending} onClick={() => clear.mutate()}>Remove saved figures</button>}
        </div>
      </form>
    </div>
  );
}
