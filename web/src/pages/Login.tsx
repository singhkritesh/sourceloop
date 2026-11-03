import { useEffect, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { auth, type DemoAccount } from '../api/endpoints';
import { ApiError } from '../api/http';
import { Icon } from '../components/Icon';
import { useAuth } from '../state/auth';

const ROLE_HINT: Record<string, string> = {
  BUYER: 'Describes the need and makes every buyer-side decision.', SUPPLIER: 'Receives requests and quotes through its own agent.',
  SUPER_ADMIN: 'Platform operator: sets rules, mediates, sees everything.', ADMIN: 'Manages one organization and its agent settings; never approves.',
};
const ORDER = ['BUYER', 'SUPPLIER', 'SUPER_ADMIN', 'ADMIN'];

export function Login() {
  const setMe = useAuth((s) => s.setMe);
  const nav = useNavigate();
  const loc = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lockSecs, setLockSecs] = useState(0);
  const roles = useQuery({ queryKey: ['demo-roles'], queryFn: auth.demoRoles, retry: false });
  const [usePassword, setUsePassword] = useState(false);
  const [pickedFor, setPickedFor] = useState<Record<string, string>>({});
  const picker = !!roles.data?.enabled && !usePassword;

  async function pick(a: DemoAccount) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const me = await auth.demoLogin(a.email);
      setMe(me);
      const from = (loc.state as { from?: string } | null)?.from;
      nav(from && from !== '/login' ? from : '/', { replace: true });
    } catch {
      setError('Could not sign in as that role. Check that the app is running (./doctor.sh).');
    } finally { setBusy(false); }
  }

  useEffect(() => {
    if (lockSecs <= 0) return;
    const t = setInterval(() => setLockSecs((s) => (s > 0 ? s - 1 : 0)), 1000);
    return () => clearInterval(t);
  }, [lockSecs > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || lockSecs > 0) return;
    setBusy(true);
    setError(null);
    try {
      const me = await auth.login(email.trim(), password);
      setPassword('');
      setMe(me);
      const from = (loc.state as { from?: string } | null)?.from;
      nav(from && from !== '/login' ? from : '/', { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setLockSecs(err.retryAfter ?? 60);
        setError('Too many requests. Please wait before trying again.');
      } else if (err instanceof ApiError && err.status === 401) {
        setError('Email or password is incorrect, or the account is temporarily locked.');
      } else if (err instanceof ApiError && err.status === 413) {
        setError('That request is too large.');
      } else if (err instanceof ApiError && err.status >= 500) {
        setError('The server had a problem. Try again in a moment.');
      } else if (err instanceof ApiError && err.status === 422) {
        setError('Enter a valid email address and password.');
      } else {
        setError('Cannot reach the server. Check your connection and try again.');
      }
    } finally {
      setBusy(false);
    }
  }
  const mm = String(Math.floor(lockSecs / 60)).padStart(1, '0');
  const ss = String(lockSecs % 60).padStart(2, '0');
  if (picker) {
    const all = (roles.data?.accounts ?? []).filter((a) => ORDER.includes(a.role));
    // accounts the test bed creates on the fly for a supplier that is asked to confirm terms are not offered while the standing ones exist
    const standing = all.filter((a) => !/^supplier-s\d+@/.test(a.email));
    const accounts = standing.length ? standing : all;
    const kinds: { key: string; role: string; orgKind?: string; title: string; ico: string; cls: string }[] = [
      { key: 'buyer', role: 'BUYER', title: 'Buyer', ico: 'B', cls: 'buyer' },
      { key: 'supplier', role: 'SUPPLIER', title: 'Supplier', ico: 'S', cls: 'supplier' },
      { key: 'admin', role: 'ADMIN', title: 'Organization admin', ico: 'A', cls: 'admin' },
      { key: 'sbn', role: 'SUPER_ADMIN', title: 'SBN admin', ico: 'N', cls: 'sbn' },
    ];
    return (
      <div className="login-wrap">
        <aside className="login-story">
          <div className="brand"><Icon name="logo" /><span>SourceLoop</span></div>
          <div>
            <h2>Sourcing that runs itself, and asks you only when it matters.</h2>
            <p>A buyer agent and a supplier agent find, qualify and negotiate. Limits live in code, the audit trail is tamper-evident, and you decide only what truly needs a person.</p>
          </div>
          <ul className="login-points">
            <li><span className="dot">1</span><div><b>Find and qualify</b><span>Suppliers sorted into ready, missing information or not eligible.</span></div></li>
            <li><span className="dot">2</span><div><b>Negotiate within limits</b><span>Offers and counteroffers, each checked against a mandate neither side can see.</span></div></li>
            <li><span className="dot">3</span><div><b>Decide from anywhere</b><span>Approve from chat, mail or text, on your phone.</span></div></li>
          </ul>
        </aside>
        <main className="login-main">
          <div className="login-panel">
            <div><h1 id="login-h">Choose a role to continue</h1><p className="muted">Demo environment with made-up data. No password needed: pick who you want to be.</p></div>
            {error && <div className="callout bad" role="alert"><Icon name="alert" /><div>{error}</div></div>}
            <div className="kind-grid">
              {kinds.map((k) => {
                const list = accounts.filter((a) => a.role === k.role).sort((x, y) => x.org.localeCompare(y.org));
                if (!list.length) return null;
                const chosen = list.find((a) => a.email === pickedFor[k.key]) ?? list[0]!;
                return (
                  <div key={k.key} className={`kind ${k.cls}`}>
                    <button type="button" className="kind-btn" disabled={busy} onClick={() => void pick(chosen)}>
                      <span className="ico" data-l={k.ico} aria-hidden="true" />
                      <b>{k.title}</b>
                      <span className="d">{ROLE_HINT[k.role] ?? ''}</span>
                    </button>
                    {list.length > 1
                      ? <select className="select" aria-label={`Company for ${k.title}`} value={chosen.email} onChange={(e) => setPickedFor((m) => ({ ...m, [k.key]: e.target.value }))}>
                          {list.map((a) => <option key={a.email} value={a.email}>{a.org}</option>)}</select>
                      : <span className="org-one">{chosen.org}</span>}
                  </div>
                );
              })}
            </div>
            <button className="btn ghost" type="button" onClick={() => setUsePassword(true)}>Sign in with a password instead</button>
          </div>
        </main>
      </div>
    );
  }
  return (
    <div className="login-wrap">
      <aside className="login-story"><div className="brand"><Icon name="logo" /><span>SourceLoop</span></div>
        <div><h2>Sourcing that runs itself, and asks you only when it matters.</h2><p>Agentic sourcing, with limits enforced in code.</p></div><span /></aside>
      <main className="login-main"><form className="card login-card stack" style={{ width: 'min(420px, 100%)' }} onSubmit={(e) => void submit(e)} aria-labelledby="login-h">
        <div className="brand"><Icon name="logo" /><span>SourceLoop</span></div>
        <div><h1 id="login-h">Sign in</h1><p className="muted">Agentic sourcing, with limits enforced in code.</p></div>
        {error && (
          <div className={`callout ${lockSecs > 0 ? 'warn' : 'bad'}`} role="alert">
            <Icon name={lockSecs > 0 ? 'lock' : 'alert'} />
            <div>{error}{lockSecs > 0 && <div><b className="num">Try again in {mm}:{ss}</b></div>}</div>
          </div>
        )}
        <div className="field"><label htmlFor="em">Email</label>
          <input id="em" className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus aria-invalid={!!error && lockSecs === 0} /></div>
        <div className="field"><label htmlFor="pw">Password</label>
          <input id="pw" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={!!error && lockSecs === 0} /></div>
        <button className="btn primary" style={{ height: 38 }} disabled={busy || lockSecs > 0 || !email || !password}>
          {busy ? 'Signing in…' : lockSecs > 0 ? `Locked (${lockSecs}s)` : 'Sign in'}
        </button>
      </form></main>
    </div>
  );
}
