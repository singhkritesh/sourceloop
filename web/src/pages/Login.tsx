import { useEffect, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { auth, type DemoAccount } from '../api/endpoints';
import { roleTitle } from '../lib/friendly';
import { ApiError } from '../api/http';
import { Icon } from '../components/Icon';
import { useAuth } from '../state/auth';

const ROLE_HINT: Record<string, string> = {
  BUYER: 'Describes the need and makes every buyer-side decision', SUPPLIER: 'Receives requests and quotes through its own agent',
  SUPER_ADMIN: 'Platform operator: sets platform rules and mediates, sees everything', ADMIN: 'Manages this organization and its agent settings; never approves',
};
const ORDER = ['BUYER', 'SUPPLIER', 'SUPER_ADMIN', 'ADMIN'];
const ROLE_NAME: Record<string, string> = { SUPER_ADMIN: 'SBN admin', ADMIN: 'Organization admin' };

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
    const list = (roles.data?.accounts ?? []).filter((a) => ORDER.includes(a.role)).sort((x, y) => ORDER.indexOf(x.role) - ORDER.indexOf(y.role) || x.email.localeCompare(y.email));
    return (
      <div className="login-wrap">
        <div className="card login-card stack" style={{ maxWidth: 560 }}>
          <div className="brand"><Icon name="logo" /><span>SourceLoop</span></div>
          <div><h1 id="login-h">Choose a role to continue</h1><p className="muted">Demo environment with made-up data. No password needed: pick who you want to be.</p></div>
          {error && <div className="callout bad" role="alert"><Icon name="alert" /><div>{error}</div></div>}
          <div className="stack" role="list" style={{ gap: 8 }}>
            {list.map((a) => (
              <button key={a.email} role="listitem" className="btn" style={{ justifyContent: 'flex-start', height: 'auto', padding: '10px 12px', textAlign: 'left' }}
                disabled={busy} onClick={() => void pick(a)}>
                <span><b>{ROLE_NAME[a.role] ?? roleTitle(a.role)}</b><span className="muted"> · {a.org}</span>
                  <span className="muted" style={{ display: 'block', fontWeight: 400 }}>{ROLE_HINT[a.role] ?? ''}</span></span>
              </button>
            ))}
          </div>
          <button className="btn ghost" type="button" onClick={() => setUsePassword(true)}>Sign in with a password instead</button>
        </div>
      </div>
    );
  }
  return (
    <div className="login-wrap">
      <form className="card login-card stack" onSubmit={(e) => void submit(e)} aria-labelledby="login-h">
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
      </form>
    </div>
  );
}
