import React, { useMemo } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { Topbar } from './components/Topbar';
import { Icons } from './components/icons';
import { JOBS, ORDERS } from './data/mock';

import { QueueScreen }     from './screens/QueueScreen';
import { FleetScreen }     from './screens/FleetScreen';
import { OrdersScreen }    from './screens/OrdersScreen';
import { NewJobScreen }    from './screens/NewJobScreen';
import { NewOrderScreen }  from './screens/NewOrderScreen';
import { FilesScreen }     from './screens/FilesScreen';
import { FilamentsScreen } from './screens/FilamentsScreen';
import { PrintersScreen }  from './screens/PrintersScreen';
import { SettingsScreen }  from './screens/SettingsScreen';

function AppShell() {
  const queueCount = useMemo(() => JOBS.filter(j => j.status === 'queued').length, []);
  const ordersOpen = useMemo(() => ORDERS.filter(o => o.status !== 'complete').length, []);
  const location = useLocation();

  const screenConfig: Record<string, { title: string; crumbs: string[]; actions?: React.ReactNode }> = {
    '/queue':      { title: 'Job queue',        crumbs: ['Workshop'],
                     actions: <><button className="btn sm">{Icons.refresh} Resync</button>
                                <button className="btn primary sm">{Icons.plus} New job</button></> },
    '/queue/new':  { title: 'New job',           crumbs: ['Workshop', 'Job queue'] },
    '/fleet':      { title: 'Fleet',             crumbs: ['Workshop'],
                     actions: <button className="btn sm">{Icons.refresh} Resync</button> },
    '/orders':     { title: 'Orders',            crumbs: ['Workshop'],
                     actions: <button className="btn primary sm">{Icons.plus} New order</button> },
    '/orders/new': { title: 'New order',         crumbs: ['Workshop', 'Orders'] },
    '/files':      { title: 'Model library',     crumbs: ['Workshop'],
                     actions: <button className="btn primary sm">{Icons.upload} Upload</button> },
    '/filaments':  { title: 'Filament library',  crumbs: ['Workshop'],
                     actions: <><button className="btn sm">{Icons.refresh} Sync vendor prices</button>
                                <button className="btn primary sm">{Icons.plus} Add filament</button></> },
    '/printers':   { title: 'Printers',          crumbs: ['Workshop'] },
    '/settings':   { title: 'Settings',          crumbs: [] },
  };

  const path = '/' + location.pathname.split('/').filter(Boolean).slice(0, 2).join('/');
  const cfg = screenConfig[path] ?? screenConfig['/queue'];

  return (
    <div className="app" data-nav="expanded">
      <Sidebar queueCount={queueCount} ordersOpen={ordersOpen} />
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
            <Route path="/files"        element={<FilesScreen />} />
            <Route path="/filaments"    element={<FilamentsScreen />} />
            <Route path="/printers"     element={<PrintersScreen />} />
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
