import {
  Search,
  Database,
  History,
  BarChart3,
  Settings as SettingsIcon,
  Plug,
} from 'lucide-react';
import { PAGES } from '../lib/constants';
import { cn } from '../lib/utils';
import { useSettings } from '../lib/settings';
import { StatusIndicator } from './shared/StatusIndicator';

const NAV_ITEMS = [
  { key: PAGES.QUERY, label: '查询', Icon: Search },
  { key: PAGES.CONNECTORS, label: '数据接入', Icon: Plug },
  { key: PAGES.SCHEMA, label: 'Schema', Icon: Database },
  { key: PAGES.HISTORY, label: '历史', Icon: History },
  { key: PAGES.AUDIT, label: '审计', Icon: BarChart3 },
  { key: PAGES.SETTINGS, label: '设置', Icon: SettingsIcon },
];

export function Sidebar({ currentPage, onNavigate }) {
  const { settings } = useSettings();
  // On light theme the white logo disappears, swap to the navy/cyan version.
  const logoSrc =
    settings.theme === 'light' ? '/elytra-logo-hex-icon.svg' : '/elytra-logo-hex-white.svg';

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-[var(--border-color)] bg-[var(--bg-secondary)]">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-[var(--border-subtle)]">
        <img
          src={logoSrc}
          alt="Elytra"
          className="h-10 w-10 elytra-logo-glow"
        />
        <div className="flex flex-col">
          <span className="text-lg font-semibold tracking-[0.12em] text-[var(--text-primary)]">
            ELYTRA
          </span>
          <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            NL2SQL Analytics
          </span>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3">
        {NAV_ITEMS.map(({ key, label, Icon }) => {
          const active = key === currentPage;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onNavigate(key)}
              className={cn(
                'group flex w-full items-center gap-3 px-5 py-2.5 text-sm font-medium transition-colors',
                active
                  ? 'bg-[var(--bg-tertiary)] text-[var(--text-primary)] border-l-2 border-[var(--accent-primary)]'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]/50 hover:text-[var(--text-primary)] border-l-2 border-transparent'
              )}
            >
              <Icon className="h-4 w-4" />
              <span>{label}</span>
            </button>
          );
        })}
      </nav>

      {/* Footer: connection status */}
      <div className="border-t border-[var(--border-subtle)] px-5 py-3">
        <StatusIndicator />
      </div>
    </aside>
  );
}
