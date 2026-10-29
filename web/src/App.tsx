import { useEffect, type ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Shell } from './components/Shell';
import { ErrorBoundary, Skeleton } from './components/ui';
import { Approvals } from './pages/Approvals';
import { Login } from './pages/Login';
import { NotFound } from './pages/NotFound';
import { Help } from './pages/Help';
import { Organization } from './pages/Organization';
import { ProjectPage } from './pages/ProjectPage';
import { Projects } from './pages/Projects';
import { SettingsPage } from './pages/Settings';
import { Studio } from './studio/Studio';
import { can, homeFor, useAuth } from './state/auth';

function Guard({ ok, children }: { ok: (r?: import('./api/types').Role) => boolean; children: ReactNode }) {
  const me = useAuth((s) => s.me);
  if (!ok(me?.role)) return <NotFound reason="forbidden" />;
  return <>{children}</>;
}

export function App() {
  const { status, bootstrap, me } = useAuth();
  const loc = useLocation();
  useEffect(() => { void bootstrap(); }, [bootstrap]);

  if (status === 'loading') {
    return <div style={{ padding: 40, maxWidth: 520, margin: '10vh auto' }}><Skeleton h={28} w={180} /><div style={{ height: 16 }} /><Skeleton h={120} /></div>;
  }
  if (status === 'anon') {
    if (loc.pathname !== '/login') {
      return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
    }
    return <Login />;
  }
  return (
    <Shell>
      <ErrorBoundary>
        <Routes>
          <Route path="/" element={<Navigate to={homeFor(me?.role)} replace />} />
          <Route path="/login" element={<Navigate to={homeFor(me?.role)} replace />} />
          <Route path="/projects" element={<Guard ok={can.seeProjects}><Projects /></Guard>} />
          <Route path="/projects/:id" element={<Guard ok={can.seeProjects}><ProjectPage /></Guard>} />
          <Route path="/organization" element={<Guard ok={can.seeOrganization}><Organization /></Guard>} />
          <Route path="/approvals" element={<Guard ok={can.seeApprovals}><Approvals /></Guard>} />
          <Route path="/studio/*" element={<Guard ok={can.seeStudio}><Studio /></Guard>} />
          <Route path="/settings" element={<Guard ok={can.seeSettings}><SettingsPage /></Guard>} />
          <Route path="/help" element={<Help />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </ErrorBoundary>
    </Shell>
  );
}
