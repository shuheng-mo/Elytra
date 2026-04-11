import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Database,
  RefreshCw,
  Play,
  Eye,
  Star,
  Info,
  Plus,
  Search,
  Trash2,
  Loader2,
} from 'lucide-react';
import { api } from '../../lib/api';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { Input } from '../ui/input';
import { Skeleton } from '../ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { DataSourceIcon, getDialectLabel } from '../icons/DataSourceIcon';
import { AddConnectorDialog } from '../connectors/AddConnectorDialog';
import { PAGES } from '../../lib/constants';
import { cn } from '../../lib/utils';

function StatusBadge({ connected }) {
  if (connected) {
    return (
      <Badge variant="success" className="gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-success)]" />
        已连接
      </Badge>
    );
  }
  return (
    <Badge variant="error" className="gap-1.5">
      <span className="h-1.5 w-1.5 rounded-full bg-[var(--accent-error)]" />
      未连接
    </Badge>
  );
}

function ConnectorCard({ ds, onJumpTo, onRequestDelete }) {
  return (
    <Card className={cn(!ds.connected && 'opacity-70')}>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-md border border-[var(--border-subtle)] bg-[var(--bg-tertiary)]">
              <DataSourceIcon dialect={ds.dialect} size={24} />
            </div>
            <div className="min-w-0">
              <CardTitle className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{ds.name}</span>
                {ds.is_default && (
                  <span className="inline-flex items-center gap-0.5 rounded bg-[var(--accent-primary-soft)] px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[var(--accent-primary)]">
                    <Star className="h-2.5 w-2.5" />
                    默认
                  </span>
                )}
                {ds.user_managed && (
                  <span className="rounded bg-[var(--accent-warning)]/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[var(--accent-warning)]">
                    UI 创建
                  </span>
                )}
              </CardTitle>
              <CardDescription className="mt-1">{ds.description || '—'}</CardDescription>
            </div>
          </div>
          <StatusBadge connected={ds.connected} />
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-3 border-t border-[var(--border-subtle)] pt-4">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              方言
            </div>
            <div className="mt-0.5 text-sm text-[var(--text-primary)]">
              {getDialectLabel(ds.dialect)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              表数量
            </div>
            <div className="mt-0.5 font-mono text-sm text-[var(--text-primary)]">
              {ds.table_count ?? '—'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              类型
            </div>
            <div className="mt-0.5 text-sm text-[var(--text-primary)]">
              {ds.dialect === 'postgresql' || ds.dialect === 'starrocks'
                ? '远程'
                : ds.dialect === 'hiveql'
                  ? '云数仓'
                  : '本地嵌入'}
            </div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[var(--border-subtle)] pt-3">
          <Button
            variant="secondary"
            size="sm"
            disabled={!ds.connected}
            onClick={() => onJumpTo(PAGES.QUERY, ds.name)}
          >
            <Play className="h-3 w-3" /> 发起查询
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!ds.connected}
            onClick={() => onJumpTo(PAGES.SCHEMA, ds.name)}
          >
            <Eye className="h-3 w-3" /> 浏览 Schema
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto text-[var(--accent-error)] hover:bg-[var(--accent-error)]/10 hover:text-[var(--accent-error)]"
            onClick={() => onRequestDelete(ds)}
          >
            <Trash2 className="h-3 w-3" /> 删除
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function FilterChip({ active, label, count, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors',
        active
          ? 'border-[var(--accent-primary)] bg-[var(--accent-primary-soft)] text-[var(--accent-primary)]'
          : 'border-[var(--border-color)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]'
      )}
    >
      {label}
      <span
        className={cn(
          'rounded-full px-1.5 text-[10px] tabular-nums',
          active ? 'bg-[var(--accent-primary)]/20' : 'bg-[var(--bg-tertiary)]'
        )}
      >
        {count}
      </span>
    </button>
  );
}

export function DataConnectorsPage({ onJumpTo }) {
  const [state, setState] = useState({ loading: true, error: null, datasources: [] });
  const [dialectFilter, setDialectFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await api.getDataSources();
      setState({ loading: false, error: null, datasources: data.datasources || [] });
    } catch (err) {
      setState({ loading: false, error: err.message || String(err), datasources: [] });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Compute dialect counts for filter chips.
  const dialectCounts = useMemo(() => {
    const counts = new Map();
    counts.set('all', state.datasources.length);
    for (const ds of state.datasources) {
      counts.set(ds.dialect, (counts.get(ds.dialect) || 0) + 1);
    }
    return counts;
  }, [state.datasources]);

  const availableDialects = useMemo(
    () => Array.from(new Set(state.datasources.map((d) => d.dialect))),
    [state.datasources]
  );

  // Apply filter + search.
  const filtered = useMemo(() => {
    let list = state.datasources;
    if (dialectFilter !== 'all') {
      list = list.filter((d) => d.dialect === dialectFilter);
    }
    const q = query.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (d) =>
          d.name.toLowerCase().includes(q) ||
          (d.description || '').toLowerCase().includes(q)
      );
    }
    return list;
  }, [state.datasources, dialectFilter, query]);

  const connectedCount = state.datasources.filter((d) => d.connected).length;
  const totalCount = state.datasources.length;
  const userManagedCount = state.datasources.filter((d) => d.user_managed).length;

  const handleCreated = useCallback(() => {
    // Reload the list after a successful add
    load();
  }, [load]);

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.deleteDataSource(deleteTarget.name);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      setState((s) => ({ ...s, error: err.message || String(err) }));
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, load]);

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      {/* Header */}
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Database className="h-6 w-6 text-[var(--accent-primary)]" />
            数据接入
          </h1>
          <p className="mt-1 text-xs text-[var(--text-secondary)]">
            管理 data connectors 接入的数据源 · 静态配置在{' '}
            <code className="rounded bg-[var(--bg-tertiary)] px-1 py-0.5 font-mono text-[10px]">
              config/datasources.yaml
            </code>
            ，UI 创建的存于{' '}
            <code className="rounded bg-[var(--bg-tertiary)] px-1 py-0.5 font-mono text-[10px]">
              datasources.local.yaml
            </code>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={load}>
            <RefreshCw className={cn('h-3.5 w-3.5', state.loading && 'animate-spin')} />
            重新检测
          </Button>
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-3.5 w-3.5" />
            新增数据源
          </Button>
        </div>
      </div>

      {/* Summary strip */}
      {!state.loading && !state.error && (
        <div className="mb-5 flex items-center gap-6 rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)] px-5 py-3 text-sm">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              已注册
            </div>
            <div className="font-semibold text-[var(--text-primary)]">{totalCount}</div>
          </div>
          <div className="h-8 w-px bg-[var(--border-subtle)]" />
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              可用
            </div>
            <div className="font-semibold text-[var(--accent-success)]">
              {connectedCount} / {totalCount}
            </div>
          </div>
          <div className="h-8 w-px bg-[var(--border-subtle)]" />
          <div>
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              UI 创建
            </div>
            <div className="font-semibold text-[var(--accent-warning)]">
              {userManagedCount}
            </div>
          </div>
        </div>
      )}

      {/* Filter + search */}
      {!state.loading && !state.error && totalCount > 0 && (
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <FilterChip
            active={dialectFilter === 'all'}
            label="全部"
            count={dialectCounts.get('all') || 0}
            onClick={() => setDialectFilter('all')}
          />
          {availableDialects.map((d) => (
            <FilterChip
              key={d}
              active={dialectFilter === d}
              label={
                <span className="inline-flex items-center gap-1.5">
                  <DataSourceIcon dialect={d} size={12} />
                  {getDialectLabel(d)}
                </span>
              }
              count={dialectCounts.get(d) || 0}
              onClick={() => setDialectFilter(d)}
            />
          ))}
          <div className="relative ml-auto">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-muted)]" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索名称或描述…"
              className="w-64 pl-8"
            />
          </div>
        </div>
      )}

      {/* Loading */}
      {state.loading && (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-48 rounded-lg" />
          ))}
        </div>
      )}

      {/* Error */}
      {state.error && (
        <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
          {state.error}
        </div>
      )}

      {/* Empty filter result */}
      {!state.loading && !state.error && filtered.length === 0 && totalCount > 0 && (
        <div className="rounded-md border border-dashed border-[var(--border-color)] bg-[var(--bg-secondary)] px-4 py-12 text-center text-sm text-[var(--text-muted)]">
          <Info className="mx-auto mb-2 h-5 w-5" />
          当前筛选条件下没有匹配的数据源
        </div>
      )}

      {/* Grid */}
      {!state.loading && !state.error && filtered.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {filtered.map((ds) => (
            <ConnectorCard
              key={ds.name}
              ds={ds}
              onJumpTo={onJumpTo}
              onRequestDelete={setDeleteTarget}
            />
          ))}
        </div>
      )}

      {/* Add dialog */}
      <AddConnectorDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onCreated={handleCreated}
      />

      {/* Delete confirmation */}
      <Dialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && !deleting && setDeleteTarget(null)}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4 text-[var(--accent-error)]" />
              删除数据源
            </DialogTitle>
            <DialogDescription asChild>
              {deleteTarget?.user_managed ? (
                <p>
                  永久删除：从{' '}
                  <code className="font-mono">config/datasources.local.yaml</code>{' '}
                  移除条目并断开连接。Schema 向量索引保留，不会被清除。
                </p>
              ) : (
                <p className="space-y-1">
                  <span className="block">
                    <strong className="text-[var(--accent-warning)]">仅运行时删除</strong>
                    。这是 git 追踪的静态配置项，后端不会修改源文件。
                  </span>
                  <span className="block text-[var(--text-muted)]">
                    下次后端重启时会从{' '}
                    <code className="font-mono">config/datasources.yaml</code>{' '}
                    重新加载。如需永久移除请直接编辑该文件。
                  </span>
                </p>
              )}
            </DialogDescription>
          </DialogHeader>
          {deleteTarget && (
            <div className="rounded-md border border-[var(--border-color)] bg-[var(--bg-code)] px-3 py-2 font-mono text-xs">
              <div>
                <span className="text-[var(--text-muted)]">name:</span>{' '}
                <span className="text-[var(--text-primary)]">{deleteTarget.name}</span>
              </div>
              <div>
                <span className="text-[var(--text-muted)]">dialect:</span>{' '}
                <span className="text-[var(--text-primary)]">{deleteTarget.dialect}</span>
              </div>
              <div>
                <span className="text-[var(--text-muted)]">source:</span>{' '}
                <span className="text-[var(--text-primary)]">
                  {deleteTarget.user_managed ? 'datasources.local.yaml' : 'config/datasources.yaml (primary)'}
                </span>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteTarget(null)}
              disabled={deleting}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleting}
            >
              {deleting ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  删除中…
                </>
              ) : (
                <>
                  <Trash2 className="h-3 w-3" />
                  {deleteTarget?.user_managed ? '确认永久删除' : '确认移除（运行时）'}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
