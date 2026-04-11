import { useCallback, useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { HistoryTable } from '../shared/HistoryTable';
import { Input } from '../ui/input';
import { Button } from '../ui/button';
import { RefreshCw, Download } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';
import { Skeleton } from '../ui/skeleton';
import { HISTORY_LIMITS } from '../../lib/constants';
import { getOrCreateSessionId } from '../../lib/utils';
import { exportHistoryToXlsx, exportHistoryToCsv } from '../../lib/export';

export function HistoryPage({ onReuseQuery }) {
  const [sessionFilter, setSessionFilter] = useState('');
  const [limit, setLimit] = useState(20);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = { limit };
      if (sessionFilter) params.session_id = sessionFilter;
      const data = await api.getHistory(params);
      setHistory(data.history || []);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  }, [sessionFilter, limit]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">查询历史</h1>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={history.length === 0 || loading}
            onClick={() => exportHistoryToXlsx(history, sessionFilter || null)}
            title="导出当前历史条目到 Excel"
          >
            <Download className="h-3.5 w-3.5" /> 导出 Excel
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={history.length === 0 || loading}
            onClick={() => exportHistoryToCsv(history, sessionFilter || null)}
            title="导出当前历史条目到 CSV"
          >
            <Download className="h-3.5 w-3.5" /> 导出 CSV
          </Button>
          <Button variant="secondary" size="sm" onClick={load}>
            <RefreshCw className="h-3.5 w-3.5" /> 刷新
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Input
          placeholder={`Session ID (留空查全部，当前会话：${getOrCreateSessionId()})`}
          value={sessionFilter}
          onChange={(e) => setSessionFilter(e.target.value)}
          className="max-w-md"
        />
        <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v))}>
          <SelectTrigger className="w-[140px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {HISTORY_LIMITS.map((n) => (
              <SelectItem key={n} value={String(n)}>
                最近 {n} 条
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {loading && (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {error && (
        <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
          {error}
        </div>
      )}

      {!loading && !error && (
        <HistoryTable history={history} onReuseQuery={onReuseQuery} />
      )}
    </div>
  );
}
