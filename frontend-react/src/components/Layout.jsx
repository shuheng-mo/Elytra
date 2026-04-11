import { Sidebar } from './Sidebar';

export function Layout({ currentPage, onNavigate, children }) {
  return (
    <div className="flex h-screen w-screen bg-[var(--bg-primary)] text-[var(--text-primary)] font-body">
      <Sidebar currentPage={currentPage} onNavigate={onNavigate} />
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
