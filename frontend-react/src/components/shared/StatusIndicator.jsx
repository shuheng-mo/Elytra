import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { cn } from '../../lib/utils';

export function StatusIndicator() {
  const [status, setStatus] = useState('checking');

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        await api.health();
        if (!cancelled) setStatus('online');
      } catch {
        if (!cancelled) setStatus('offline');
      }
    };
    check();
    const timer = setInterval(check, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const label =
    status === 'online' ? 'Backend 在线' : status === 'offline' ? 'Backend 离线' : '检查中…';
  const dotColor =
    status === 'online'
      ? 'bg-[var(--accent-success)]'
      : status === 'offline'
        ? 'bg-[var(--accent-error)]'
        : 'bg-[var(--text-muted)]';

  return (
    <div className="flex items-center gap-2">
      <span className={cn('h-2 w-2 rounded-full', dotColor)} />
      <span className="text-xs text-[var(--text-secondary)]">{label}</span>
    </div>
  );
}
