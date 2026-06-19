import React, { useMemo, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { Topbar } from './components/Topbar';
import { Icons } from './components/icons';
import { useQueue, useQueueConfig } from './api/queue';
import { useOrders } from './api/orders';
import { useFleetData } from './api/fleet';

import { QueueScreen }     from './screens/QueueScreen';
import { FleetScreen }     from './screens/FleetScreen';
import { OrdersScreen }    from './screens/OrdersScreen';
import { NewJobScreen }    from './screens/NewJobScreen';
import { NewOrderScreen }  from './screens/NewOrderScreen';
import { JobDetailScreen } from './screens/JobDetailScreen';
import { EditJobScreen }    from './screens/EditJobScreen';
import { FilesScreen }     from './screens/FilesScreen';
import { SettingsScreen }  from './screens/SettingsScreen';

function AppShell() {
  const { jobs } = useQueue();
  const { config: queueConfig } = useQueueConfig();
  const [printers] = useFleetData();
  const queueCounts = useMemo(() => ({
    active:  jobs.filter(j => ['printing','paused','slicing','uploading'].includes(j.status)).length,
    pending: jobs.filter(j => j.status === 'queued').length,
    blocked: jobs.filter(j => j.status === 'blocked').length,
  }), [jobs]);
  const { orders } = useOrders();
  const ordersOpen = useMemo(() => orders.filter(o => o.status !== 'complete').length, [orders]);
  const [navCollapsed, setNavCollapsed] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  const screenConfig: Record<string, { title: string; crumbs: string[]; actions?: React.ReactNode }> = {
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
    '/settings':   { title: 'Settings',          crumbs: [] },
  };

  const segments = location.pathname.split('/').filter(Boolean);
  const path = segments[0] === 'orders' && segments[2] === 'edit'
    ? '/orders/edit'
    : segments[0] === 'jobs' && segments[2] === 'edit'
    ? '/jobs/edit'
    : segments[0] === 'jobs' && segments.length >= 2
    ? '/jobs/detail'
    : '/' + segments.slice(0, 2).join('/');
  const cfg = screenConfig[path] ?? screenConfig['/queue'];

  return (
    <div className="app" data-nav={navCollapsed ? 'collapsed' : 'expanded'}>
      <Sidebar queueCounts={queueCounts} ordersOpen={ordersOpen}
               operatorName={queueConfig?.operator_name ?? null} printerCount={printers.length}
               collapsed={navCollapsed} onToggle={() => setNavCollapsed(c => !c)} />
      <div className="main">
        <Topbar title={cfg.title} crumbs={cfg.crumbs} actions={cfg.actions} />
        <div className="content" data-density="balanced">
          <Routes>
            <Route path="/"             element={<Navigate to="/queue" replace />} />
            <Route path="/queue"        element={<QueueScreen />} />
            <Route path="/queue/new"    element={<NewJobScreen />} />
            <Route path="/fleet"        element={<FleetScreen />} />
            <Route path="/orders"       element={<OrdersScreen />} />
            <Route path="/orders/new"   element={<NewOrderScreen />} />
            <Route path="/orders/:id/edit" element={<NewOrderScreen />} />
            <Route path="/jobs/:id"        element={<JobDetailScreen />} />
            <Route path="/jobs/:id/edit"   element={<EditJobScreen />} />
            <Route path="/files"        element={<FilesScreen />} />
            <Route path="/settings/*"   element={<SettingsScreen />} />
            <Route path="*"             element={<Navigate to="/queue" replace />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
