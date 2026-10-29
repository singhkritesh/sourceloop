import { Fragment, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { reviewers, settings } from '../api/endpoints';
import { errorMessage } from '../api/http';
import { ErrorBox, SkeletonList } from '../components/ui';
import { can, useAuth } from '../state/auth';
import { toast } from '../state/ui';

const SECRET_KEYS = ['jwt_secret', 'org_signing_secret', 'config_encryption_key', 'database_url'];

export function SettingsPage() {
  const role = useAuth((s) => s.me?.role);
  const editable = can.editSettings(role);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['settings'], queryFn: settings.get });
  const [mode, setMode] = useState('replay');
  const [budget, setBudget] = useState(0);
  useEffect(() => {
    if (q.data) { setMode(String(q.data.llm_mode ?? 'replay')); setBudget(Number(q.data.token_budget_per_project ?? 0)); }
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => settings.put({ llm_mode: mode, token_budget_per_project: budget }),
    onSuccess: (d) => { qc.setQueryData(['settings'], d); toast.ok('Settings saved'); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const d = q.data;
  const rates = useQuery({ queryKey: ['override-rates'], queryFn: reviewers.overrideRates, enabled: role === 'SUPER_ADMIN' || role === 'EVALUATOR' || role === 'ADMIN' });
  return (
    <>
      <div className="page-head"><div className="grow"><h1>Settings</h1><p>System-wide values. Secrets are never shown. Model choice per project comes from the Studio.</p></div></div>
      {q.isLoading ? <SkeletonList rows={4} /> : q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> : d && (
        <div className="stack" style={{ maxWidth: 640 }}>
          <form className="card pad stack" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
            <h2>Runtime</h2>
            <div className="field"><label htmlFor="s-mode">LLM mode</label>
              <select id="s-mode" className="select" disabled={!editable} value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="replay">replay (recorded responses)</option><option value="live">live</option><option value="record">record</option></select></div>
            <div className="field"><label htmlFor="s-bud">Token budget per project</label>
              <input id="s-bud" className="input" type="number" min={1000} max={10000000} disabled={!editable} value={budget} onChange={(e) => setBudget(Number(e.target.value))} /></div>
            {editable ? <div><button className="btn primary" disabled={save.isPending}>{save.isPending ? 'Saving…' : 'Save'}</button></div> : <p className="hint">Read-only for your role.</p>}
          </form>
          {rates.data && (
            <div className="card pad">
              <h2 style={{ marginBottom: 8 }}>Reviewer override rates</h2>
              {rates.data.items.length === 0 ? <p className="hint">No resolved approvals yet.</p> : (
                <table className="table"><thead><tr><th>Reviewer</th><th>Role</th><th>Decisions</th><th>Override rate</th><th /></tr></thead>
                  <tbody>{rates.data.items.map((r) => (
                    <tr key={r.resolver_id}><td>{r.email ?? r.resolver_id}</td><td>{r.role}</td><td className="num">{r.decisions}</td>
                      <td className="num">{(r.override_rate * 100).toFixed(1)}%</td><td>{r.flag ? <span className="muted">{r.flag.replace(/_/g, ' ').toLowerCase()}</span> : null}</td></tr>
                  ))}</tbody></table>
              )}
            </div>
          )}
          <div className="card pad">
            <h2 style={{ marginBottom: 8 }}>Environment</h2>
            <dl className="kv">
              {Object.entries(d).filter(([k]) => !['llm_mode', 'token_budget_per_project'].includes(k)).map(([k, v]) => (
                <Fragment key={k}><dt>{k.replace(/_/g, ' ')}</dt><dd className={SECRET_KEYS.includes(k) ? 'mono' : undefined}>{SECRET_KEYS.includes(k) ? '****' : typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd></Fragment>
              ))}
            </dl>
          </div>
        </div>
      )}
    </>
  );
}
