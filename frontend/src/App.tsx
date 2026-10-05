import React, { useMemo, useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { Topbar } from './components/Topbar';
import { TopbarOverrideContext, type Crumb, type TopbarOverride } from './components/topbarOverride';
import { Icons } from './components/icons';
import { SearchModal } from './components/SearchModal';
import { useQueue, useQueueConfig } from './api/queue';
import { useFleetData } from './api/fleet';
import { AuthGate } from './auth/AuthGate';
import { apiFetch } from './api/client';
import { getSession, type Role } from './auth/session';
import { CustomerPortal } from './screens/CustomerPortal';
import { PluginPage } from './screens/PluginPage';
import { InventoryBanner } from './components/InventoryBanner';
import { LEGACY_REDIRECTS } from './plugins/registry';

import { QueueScreen }     from './screens/QueueScreen';
import { FleetScreen }     from './screens/FleetScreen';
import { PrinterConsoleScreen } from './screens/PrinterConsoleScreen';
import { OrdersScreen }    from './screens/OrdersScreen';
import { NewJobScreen }    from './screens/NewJobScreen';
import { NewOrderScreen }  from './screens/NewOrderScreen';
import { JobDetailScreen } from './screens/JobDetailScreen';
import { EditJobScreen }    from './screens/EditJobScreen';
import { FilesScreen }          from './screens/FilesScreen';
import { PrinterFilesScreen }   from './screens/PrinterFilesScreen';
import { AlarmsScreen }         from './screens/AlarmsScreen';
import { CameraWallScreen }    from './screens/CameraWallScreen';
import { useAlarmSummary }      from './api/alarms';
import { SettingsScreen }       from './screens/SettingsScreen';
import { ProjectsScreen }       from './screens/ProjectsScreen';
import { ProjectBuilderScreen } from './screens/ProjectBuilderScreen';
import { ProjectDetailScreen }  from './screens/ProjectDetailScreen';
import { HistoryScreen }        from './screens/HistoryScreen';
import { AnalyticsScreen }      from './screens/AnalyticsScreen';
import { SharedProjectScreen }  from './screens/SharedProjectScreen';
import { CustomersScreen }      from './screens/CustomersScreen';
import { CustomerDetailScreen } from './screens/CustomerDetailScreen';

type SvcStatus = 'up' | 'down' | 'unconfigured';

function ServiceBubble({ name, status }: { name: string; status: SvcStatus }) {
  const dot = status === 'up' ? 'var(--ok)' : status === 'down' ? 'var(--err)' : 'var(--idle)';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-4)', userSelect: 'none' }}>
      <span style={{ width: 7, height: 7, borderRadius: '50%', background: dot, flexShrink: 0 }} />
      {name}
    </span>
  );
}

