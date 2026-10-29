import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { errorMessage } from '../api/http';
import { studio } from '../api/studio';
import type { ProviderRow, ProviderTest } from '../api/studioTypes';
import { Icon } from '../components/Icon';
import { Badge, Dialog, Empty, ErrorBox, SkeletonList } from '../components/ui';
import { toast } from '../state/ui';

export function ProvidersPanel({ isAdmin, onModels }: { isAdmin: boolean; onModels: (provider: string, models: string[]) => void }) {
  const q = useQuery({ queryKey: ['studio', 'providers'], queryFn: studio.providers });
  return (
    <section style={{ marginTop: 24 }}>
      <h2>Provider connections</h2>
      <p className="hint" style={{ margin: '2px 0 10px' }}>Keys are write-only: stored encrypted on the server and never shown again. “Test connection” lists the models that can be assigned to roles and agents.</p>
      {q.isLoading ? <SkeletonList rows={2} h={90} /> : q.isError ? <ErrorBox error={q.error} onRetry={() => void q.refetch()} /> :
        !q.data?.length ? <div className="card"><Empty icon="key" title="No providers yet">Add one in the form above; it appears here once the draft is saved.</Empty></div> :
          q.data.map((p) => <ProviderCard key={p.name} p={p} isAdmin={isAdmin} onModels={onModels} />)}
    </section>
  );
}

function ProviderCard({ p, isAdmin, onModels }: { p: ProviderRow; isAdmin: boolean; onModels: (n: string, m: string[]) => void }) {
  const qc = useQueryClient();
  const [secret, setSecret] = useState('');
  const [result, setResult] = useState<ProviderTest | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ['studio', 'providers'] });
  const save = useMutation({
    mutationFn: () => studio.setKey(p.name, secret),
    onSuccess: () => { setSecret(''); toast.ok(`Key stored for ${p.name}`); void refresh(); },
    onError: (e) => { setSecret(''); toast.error(errorMessage(e)); },
  });
  const del = useMutation({
    mutationFn: () => studio.deleteKey(p.name),
    onSuccess: () => { toast.ok('Key removed'); setConfirmDel(false); void refresh(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: () => studio.testProvider(p.name),
    onSuccess: (r) => { setResult(r); if (r.ok) onModels(p.name, r.models); },
    onError: (e) => setResult({ ok: false, models: [], error: errorMessage(e) }),
  });
  return (
    <div className="card pad stack" style={{ marginBottom: 10 }}>
      <div className="row wrap">
        <h3>{p.name}</h3><Badge>{p.kind.toLowerCase()}</Badge>
        {p.is_local && <Badge tone="info">local</Badge>}
        <Badge tone={p.has_key ? 'ok' : ''}>{p.has_key ? 'key set' : 'no key'}</Badge>
        <span className="mono muted trunc grow">{p.base_url || '(default endpoint)'}</span>
        <button className="btn sm" disabled={test.isPending} onClick={() => test.mutate()}><Icon name="bolt" />{test.isPending ? 'Testing…' : 'Test connection'}</button>
      </div>
      {isAdmin && (
        <form className="row wrap" onSubmit={(e) => { e.preventDefault(); if (secret) save.mutate(); }} autoComplete="off">
          <label className="sr-only" htmlFor={`key-${p.name}`}>API key for {p.name}</label>
          <input id={`key-${p.name}`} className="input" style={{ maxWidth: 320 }} type="password" autoComplete="new-password" spellCheck={false}
            placeholder={p.has_key ? '•••••••• (enter a new key to replace)' : 'Paste API key'} value={secret} onChange={(e) => setSecret(e.target.value)} />
          <button className="btn sm primary" disabled={!secret || save.isPending}>{p.has_key ? 'Replace key' : 'Save key'}</button>
          {p.has_key && <button type="button" className="btn sm danger" onClick={() => setConfirmDel(true)}>Remove key</button>}
        </form>
      )}
      {result && (
        result.ok
          ? <div className="callout ok" role="status"><Icon name="check" /><div><b>Reachable.</b> {result.models.length} models discovered.
              <div className="row wrap" style={{ marginTop: 6 }}>{result.models.slice(0, 40).map((m) => <span key={m} className="chip mono">{m}</span>)}</div></div></div>
          : <div className="callout bad" role="alert"><Icon name="alert" /><div><b>Connection failed.</b> {result.error ?? 'Unknown error'}</div></div>
      )}
      {confirmDel && (
        <Dialog title={`Remove key for ${p.name}?`} onClose={() => setConfirmDel(false)} footer={<>
          <button className="btn" onClick={() => setConfirmDel(false)}>Cancel</button>
          <button className="btn danger" disabled={del.isPending} onClick={() => del.mutate()}>Remove</button></>}>
          <p>Runs that use this provider will fail closed until a new key is set.</p>
        </Dialog>
      )}
    </div>
  );
}
