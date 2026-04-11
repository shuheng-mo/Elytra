import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Key, Lock, Download } from 'lucide-react';
import { api } from '../../lib/api';
import { DataSourcePicker } from '../query/DataSourcePicker';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Skeleton } from '../ui/skeleton';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '../ui/collapsible';
import { cn } from '../../lib/utils';
import { exportSchemaToXlsx, exportSchemaToCsv } from '../../lib/export';

const LAYER_COLORS = {
  ODS: 'bg-[var(--accent-warning)]/15 text-[var(--accent-warning)]',
  DWD: 'bg-[var(--accent-primary)]/15 text-[var(--accent-primary)]',
  DWS: 'bg-[var(--accent-success)]/15 text-[var(--accent-success)]',
};

function ColumnRow({ col }) {
  return (
    <tr className="border-b border-[var(--border-subtle)] last:border-0">
      <td className="px-3 py-2 font-mono text-xs text-[var(--text-primary)]">
        <div className="flex items-center gap-1.5">
          {col.is_primary_key && (
            <Key className="h-3 w-3 text-[var(--accent-warning)]" title="主键" />
          )}
          {col.restricted && (
            <Lock className="h-3 w-3 text-[var(--accent-error)]" title="当前角色无权访问" />
          )}
          <span>{col.name}</span>
        </div>
      </td>
      <td className="px-3 py-2 font-mono text-xs text-[var(--text-secondary)]">{col.type}</td>
      <td className="px-3 py-2 text-xs text-[var(--text-primary)]">{col.chinese_name || '—'}</td>
      <td className="px-3 py-2 text-xs text-[var(--text-secondary)]">
        {col.description || '—'}
        {col.enum_values && col.enum_values.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {col.enum_values.slice(0, 6).map((v) => (
              <Badge key={v} variant="secondary" className="text-[10px]">
                {v}
              </Badge>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

function TableCard({ table }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="rounded-md border border-[var(--border-color)] bg-[var(--bg-secondary)]">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-[var(--bg-tertiary)]/30"
          >
            <div className="flex items-center gap-2">
              {open ? (
                <ChevronDown className="h-4 w-4 text-[var(--text-muted)]" />
              ) : (
                <ChevronRight className="h-4 w-4 text-[var(--text-muted)]" />
              )}
              <span className="font-mono text-sm font-medium text-[var(--text-primary)]">
                {table.table}
              </span>
              {table.chinese_name && (
                <span className="text-xs text-[var(--text-secondary)]">
                  ({table.chinese_name})
                </span>
              )}
            </div>
            <span className="text-xs text-[var(--text-muted)]">
              {table.columns?.length ?? 0} 字段
            </span>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="border-t border-[var(--border-subtle)] px-4 py-3">
            {table.description && (
              <p className="mb-2 text-xs text-[var(--text-secondary)]">{table.description}</p>
            )}
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-[var(--border-color)]">
                  <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                    字段名
                  </th>
                  <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                    类型
                  </th>
                  <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                    中文名
                  </th>
                  <th className="px-3 py-2 text-left text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                    说明
                  </th>
                </tr>
              </thead>
              <tbody>
                {table.columns?.map((col) => (
                  <ColumnRow key={col.name} col={col} />
                ))}
              </tbody>
            </table>
            {table.common_queries && table.common_queries.length > 0 && (
              <div className="mt-3 border-t border-[var(--border-subtle)] pt-3">
                <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--text-secondary)]">
                  常见查询
                </div>
                <ul className="list-disc pl-4 text-xs text-[var(--text-secondary)]">
                  {table.common_queries.map((q, i) => (
                    <li key={i}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

export function SchemaExplorer({ currentSource, setCurrentSource }) {
  const [schema, setSchema] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!currentSource) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getSchema(currentSource)
      .then((data) => {
        if (!cancelled) setSchema(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentSource]);

  const canExport = schema?.layers && !loading && !error;

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Schema Explorer</h1>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!canExport}
            onClick={() => exportSchemaToXlsx(schema, currentSource || 'default')}
            title={canExport ? '导出当前 Schema 到 Excel' : '等待 Schema 加载'}
          >
            <Download className="h-3.5 w-3.5" /> 导出 Excel
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!canExport}
            onClick={() => exportSchemaToCsv(schema, currentSource || 'default')}
            title={canExport ? '导出当前 Schema 到 CSV' : '等待 Schema 加载'}
          >
            <Download className="h-3.5 w-3.5" /> 导出 CSV
          </Button>
          <DataSourcePicker value={currentSource} onChange={setCurrentSource} />
        </div>
      </div>

      {loading && (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      )}

      {error && (
        <div className="rounded-md border border-[var(--accent-error)]/40 bg-[var(--accent-error)]/10 px-4 py-3 text-sm text-[var(--accent-error)]">
          {error}
        </div>
      )}

      {schema?.layers && (
        <div className="space-y-6">
          {Object.entries(schema.layers).map(([layer, tables]) => (
            <Card key={layer}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <span
                    className={cn(
                      'inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold uppercase',
                      LAYER_COLORS[layer] || 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)]'
                    )}
                  >
                    {layer}
                  </span>
                  <span className="text-sm font-normal text-[var(--text-secondary)]">
                    {tables.length} 张表
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {tables.map((t) => (
                  <TableCard key={t.table} table={t} />
                ))}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