function useServicesHealth() {
  const [laminusStatus, setLaminusStatus] = useState<SvcStatus>('unconfigured');

  useEffect(() => {
    let alive = true;
    function poll() {
      apiFetch('/api/v1/laminus/catalog/status')
        .then(r => r.ok ? r.json() : Promise.reject())
        .then((d: { laminus_configured: boolean; laminus: unknown }) => {
          if (!alive) return;
          if (!d.laminus_configured) setLaminusStatus('unconfigured');
          else setLaminusStatus(d.laminus !== null ? 'up' : 'down');
        })
        .catch(() => { if (alive) setLaminusStatus('down'); });
    }
    poll();
    const id = setInterval(poll, 30000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  return { laminusStatus };
}

const BOTTOM_NAV_ITEMS = [
  { to: '/queue',    label: 'Queue',    icon: 'queue'    },
  { to: '/fleet',    label: 'Fleet',    icon: 'fleet'    },
  { to: '/projects', label: 'Projects', icon: 'layers'   },
  { to: '/settings', label: 'Settings', icon: 'settings' },
] as const;

// Destinations the four-slot bar has no room for; reachable from its "More" sheet.
const MORE_NAV_ITEMS = [
  { to: '/customers', label: 'Customers', icon: 'user'  },
  { to: '/files',     label: 'Files',     icon: 'files' },
  { to: '/printer-files', label: 'Printer files', icon: 'printer' },
  { to: '/wall',      label: 'Camera wall', icon: 'camera' },
  { to: '/alarms',    label: 'Alarms',    icon: 'alert' },
  { to: '/history',   label: 'History',   icon: 'clock' },
  { to: '/analytics', label: 'Analytics', icon: 'chart' },
] as const;

function BottomNav({ queueCounts }: { queueCounts: { active: number; pending: number; blocked: number } }) {
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => setMoreOpen(false), [location.pathname]);
  const navigate = useNavigate();
  const path = '/' + location.pathname.split('/').filter(Boolean)[0];
  const total = queueCounts.active + queueCounts.pending + queueCounts.blocked;
  return (
    <>
    {moreOpen && (
      <div className="more-sheet" role="menu" aria-label="More destinations">
        {MORE_NAV_ITEMS.map(item => (
          <button key={item.to} role="menuitem" className={`more-sheet-item ${path === item.to ? 'active' : ''}`}
                  onClick={() => navigate(item.to)}>
            {Icons[item.icon]}
            <span>{item.label}</span>
          </button>
        ))}
      </div>
    )}
    <nav className="bottom-nav">
      {BOTTOM_NAV_ITEMS.map(item => (
        <button
          key={item.to}
          className={`bottom-nav-item ${path === item.to ? 'active' : ''}`}
          onClick={() => navigate(item.to)}
        >
          {Icons[item.icon]}
          {item.to === '/queue' && total > 0 && (
            <span className="bn-count">{total}</span>
          )}
          <span>{item.label}</span>
        </button>
      ))}
      <button className={`bottom-nav-item ${moreOpen || MORE_NAV_ITEMS.some(i => i.to === path) ? 'active' : ''}`}
              aria-expanded={moreOpen} aria-haspopup="menu" onClick={() => setMoreOpen(o => !o)}>
        {Icons.more}
        <span>More</span>
      </button>
    </nav>
    </>
  );
}

function AppShell() {
  const { jobs } = useQueue();
  const { config: queueConfig } = useQueueConfig();
  const [printers] = useFleetData();
  const alarmSummary = useAlarmSummary();
  const queueCounts = useMemo(() => ({
    active:  jobs.filter(j => ['printing','paused','slicing','uploading'].includes(j.status)).length,
    pending: jobs.filter(j => j.status === 'queued').length,
    blocked: jobs.filter(j => j.status === 'blocked').length,
  }), [jobs]);
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const { laminusStatus } = useServicesHealth();
  const [topbarOverride, setTopbarOverride] = useState<TopbarOverride | null>(null);

  useEffect(() => {
    if (location.pathname.startsWith('/settings')) {
      setNavCollapsed(false);
    }
  }, [location.pathname]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen(true);
      }
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const screenConfig: Record<string, { title: string; crumbs: Crumb[]; actions?: React.ReactNode }> = {
    '/queue':      { title: 'Job queue',        crumbs: ['Workshop'],
                     actions: <><button className="btn sm">{Icons.refresh} Resync</button>
                                <button className="btn primary sm" onClick={() => navigate('/queue/new')}>{Icons.plus} New job</button></> },
    '/queue/new':  { title: 'New job',           crumbs: ['Workshop', 'Job queue'] },
    '/fleet':      { title: 'Fleet',             crumbs: ['Workshop'],
                     actions: <button className="btn sm">{Icons.refresh} Resync</button> },
    '/orders':     { title: 'Orders',            crumbs: ['Workshop'],
                     actions: <button className="btn primary sm" onClick={() => navigate('/orders/new')}>{Icons.plus} New order</button> },
    '/orders/new': { title: 'New order',         crumbs: ['Workshop', 'Orders'] },
    '/orders/edit': { title: 'Edit order',        crumbs: ['Workshop', 'Orders'] },
    '/jobs/detail': { title: 'Job details',       crumbs: ['Workshop', 'Job queue'] },
    '/jobs/edit':   { title: 'Edit job settings', crumbs: ['Workshop', 'Job queue'] },
    '/files':      { title: 'Model library',     crumbs: ['Workshop'],
                     actions: <button className="btn primary sm">{Icons.upload} Upload</button> },
    '/printer-files': { title: 'Printer files',    crumbs: ['Workshop'] },
    '/alarms':       { title: 'Alarms',           crumbs: ['Workshop'] },
    '/wall':         { title: 'Camera wall',      crumbs: ['Workshop'] },
    '/projects':        { title: 'Projects',      crumbs: ['Workshop'],
                         actions: <button className="btn primary sm" onClick={() => navigate('/projects/new')}>{Icons.plus} New project</button> },
    '/projects/new':    { title: 'New project',  crumbs: ['Workshop', { label: 'Projects', to: '/projects' }] },
    '/projects/detail': { title: 'Project',      crumbs: ['Workshop', { label: 'Projects', to: '/projects' }] },
    '/projects/edit':   { title: 'Edit project', crumbs: ['Workshop', { label: 'Projects', to: '/projects' }] },
    '/customers':        { title: 'Customers', crumbs: ['Workshop'],
                           actions: <button className="btn primary sm" onClick={() => navigate('/customers?new=1')}>{Icons.plus} New customer</button> },
    '/customers/detail': { title: 'Customer',  crumbs: ['Workshop', { label: 'Customers', to: '/customers' }] },
    '/history':    { title: 'History',           crumbs: ['Workshop'] },
    '/analytics':  { title: 'Analytics',         crumbs: ['Workshop'] },
    '/settings':   { title: 'Settings',          crumbs: [] },
  };

  const segments = location.pathname.split('/').filter(Boolean);
  const path = segments[0] === 'settings'
    ? '/settings'                       // every settings sub-page shares one top-bar config
    : segments[0] === 'plugins'
    ? '/settings'                       // plugin pages are settings pages too
    : segments[0] === 'orders' && segments[2] === 'edit'
    ? '/orders/edit'
    : segments[0] === 'jobs' && segments[2] === 'edit'
    ? '/jobs/edit'
    : segments[0] === 'jobs' && segments.length >= 2
    ? '/jobs/detail'
    : segments[0] === 'projects' && segments.length >= 2
    ? (segments[1] === 'new' ? '/projects/new'
       : segments.length >= 3 && segments[2] === 'edit' ? '/projects/edit'
       : '/projects/detail')
    : segments[0] === 'customers' && segments.length >= 2
    ? '/customers/detail'
    : '/' + segments.slice(0, 2).join('/');
  const baseCfg = screenConfig[path] ?? screenConfig['/queue'];
  const override = topbarOverride?.path === location.pathname ? topbarOverride : null;
  const cfg = {
    ...baseCfg,
    title: override?.title ?? baseCfg.title,
    crumbs: override?.crumbs ?? baseCfg.crumbs,
  };

  return (
    <div className="app" data-nav={navCollapsed ? 'collapsed' : 'expanded'}>
      <Sidebar queueCounts={queueCounts} alarmCount={alarmSummary.count} alarmWorst={alarmSummary.worst}
               operatorName={queueConfig?.operator_name ?? null} printerCount={printers.length}
               collapsed={navCollapsed} onToggle={() => setNavCollapsed(c => !c)} />
      <div className="main">
      <BottomNav queueCounts={queueCounts} />
        <Topbar title={cfg.title} crumbs={cfg.crumbs} actions={cfg.actions} />
        <div className="content" data-density="balanced">
          <InventoryBanner />
          <TopbarOverrideContext.Provider value={setTopbarOverride}>
          <Routes>
            <Route path="/"             element={<Navigate to="/queue" replace />} />
            <Route path="/queue"        element={<QueueScreen />} />
            <Route path="/queue/new"    element={<NewJobScreen />} />
            <Route path="/fleet"        element={<FleetScreen />} />
            <Route path="/fleet/:id/console" element={<PrinterConsoleScreen />} />
            <Route path="/orders"       element={<OrdersScreen />} />
            <Route path="/orders/new"   element={<NewOrderScreen />} />
            <Route path="/orders/:id/edit" element={<NewOrderScreen />} />
            <Route path="/jobs/:id"        element={<JobDetailScreen />} />
            <Route path="/jobs/:id/edit"   element={<EditJobScreen />} />
            <Route path="/files"           element={<FilesScreen />} />
            <Route path="/printer-files"   element={<PrinterFilesScreen />} />
            <Route path="/alarms"          element={<AlarmsScreen />} />
            <Route path="/wall"            element={<CameraWallScreen />} />
            <Route path="/projects"            element={<ProjectsScreen />} />
            <Route path="/projects/new"        element={<ProjectBuilderScreen />} />
            <Route path="/projects/:id"        element={<ProjectDetailScreen />} />
            <Route path="/projects/:id/edit"   element={<ProjectBuilderScreen />} />
            <Route path="/customers"      element={<CustomersScreen />} />
            <Route path="/customers/:id"  element={<CustomerDetailScreen />} />
            <Route path="/history"        element={<HistoryScreen />} />
            <Route path="/analytics"      element={<AnalyticsScreen />} />
            {Object.entries(LEGACY_REDIRECTS).map(([from, to]) => <Route key={from} path={from} element={<Navigate to={to} replace />} />)}
            <Route path="/plugins/:id"      element={<PluginPage />} />
            <Route path="/plugins/:id/:tab" element={<PluginPage />} />
            {/* Customers used to live under Settings. */}
            <Route path="/settings/customers" element={<Navigate to="/customers" replace />} />
            <Route path="/settings/*"     element={<SettingsScreen />} />
            <Route path="*"               element={<Navigate to="/queue" replace />} />
          </Routes>
          </TopbarOverrideContext.Provider>
        </div>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 16,
          height: 26, padding: '0 18px',
          background: 'var(--bg-0)', borderTop: '1px solid var(--border-1)',
          flexShrink: 0,
        }}>
          <ServiceBubble name="Laminus" status={laminusStatus} />
        </div>
      </div>
      <SearchModal open={searchOpen} onClose={() => setSearchOpen(false)} jobs={jobs} printers={printers} />
    </div>
  );
}

/** Customers get the portal; everyone else (local admin, staff API key) the full app.
 *  Waits for the role so staff-only hooks never fire under a customer session. */
function RoleSwitch() {
  const [role, setRole] = useState<Role | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    getSession().then(s => { if (alive) setRole(s?.role ?? null); });
    return () => { alive = false; };
  }, []);
  if (role === undefined) return null;
  return role === 'customer' ? <CustomerPortal /> : <AppShell />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/share/:token" element={<SharedProjectScreen />} />
        <Route
          path="/*"
          element={
            <AuthGate>
              <RoleSwitch />
            </AuthGate>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
