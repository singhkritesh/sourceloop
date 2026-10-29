import { useEffect, useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { auth } from '../api/endpoints';
import { ApiError } from '../api/http';
import { Icon } from '../components/Icon';
import { useAuth } from '../state/auth';

export function Login() {
  const setMe = useAuth((s) => s.setMe);
  const nav = useNavigate();
  const loc = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lockSecs, setLockSecs] = useState(0);

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
        <details className="hint">
          <summary style={{ cursor: 'pointer' }}>Which account should I use?</summary>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            <li><b>buyer@sourceloop.test</b>: start and follow a sourcing project</li>
            <li><b>compliance@</b>, <b>quality@</b>, <b>legal@</b>, <b>finance@sourceloop.test</b>: the specialists a sample project waits on after the buyer</li>
            <li><b>supplier1@sourceloop.test</b>: see a project from the supplier side</li>
            <li><b>superadmin@sourceloop.test</b>: everything, including settings</li>
          </ul>
          <p style={{ margin: '6px 0 0' }}>Passwords were generated when the app was first set up. In a terminal, run <code>cat data/demo-creds.txt</code> in the project folder.</p>
        </details>
      </form>
    </div>
  );
}
