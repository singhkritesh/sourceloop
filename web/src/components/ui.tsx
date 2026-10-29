import { Component, useEffect, useRef, type ErrorInfo, type ReactNode } from 'react';
import { ApiError } from '../api/http';
import { friendlyMessage, isLostOnRestart } from '../lib/errors';
import { plainStatus } from '../lib/friendly';
import { useUi } from '../state/ui';
import { Icon } from './Icon';

export function Skeleton({ h = 16, w = '100%', style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden="true" />;
}
export function SkeletonList({ rows = 4, h = 44 }: { rows?: number; h?: number }) {
  return (
    <div className="stack" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} h={h} />)}
    </div>
  );
}
export function Empty({ icon = 'folder', title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} />
      <b className="text-2">{title}</b>
      {children && <div>{children}</div>}
    </div>
  );
}
export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="callout bad" role="alert">
      <Icon name="alert" />
      <div className="grow">
        <b>{e ? (e.status === 403 ? 'Not permitted' : e.status === 404 ? 'Not found' : 'Request failed') : 'Something went wrong'}</b>
        <div>{e ? friendlyMessage(e) : error instanceof Error ? error.message : String(error)}{e?.requestId && <span className="muted"> · req {e.requestId.slice(0, 8)}</span>}</div>
      </div>
      {onRetry && <button className="btn sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Badge({ tone = '', children, pulse }: { tone?: '' | 'ok' | 'warn' | 'bad' | 'info' | 'accent'; children: ReactNode; pulse?: boolean }) {
  return <span className={`badge ${tone} ${pulse ? 'pulse' : ''}`}><span className="dot" />{children}</span>;
}

const STATUS_TONE: Record<string, '' | 'ok' | 'warn' | 'bad' | 'info' | 'accent'> = {
  RUNNING: 'accent', DONE: 'ok', ESCALATED: 'warn', STOPPED: 'bad', FAILED_RECOVERABLE: 'bad', STALLED: 'bad', BUDGET_EXCEEDED: 'bad', DRAFT: '',
};
export const StatusBadge = ({ status, reason, outcome }: { status: string; reason?: string | null; outcome?: string | null }) => {
  const ps = plainStatus(status, outcome, isLostOnRestart({ status, reason }));
  return <Badge tone={ps.tone || (STATUS_TONE[status] ?? '')} pulse={status === 'RUNNING'}>{ps.label}</Badge>;
};

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(_e: Error, _i: ErrorInfo) { /* never log props/state: may contain business data */ }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page"><div className="card pad stack" role="alert">
        <h2>This screen crashed</h2>
        <p className="muted">{this.state.error.message}</p>
        <div className="row"><button className="btn primary" onClick={() => this.setState({ error: null })}>Try again</button><a className="btn" href="/">Home</a></div>
      </div></div>
    );
  }
}

export function Toasts() {
  const { toasts, dismiss } = useUi();
  return (
    <div className="toasts" aria-live="polite" role="status">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          <div className="grow">{t.text}</div>
          <button className="btn ghost icon sm" aria-label="Dismiss" onClick={() => dismiss(t.id)}><Icon name="x" /></button>
        </div>
      ))}
    </div>
  );
}

/** Modal with focus trap, Esc to close, focus restore. */
export function Dialog({ title, onClose, children, footer, wide, labelId = 'dlg-title' }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean; labelId?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useTrap(ref, onClose);
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`dialog ${wide ? 'lg' : ''}`} role="dialog" aria-modal="true" aria-labelledby={labelId} ref={ref}>
        <div className="dialog-head"><h2 id={labelId}>{title}</h2></div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ title, subtitle, onClose, children, footer }: { title: ReactNode; subtitle?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useTrap(ref, onClose);
  return (
    <div className="drawer-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Details" ref={ref}>
        <div className="drawer-head row">
          <div className="grow"><h2>{title}</h2>{subtitle && <div className="muted">{subtitle}</div>}</div>
          <button className="btn ghost icon" aria-label="Close (Esc)" onClick={onClose}><Icon name="x" /></button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-foot">{footer}</div>}
      </aside>
    </div>
  );
}

const FOCUSABLE = 'a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
function useTrap(ref: React.RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const first = el?.querySelector<HTMLElement>('input,textarea,select');
    if (first) first.focus();
    else if (el) { el.tabIndex = -1; el.focus(); }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); return; }
      if (e.key !== 'Tab' || !el) return;
      const f = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!f.length) return;
      const a = f[0] as HTMLElement, z = f[f.length - 1] as HTMLElement;
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    };
    document.addEventListener('keydown', onKey, true);
    return () => { document.removeEventListener('keydown', onKey, true); prev?.focus?.(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

export function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} className="switch" onClick={() => onChange(!checked)} />;
}

export const fmtTime = (iso?: string | null): string => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};
