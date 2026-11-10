import React from 'react';
import { CostSettings } from '../components/CostSettings';
import { useLocation, useNavigate } from 'react-router-dom';
import { Icons } from '../components/icons';
import { PageHeader } from '../components/settingsUi';
import { PrinterModelsPanel } from './PrinterModelsPanel';
import { FilamentInventoryPage } from './FilamentInventoryPage';
import { PluginsPage } from './PluginsPage';
import { CapabilitiesPage } from './CapabilitiesPage';
import { navigablePlugins, usePlugins } from '../api/plugins';
import { TagsPage } from './settings/TagsPage';
import { PrintDefaultsPage } from './settings/PrintDefaultsPage';
import { AboutPage } from './settings/AboutPage';
import { FleetBackupPage } from './settings/FleetBackupPage';
import { WebhookPage } from './settings/WebhookPage';
import { MaintenancePage } from './settings/MaintenancePage';
import { ApiKeysPage } from './settings/ApiKeysPage';
import { AdminAccountPage } from './settings/AdminAccountPage';
import { SettingsIcons } from './settings/icons';

// =========================================================================
// Local icons not in the main Icons set
// =========================================================================

function PrinterModelsPage() {
  return (
    <div className="col gap-4">
      <PageHeader title="Printer models" sub="Only enabled models are offered when adding a printer or choosing which machines a G-code file fits." />
      <PrinterModelsPanel />
    </div>
  );
}

type PageId = 'tags' | 'print' | 'costs' | 'printer-models' | 'maintenance' | 'inventory' | 'plugins' | 'capabilities' | 'webhook' | 'fleet-backup' | 'api-keys' | 'admin-account' | 'about';

interface NavItem {
  id: PageId;
  label: string;
  icon: React.ReactElement;
  sub: string;
}

interface NavSection {
  label: string;
  items: NavItem[];
}

const PAGE_IDS: PageId[] = ['tags', 'print', 'costs', 'printer-models', 'maintenance', 'inventory', 'plugins', 'capabilities', 'webhook', 'fleet-backup', 'api-keys', 'admin-account', 'about'];

function pageFromPath(pathname: string): PageId {
  const seg = pathname.replace(/^\/settings\/?/, '').split('/')[0];
  return (PAGE_IDS as string[]).includes(seg) ? (seg as PageId) : 'tags';
}

export function SettingsScreen() {
  const location = useLocation();
  const navigate = useNavigate();
  const activePage = pageFromPath(location.pathname);
  const setActivePage = (id: PageId) => navigate(`/settings/${id}`);
  const { plugins } = usePlugins();
  // A plugin that asks for its own page gets a sidebar entry only while it is installed, running and enabled.
  const pluginPages = navigablePlugins(plugins).filter(p => p.ui.nav_placement === 'settings');

  const sections: NavSection[] = [
    {
      label: 'Workshop',
      items: [
        { id: 'tags',          label: 'Tags',           icon: SettingsIcons.tag,     sub: 'Manage labels across files & jobs' },
        { id: 'print',         label: 'Print defaults', icon: Icons.printer,         sub: 'Queue interval & profile rescan' },
        { id: 'costs',         label: 'Costs',          icon: Icons.layers,          sub: 'Machine & labour rates' },
        { id: 'printer-models', label: 'Printer models', icon: Icons.printer,       sub: 'Which models appear when adding a printer' },
        { id: 'maintenance',   label: 'Maintenance',    icon: SettingsIcons.maintenance, sub: 'Recurring printer upkeep & schedules' },
      ],
    },
    {
      label: 'Integrations',
      items: [
        { id: 'inventory' as PageId, label: 'Filament inventory', icon: SettingsIcons.inventory, sub: 'Provider, deduction & low-stock alerts' },
        { id: 'plugins' as PageId,   label: 'Plugins',            icon: Icons.layers,           sub: 'Installed plugins & their settings' },
        { id: 'capabilities' as PageId, label: 'Capabilities',    icon: Icons.layers,           sub: 'Which plugin serves each capability' },
        { id: 'webhook' as PageId, label: 'Webhooks',          icon: SettingsIcons.webhook,  sub: 'Job state notifications' },
      ],
    },
    {
      label: 'Data',
      items: [
        { id: 'fleet-backup',  label: 'Fleet backup',   icon: SettingsIcons.backup,  sub: 'Export & import printer configs' },
      ],
    },
    {
      label: 'Security',
      items: [
        { id: 'api-keys',      label: 'API Keys',       icon: SettingsIcons.apikey,  sub: 'Manage app access & scopes' },
        { id: 'admin-account', label: 'Admin account',  icon: SettingsIcons.apikey,  sub: 'Password, local sign-in, recovery' },
      ],
    },
    {
      label: 'System',
      items: [
        { id: 'about',         label: 'About',          icon: SettingsIcons.info,    sub: 'Version' },
      ],
    },
  ];

  return (
    <div>
      {/* mobile tab bar */}
      <nav className="settings-tabs" style={{ gap: 4, overflowX: 'auto', paddingBottom: 12, marginBottom: 8 }}>
        {pluginPages.map(p => (
          <button key={p.id} onClick={() => navigate(`/plugins/${p.id}`)} className="settings-tab" style={{ whiteSpace: 'nowrap' }}>
            {p.ui.nav_label}
          </button>
        ))}
        {sections.flatMap(s => s.items).map(item => (
          <button key={item.id}
                  onClick={() => setActivePage(item.id)}
                  className={`settings-tab ${activePage === item.id ? 'active' : ''}`}
                  style={{ whiteSpace: 'nowrap' }}>
            {item.label}
          </button>
        ))}
      </nav>

      {/* page content */}
      {activePage === 'tags'              && <TagsPage />}
      {activePage === 'print'             && <PrintDefaultsPage />}
      {activePage === 'costs'             && <CostSettings />}
      {activePage === 'printer-models'    && <PrinterModelsPage />}
      {activePage === 'maintenance'        && <MaintenancePage />}
      {activePage === 'inventory'         && <FilamentInventoryPage />}
      {activePage === 'plugins'           && <PluginsPage />}
      {activePage === 'capabilities'      && <CapabilitiesPage />}
      {activePage === 'webhook'           && <WebhookPage />}
      {activePage === 'fleet-backup'      && <FleetBackupPage />}
      {activePage === 'api-keys'          && <ApiKeysPage />}
      {activePage === 'admin-account'     && <AdminAccountPage />}
      {activePage === 'about'             && <AboutPage />}
    </div>
  );
}
