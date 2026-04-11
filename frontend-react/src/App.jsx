import { useCallback, useState } from 'react';
import { Layout } from './components/Layout';
import { QueryPage } from './components/pages/QueryPage';
import { SchemaExplorer } from './components/pages/SchemaExplorer';
import { HistoryPage } from './components/pages/HistoryPage';
import { AuditDashboard } from './components/pages/AuditDashboard';
import { SettingsPage } from './components/pages/SettingsPage';
import { DataConnectorsPage } from './components/pages/DataConnectorsPage';
import { PAGES } from './lib/constants';
import { TooltipProvider } from './components/ui/tooltip';
import { SettingsProvider } from './lib/settings';

export default function App() {
  const [currentPage, setCurrentPage] = useState(PAGES.QUERY);
  const [currentSource, setCurrentSource] = useState(null);
  const [pendingQuery, setPendingQuery] = useState(null);

  const handleReuseQuery = (query, source) => {
    setPendingQuery(query);
    if (source) setCurrentSource(source);
    setCurrentPage(PAGES.QUERY);
  };

  // Used by DataConnectorsPage to jump to another page with a source preselected.
  const handleJumpTo = useCallback((page, source) => {
    if (source) setCurrentSource(source);
    setCurrentPage(page);
  }, []);

  return (
    <SettingsProvider>
      <TooltipProvider delayDuration={200}>
        <Layout currentPage={currentPage} onNavigate={setCurrentPage}>
          {currentPage === PAGES.QUERY && (
            <QueryPage
              currentSource={currentSource}
              setCurrentSource={setCurrentSource}
              pendingQuery={pendingQuery}
              clearPendingQuery={() => setPendingQuery(null)}
            />
          )}
          {currentPage === PAGES.CONNECTORS && (
            <DataConnectorsPage onJumpTo={handleJumpTo} />
          )}
          {currentPage === PAGES.SCHEMA && (
            <SchemaExplorer
              currentSource={currentSource}
              setCurrentSource={setCurrentSource}
            />
          )}
          {currentPage === PAGES.HISTORY && (
            <HistoryPage onReuseQuery={handleReuseQuery} />
          )}
          {currentPage === PAGES.AUDIT && <AuditDashboard />}
          {currentPage === PAGES.SETTINGS && <SettingsPage />}
        </Layout>
      </TooltipProvider>
    </SettingsProvider>
  );
}
