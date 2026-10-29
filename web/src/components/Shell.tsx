import type { ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { approvals } from '../api/endpoints';
import { can, useAuth } from '../state/auth';
import { roleTitle } from '../lib/friendly';
import { useUi } from '../state/ui';
import { Icon } from './Icon';
import { Badge, Toasts } from './ui';

export function Shell({ children }: { children: ReactNode }) {
  const me = useAuth((s) => s.me);
  const logout = useAuth((s) => s.logout);
  const { theme, cycleTheme } = useUi();
  const loc = useLocation();
  const wide = loc.pathname.startsWith('/studio') || /^\/projects\/[^/]+/.test(loc.pathname);
  const role = me?.role;
  const pending = useQuery({
    queryKey: ['approvals', 'PENDING', 'count'],
    queryFn: () => approvals.list('PENDING'),
    enabled: can.seeApprovals(role),
    refetchInterval: 20_000,
    select: (d) => d.items.length,
  });
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand" aria-label="SourceLoop home"><Icon name="logo" /><span>SourceLoop</span></Link>
        <nav className="nav" aria-label="Main">
          {can.seeProjects(role) && <NavLink to="/projects">Projects</NavLink>}
          {can.seeApprovals(role) && !(role === 'ADMIN' && me?.org.kind === 'SUPPLIER') && (
            <NavLink to="/approvals">Approvals{!!pending.data && <Badge tone="warn">{pending.data}</Badge>}</NavLink>
          )}
          {can.seeOrganization(role) && <NavLink to="/organization">{role === 'ADMIN' ? 'Organization' : 'Organizations'}</NavLink>}
          {can.seeStudio(role) && <NavLink to="/studio">Studio</NavLink>}
          {can.seeSettings(role) && <NavLink to="/settings">Settings</NavLink>}
        </nav>
        <div className="spacer" />
        <div className="who">
          <span className="email trunc" title={me?.org.name}>{me?.email}</span>
          <Badge tone="accent">{role ? roleTitle(role) : ''}</Badge>
        </div>
        <button className="btn ghost icon" onClick={cycleTheme} aria-label={`Theme: ${theme}. Click to change`} title={`Theme: ${theme}`}>
          <Icon name={theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'auto'} />
        </button>
        <button className="btn ghost icon" onClick={() => void logout()} aria-label="Sign out" title="Sign out"><Icon name="logout" /></button>
      </header>
      <main className="main" id="main"><div className={wide ? 'page wide' : 'page'}>{children}</div></main>
      <Toasts />
    </div>
  );
}
